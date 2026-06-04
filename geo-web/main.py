import os
import re
import uuid
from datetime import datetime, timezone
from io import BytesIO

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse


app = FastAPI(title="GEO Web - No Login", version="2.6.0-output-only-ui")


GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "martech-497412")
REGION = os.environ.get("REGION", "asia-south2")
GEO_BUCKET = os.environ.get("GEO_BUCKET", "geo-accelator")
PROCESSOR_JOB_NAME = os.environ.get("PROCESSOR_JOB_NAME", "martech-geo-llm")

SUPPORTED_APIS = ["openai", "anthropic", "perplexity", "google"]


def make_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return f"run_{timestamp}_{suffix}"


def make_user_id_from_brand_name(brand_name: str) -> str:
    """
    Creates user_id automatically from Brand Name.

    Rules:
    - lower case
    - remove spaces
    - remove special characters
    - add timestamp and short UUID suffix for uniqueness
    """
    base = (brand_name or "").strip().lower()
    base = re.sub(r"\s+", "", base)
    base = re.sub(r"[^a-z0-9]", "", base)

    if not base:
        base = "brand"

    base = base[:60]

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:6]

    return f"{base}_{timestamp}_{suffix}"


def safe_id(value: str, default: str = "brand") -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", (value or default).strip())


def clean_lines(value: str) -> list[str]:
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
    return "\n".join([f"- {item}" for item in items]) if items else "- Not provided"


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
            detail=f"{field_name} is required and must be at least {min_len} characters.",
        )

    if len(value) > max_len:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must not exceed {max_len} characters.",
        )

    return value


def normalize_selected_apis(selected_apis: list[str] | None) -> str:
    """
    If no checkbox is selected, returns blank string.
    Blank means run all available configured APIs.
    """
    if not selected_apis:
        return ""

    cleaned = []
    for api in selected_apis:
        api = api.strip().lower()
        if api in SUPPORTED_APIS and api not in cleaned:
            cleaned.append(api)

    return ",".join(cleaned)


def build_brand_context_md(data: dict) -> str:
    return f"""# Brand Context

## Brand Name
{data['brand_name']}

## Website URL
{data['website_url']}

## Industry
{data['industry']}

## Description
{data['description']}

## Tone
{data['tone']}

## Positioning Statement
{data['positioning_statement']}

## Products
{bullet_list(data['products'])}

## Target Markets
{bullet_list(data['target_markets'])}

## Known Personas
{bullet_list(data['known_personas'])}

## Competitor List
{bullet_list(data['competitor_list'])}

## C360 Column Guide
{data['c360_column_guide']}

## Aliases
{bullet_list(data['aliases'])}

## Regions
{bullet_list(data['regions'])}
"""


def upload_text_to_gcs(
    bucket_name: str,
    object_name: str,
    content: str,
    content_type: str = "text/markdown",
) -> str:
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_string(content, content_type=content_type)

    return f"gs://{bucket_name}/{object_name}"


def download_gcs_file_as_bytes(bucket_name: str, object_name: str) -> bytes:
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)

    if not blob.exists():
        raise HTTPException(
            status_code=404,
            detail="File is not ready yet. Please try again after the job completes.",
        )

    return blob.download_as_bytes()


def trigger_processor_job(
    user_id: str,
    run_id: str,
    selected_apis_csv: str,
) -> str:
    from google.cloud.run_v2 import JobsClient, RunJobRequest

    input_prefix = f"{user_id}/{run_id}/Input"
    output_prefix = f"{user_id}/{run_id}/Output"
    working_prefix = f"{user_id}/{run_id}/Working"
    artefacts_prefix = f"{user_id}/{run_id}/Artefacts"

    client = JobsClient()

    job_name = (
        f"projects/{GCP_PROJECT_ID}/locations/{REGION}/jobs/{PROCESSOR_JOB_NAME}"
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
</style>
"""


def form_html() -> str:
    return f"""
<!DOCTYPE html>
<html>
<head>
  <title>GEO Brand Context Intake</title>
  {page_css()}
</head>
<body>
  <h1>GEO Brand Context Intake</h1>
  <p class="subtitle">
    Data will be stored under:
    <code>gs://{GEO_BUCKET}/&lt;generated_user_id&gt;/&lt;run_id&gt;/</code>
  </p>
  <p class="hint">
    generated_user_id is created automatically from Brand Name. Spaces are removed and a unique suffix is added.
  </p>

  <div class="card">
    <form method="post" action="/brand-context">
      <label>Brand Name *</label>
      <input name="brand_name" required value="ABC Mobility Components">

      <label>LLM APIs to run</label>
      <div class="hint">Select one or more. If nothing is selected, all configured APIs will run.</div>
      <div class="checkbox-row">
        <label class="checkbox-item"><input type="checkbox" name="selected_apis" value="openai"> OpenAI</label>
        <label class="checkbox-item"><input type="checkbox" name="selected_apis" value="anthropic"> Anthropic</label>
        <label class="checkbox-item"><input type="checkbox" name="selected_apis" value="perplexity"> Perplexity</label>
        <label class="checkbox-item"><input type="checkbox" name="selected_apis" value="google"> Google</label>
      </div>

      <label>Website URL *</label>
      <input name="website_url" required value="https://www.abcmobilitycomponents.com">

      <label>Industry *</label>
      <input name="industry" required value="Automotive Components Manufacturing">

      <label>Description *</label>
      <textarea name="description" required>ABC Mobility Components is an automotive component manufacturer focused on body-in-white assemblies, sheet metal stampings, welded sub-assemblies, chassis structures, and EV-related structural components for passenger vehicle OEMs and Tier-1 automotive customers.</textarea>

      <label>Tone *</label>
      <input name="tone" required value="Professional, credible, practical, and technical">

      <label>Positioning Statement *</label>
      <textarea name="positioning_statement" required>ABC Mobility Components is positioned as a reliable automotive manufacturing partner for structural components, BIW assemblies, and EV-ready systems.</textarea>

      <label>Products *</label>
      <textarea name="products" required>Body-in-white assemblies
Sheet metal stampings
Welded sub-assemblies
Chassis structures
EV structural components</textarea>

      <label>Target Markets *</label>
      <textarea name="target_markets" required>India
United States
Europe
Japan
ASEAN
Middle East</textarea>

      <label>Known Personas *</label>
      <textarea name="known_personas" required>OEM sourcing leader
Automotive program manager
EV platform engineering team
Tier-1 procurement team</textarea>

      <label>Competitor List *</label>
      <textarea name="competitor_list" required>JBM Group
Autocomp Corporation
Gestamp India
Magna India
Bharat Forge auto components division</textarea>

      <label>C360 Column Guide *</label>
      <textarea name="c360_column_guide" required>customer_name: customer or account name
segment: customer segment
region: customer geography
product_interest: product or service interest
engagement_score: engagement score</textarea>

      <label>Aliases *</label>
      <textarea name="aliases" required>ABC Mobility Components
ABC Mobility
ABC Components
ABC Auto Components
ABCMC</textarea>

      <label>Regions *</label>
      <textarea name="regions" required>India
IN
United States
US
U.S.
USA
Europe
EU
Japan
ASEAN
Middle East
UAE</textarea>

      <button type="submit">Create brand_context.md and Run Pipeline</button>
    </form>
  </div>
</body>
</html>
"""


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "geo-web-complete",
        "project": GCP_PROJECT_ID,
        "region": REGION,
        "geo_bucket": GEO_BUCKET,
        "processor_job": PROCESSOR_JOB_NAME,
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
    selected_apis: list[str] = Form(default=[]),
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

    input_prefix = f"{generated_user_id}/{run_id}/Input"
    output_prefix = f"{generated_user_id}/{run_id}/Output"
    working_prefix = f"{generated_user_id}/{run_id}/Working"

    input_file = f"{input_prefix}/brand_context.md"

    input_gcs_uri = upload_text_to_gcs(
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

    return HTMLResponse(
        f"""
<!DOCTYPE html>
<html>
<head>{page_css()}</head>
<body>
  <h1>GEO Processing Started</h1>
  <div class="card">
    <p><b>Brand Name:</b> <code>{brand_name}</code></p>
    <p><b>Generated User ID:</b> <code>{generated_user_id}</code></p>
    <p><b>Run ID:</b> <code>{run_id}</code></p>
    <p><b>Selected APIs:</b> <code>{selected_label}</code></p>

    <p><b>Input:</b><br><code>{input_gcs_uri}</code></p>
    <p><b>Output folder:</b><br><code>gs://{GEO_BUCKET}/{output_prefix}/</code></p>
    <p><b>Working/log folder:</b><br><code>gs://{GEO_BUCKET}/{working_prefix}/</code></p>
    <p><b>Cloud Run Job operation:</b><br><code>{operation_name}</code></p>

    <a class="button" href="/download/{generated_user_id}/{run_id}/output.json">Download output.json</a>

    <br><br>
    <a href="/">Create another run</a>
  </div>
</body>
</html>
"""
    )


@app.get("/download/{user_id}/{run_id}/output.json")
def download_output(user_id: str, run_id: str):
    object_name = f"{safe_id(user_id)}/{safe_id(run_id, 'run_001')}/Output/output.json"

    return StreamingResponse(
        BytesIO(download_gcs_file_as_bytes(GEO_BUCKET, object_name)),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{run_id}_output.json"'
        },
    )

