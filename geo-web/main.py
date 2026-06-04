
import os
import re
import time
import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import List, Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse


app = FastAPI(title="GEO Web - No Login", version="3.0.0-run-before-download")


GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "martech-497412")
REGION = os.environ.get("REGION", "asia-south2")
GEO_BUCKET = os.environ.get("GEO_BUCKET", "geo-accelator")
PROCESSOR_JOB_NAME = os.environ.get("PROCESSOR_JOB_NAME", "martech-geo-v3-job")

# The UI will wait for output.json before showing the download button.
# Set Cloud Run service timeout higher than this value.
JOB_WAIT_TIMEOUT_SECONDS = int(os.environ.get("JOB_WAIT_TIMEOUT_SECONDS", "840"))
JOB_POLL_SECONDS = int(os.environ.get("JOB_POLL_SECONDS", "5"))

SUPPORTED_APIS = ["openai", "anthropic", "perplexity", "google"]


def make_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return "run_{}_{}".format(timestamp, suffix)


def make_user_id_from_brand_name(brand_name: str) -> str:
    base = (brand_name or "").strip().lower()
    base = re.sub(r"\s+", "", base)
    base = re.sub(r"[^a-z0-9]", "", base)

    if not base:
        base = "brand"

    base = base[:60]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:6]

    return "{}_{}_{}".format(base, timestamp, suffix)


def safe_id(value: str, default: str = "brand") -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", (value or default).strip())


def clean_lines(value: str) -> List[str]:
    if not value:
        return []

    items = []

    for line in value.splitlines():
        line = line.strip()
        if not line:
            continue

        if "," in line:
            items.extend([item.strip() for item in line.split(",") if item.strip()])
        else:
            items.append(line)

    return items


def bullet_list(value: str) -> str:
    items = clean_lines(value)
    return "\n".join(["- {}".format(item) for item in items]) if items else "- Not provided"


def validate_required_text(
    field_name: str,
    value: str,
    min_len: int = 2,
    max_len: int = 5000,
) -> str:
    value = (value or "").strip()

    if len(value) < min_len:
        raise HTTPException(
            status_code=400,
            detail="{} is required and must be at least {} characters.".format(field_name, min_len),
        )

    if len(value) > max_len:
        raise HTTPException(
            status_code=400,
            detail="{} must not exceed {} characters.".format(field_name, max_len),
        )

    return value


def normalize_selected_apis(selected_apis: Optional[List[str]]) -> str:
    if not selected_apis:
        return ""

    cleaned = []
    for api in selected_apis:
        api = api.strip().lower()
        if api in SUPPORTED_APIS and api not in cleaned:
            cleaned.append(api)

    return ",".join(cleaned)


def build_brand_context_md(data: dict) -> str:
    return """# Brand Context

## Brand Name
{brand_name}

## Website URL
{website_url}

## Industry
{industry}

## Description
{description}

## Tone
{tone}

## Positioning Statement
{positioning_statement}

## Products
{products}

## Target Markets
{target_markets}

## Known Personas
{known_personas}

## Competitor List
{competitor_list}

## C360 Column Guide
{c360_column_guide}

## Aliases
{aliases}

## Regions
{regions}
""".format(
        brand_name=data["brand_name"],
        website_url=data["website_url"],
        industry=data["industry"],
        description=data["description"],
        tone=data["tone"],
        positioning_statement=data["positioning_statement"],
        products=bullet_list(data["products"]),
        target_markets=bullet_list(data["target_markets"]),
        known_personas=bullet_list(data["known_personas"]),
        competitor_list=bullet_list(data["competitor_list"]),
        c360_column_guide=data["c360_column_guide"],
        aliases=bullet_list(data["aliases"]),
        regions=bullet_list(data["regions"]),
    )


def get_storage_client():
    from google.cloud import storage

    return storage.Client()


def upload_text_to_gcs(
    bucket_name: str,
    object_name: str,
    content: str,
    content_type: str = "text/markdown",
) -> str:
    client = get_storage_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_string(content, content_type=content_type)

    return "gs://{}/{}".format(bucket_name, object_name)


def gcs_file_exists(bucket_name: str, object_name: str) -> bool:
    client = get_storage_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    return blob.exists()


def wait_for_gcs_file(bucket_name: str, object_name: str, timeout_seconds: int, poll_seconds: int) -> bool:
    start = time.time()

    while time.time() - start < timeout_seconds:
        if gcs_file_exists(bucket_name, object_name):
            return True
        time.sleep(poll_seconds)

    return False


def download_gcs_file_as_bytes(bucket_name: str, object_name: str) -> bytes:
    client = get_storage_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)

    if not blob.exists():
        raise HTTPException(
            status_code=404,
            detail="output.json is not ready yet. Please wait for the Cloud Run Job to complete and then try again.",
        )

    return blob.download_as_bytes()


def trigger_processor_job(
    user_id: str,
    run_id: str,
    selected_apis_csv: str,
) -> str:
    from google.cloud.run_v2 import JobsClient, RunJobRequest

    input_prefix = "{}/{}/Input".format(user_id, run_id)
    output_prefix = "{}/{}/Output".format(user_id, run_id)
    working_prefix = "{}/{}/Working".format(user_id, run_id)
    artefacts_prefix = "{}/{}/Artefacts".format(user_id, run_id)

    client = JobsClient()
    job_name = "projects/{}/locations/{}/jobs/{}".format(
        GCP_PROJECT_ID,
        REGION,
        PROCESSOR_JOB_NAME,
    )

    request = RunJobRequest(
        name=job_name,
        overrides={
            "container_overrides": [
                {
                    "env": [
                        {"name": "GEO_BUCKET", "value": GEO_BUCKET},
                        {"name": "USER_ID", "value": user_id},
                        {"name": "RUN_ID", "value": run_id},
                        {"name": "INPUT_PREFIX", "value": input_prefix},
                        {"name": "OUTPUT_PREFIX", "value": output_prefix},
                        {"name": "WORKING_PREFIX", "value": working_prefix},
                        {"name": "ARTEFACTS_PREFIX", "value": artefacts_prefix},
                        {"name": "SELECTED_APIS", "value": selected_apis_csv},
                    ]
                }
            ]
        },
    )

    operation = client.run_job(request=request)
    return operation.operation.name


def page_css() -> str:
    return """
<style>
body { font-family: Arial, sans-serif; max-width: 950px; margin: 40px auto; line-height: 1.5; color: #222; }
h1 { margin-bottom: 6px; }
.subtitle { color: #666; margin-bottom: 24px; }
.card { border:1px solid #ddd; border-radius:12px; padding:24px; margin-bottom:20px; }
label { font-weight:bold; display:block; margin-top:16px; }
input, textarea { width:100%; padding:10px; margin-top:6px; border:1px solid #ccc; border-radius:8px; font-size:14px; box-sizing:border-box; }
textarea { min-height:90px; }
button, .button { margin-top:24px; padding:12px 18px; border:0; border-radius:8px; background:#111827; color:white; text-decoration:none; display:inline-block; }
code { background:#f3f4f6; padding:2px 4px; border-radius:4px; }
.hint { color:#666; font-size:12px; }
.checkbox-row { display:flex; gap:18px; flex-wrap:wrap; margin-top:8px; }
.checkbox-item { display:flex; align-items:center; gap:6px; border:1px solid #ddd; padding:8px 10px; border-radius:8px; }
.checkbox-item input { width:auto; margin:0; }
.success { background:#ecfdf5; border:1px solid #a7f3d0; padding:12px; border-radius:8px; }
.error { background:#fef2f2; border:1px solid #fecaca; padding:12px; border-radius:8px; }
</style>
"""


def form_html() -> str:
    return """
<!DOCTYPE html>
<html>
<head>
  <title>GEO Brand Context Intake</title>
  {css}
</head>
<body>
  <h1>GEO Brand Context Intake</h1>
  <p class="subtitle">
    Data will be stored under:
    <code>gs://{bucket}/&lt;generated_user_id&gt;/&lt;run_id&gt;/</code>
  </p>

  <div class="card">
    <form method="post" action="/brand-context">
      <label>Brand Name *</label>
      <input name="brand_name" required value="Adidas">

      <label>LLM APIs to run</label>
      <div class="hint">Select one or more. If nothing is selected, all configured APIs will run.</div>
      <div class="checkbox-row">
        <label class="checkbox-item"><input type="checkbox" name="selected_apis" value="openai"> OpenAI</label>
        <label class="checkbox-item"><input type="checkbox" name="selected_apis" value="anthropic"> Anthropic</label>
        <label class="checkbox-item"><input type="checkbox" name="selected_apis" value="perplexity"> Perplexity</label>
        <label class="checkbox-item"><input type="checkbox" name="selected_apis" value="google"> Google</label>
      </div>

      <label>Website URL *</label>
      <input name="website_url" required value="https://www.adidas.com">

      <label>Industry *</label>
      <input name="industry" required value="Sportswear, Footwear, Apparel and Lifestyle Retail">

      <label>Description *</label>
      <textarea name="description" required>Adidas is a global sportswear and lifestyle brand known for athletic footwear, performance apparel, sports equipment, and fashion-led lifestyle products across running, football, training, originals, and athleisure categories.</textarea>

      <label>Tone *</label>
      <input name="tone" required value="Energetic, aspirational, performance-led, youthful, inclusive, and lifestyle-oriented">

      <label>Positioning Statement *</label>
      <textarea name="positioning_statement" required>Adidas is positioned as a global performance and lifestyle sportswear brand that blends innovation, athlete credibility, street culture, sustainability, and everyday comfort.</textarea>

      <label>Products *</label>
      <textarea name="products" required>Performance running shoes
Football boots and teamwear
Training apparel
Lifestyle sneakers
Adidas Originals
Athleisure wear
Sports accessories
Sustainable product lines</textarea>

      <label>Target Markets *</label>
      <textarea name="target_markets" required>Global
North America
Europe
India
China
Japan
Southeast Asia
Middle East
Latin America</textarea>

      <label>Known Personas *</label>
      <textarea name="known_personas" required>Athletes and sports professionals
Fitness and training enthusiasts
Sneaker and streetwear consumers
Football fans and players
Running community
Gen Z lifestyle shoppers
Sustainability-conscious consumers
E-commerce shoppers</textarea>

      <label>Competitor List *</label>
      <textarea name="competitor_list" required>Nike
Puma
New Balance
Under Armour
ASICS
Reebok
Skechers
Lululemon</textarea>

      <label>C360 Column Guide *</label>
      <textarea name="c360_column_guide" required>customer_id: unique customer identifier
customer_name: customer or account name
segment: customer segment such as athlete, lifestyle shopper, sneaker buyer, or fitness enthusiast
region: customer geography
preferred_sport: sport or activity preference
product_interest: footwear, apparel, accessories, football, running, training, originals, or lifestyle
purchase_channel: online store, retail store, marketplace, or app
engagement_score: engagement score
loyalty_status: loyalty or membership tier</textarea>

      <label>Aliases *</label>
      <textarea name="aliases" required>Adidas
adidas
Adidas Originals
Adidas Performance
Three Stripes
Adi
adidas India
adidas Global</textarea>

      <label>Regions *</label>
      <textarea name="regions" required>Global
India
IN
United States
US
U.S.
USA
Europe
EU
Germany
China
Japan
ASEAN
Middle East
UAE
Latin America</textarea>

      <button type="submit">Run Pipeline</button>
    </form>
  </div>
</body>
</html>
""".format(css=page_css(), bucket=GEO_BUCKET)


def download_ready_html(
    brand_name: str,
    generated_user_id: str,
    run_id: str,
    selected_label: str,
    output_prefix: str,
) -> str:
    return """
<!DOCTYPE html>
<html>
<head>{css}</head>
<body>
  <h1>GEO Output Ready</h1>
  <div class="card">
    <div class="success">Pipeline completed and output.json is ready.</div>

    <p><b>Brand Name:</b> <code>{brand_name}</code></p>
    <p><b>Generated User ID:</b> <code>{generated_user_id}</code></p>
    <p><b>Run ID:</b> <code>{run_id}</code></p>
    <p><b>Selected APIs:</b> <code>{selected_label}</code></p>
    <p><b>Output folder:</b><br><code>gs://{bucket}/{output_prefix}/</code></p>

    <a class="button" href="/download/{generated_user_id}/{run_id}/output.json">Download output.json</a>

    <br><br>
    <a href="/">Create another run</a>
  </div>
</body>
</html>
""".format(
        css=page_css(),
        brand_name=brand_name,
        generated_user_id=generated_user_id,
        run_id=run_id,
        selected_label=selected_label,
        bucket=GEO_BUCKET,
        output_prefix=output_prefix,
    )


def output_not_ready_html(
    generated_user_id: str,
    run_id: str,
    output_prefix: str,
    operation_name: str,
) -> str:
    return """
<!DOCTYPE html>
<html>
<head>{css}</head>
<body>
  <h1>Output Not Ready</h1>
  <div class="card">
    <div class="error">
      The pipeline did not create output.json within the configured wait time.
      No download option is shown because output.json is not ready.
    </div>

    <p><b>Generated User ID:</b> <code>{generated_user_id}</code></p>
    <p><b>Run ID:</b> <code>{run_id}</code></p>
    <p><b>Expected output folder:</b><br><code>gs://{bucket}/{output_prefix}/</code></p>
    <p><b>Cloud Run Job operation:</b><br><code>{operation_name}</code></p>

    <br>
    <a href="/">Create another run</a>
  </div>
</body>
</html>
""".format(
        css=page_css(),
        generated_user_id=generated_user_id,
        run_id=run_id,
        bucket=GEO_BUCKET,
        output_prefix=output_prefix,
        operation_name=operation_name,
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "geo-web-run-before-download",
        "project": GCP_PROJECT_ID,
        "region": REGION,
        "geo_bucket": GEO_BUCKET,
        "processor_job": PROCESSOR_JOB_NAME,
        "job_wait_timeout_seconds": JOB_WAIT_TIMEOUT_SECONDS,
        "ui_downloads": ["Output/output.json"],
    }


@app.get("/", response_class=HTMLResponse)
def form_page(request: Request):
    return HTMLResponse(form_html())


@app.post("/brand-context")
def create_brand_context(
    brand_name: str = Form(...),
    website_url: str = Form(...),
    industry: str = Form(...),
    description: str = Form(...),
    tone: str = Form(...),
    positioning_statement: str = Form(...),
    products: str = Form(...),
    target_markets: str = Form(...),
    known_personas: str = Form(...),
    competitor_list: str = Form(...),
    c360_column_guide: str = Form(...),
    aliases: str = Form(...),
    regions: str = Form(...),
    selected_apis: Optional[List[str]] = Form(None),
):
    brand_name = validate_required_text("Brand Name", brand_name)

    generated_user_id = make_user_id_from_brand_name(brand_name)
    run_id = make_run_id()

    selected_apis_csv = normalize_selected_apis(selected_apis)
    selected_label = selected_apis_csv if selected_apis_csv else "ALL CONFIGURED APIS"

    data = {
        "brand_name": brand_name,
        "website_url": validate_required_text("Website URL", website_url, min_len=5),
        "industry": validate_required_text("Industry", industry),
        "description": validate_required_text("Description", description, min_len=20),
        "tone": validate_required_text("Tone", tone),
        "positioning_statement": validate_required_text("Positioning Statement", positioning_statement, min_len=10),
        "products": validate_required_text("Products", products),
        "target_markets": validate_required_text("Target Markets", target_markets),
        "known_personas": validate_required_text("Known Personas", known_personas),
        "competitor_list": validate_required_text("Competitor List", competitor_list),
        "c360_column_guide": validate_required_text("C360 Column Guide", c360_column_guide, min_len=10),
        "aliases": validate_required_text("Aliases", aliases),
        "regions": validate_required_text("Regions", regions),
    }

    input_prefix = "{}/{}/Input".format(generated_user_id, run_id)
    output_prefix = "{}/{}/Output".format(generated_user_id, run_id)

    input_file = "{}/brand_context.md".format(input_prefix)
    output_file = "{}/output.json".format(output_prefix)

    upload_text_to_gcs(
        bucket_name=GEO_BUCKET,
        object_name=input_file,
        content=build_brand_context_md(data),
        content_type="text/markdown",
    )

    operation_name = trigger_processor_job(
        user_id=generated_user_id,
        run_id=run_id,
        selected_apis_csv=selected_apis_csv,
    )

    output_ready = wait_for_gcs_file(
        bucket_name=GEO_BUCKET,
        object_name=output_file,
        timeout_seconds=JOB_WAIT_TIMEOUT_SECONDS,
        poll_seconds=JOB_POLL_SECONDS,
    )

    if output_ready:
        return HTMLResponse(
            download_ready_html(
                brand_name=brand_name,
                generated_user_id=generated_user_id,
                run_id=run_id,
                selected_label=selected_label,
                output_prefix=output_prefix,
            )
        )

    return HTMLResponse(
        output_not_ready_html(
            generated_user_id=generated_user_id,
            run_id=run_id,
            output_prefix=output_prefix,
            operation_name=operation_name,
        ),
        status_code=202,
    )


@app.get("/download/{user_id}/{run_id}/output.json")
def download_output(user_id: str, run_id: str):
    safe_user_id = safe_id(user_id)
    safe_run_id = safe_id(run_id, "run_001")

    object_name = "{}/{}/Output/output.json".format(safe_user_id, safe_run_id)

    return StreamingResponse(
        BytesIO(download_gcs_file_as_bytes(GEO_BUCKET, object_name)),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="{}_output.json"'.format(safe_run_id)
        },
    )
