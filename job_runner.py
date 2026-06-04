import json
import logging
import os
import subprocess
import traceback
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import storage


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("geo-pipeline-job")


# --------------------------------------------------
# Runtime configuration
# --------------------------------------------------

GEO_BUCKET = os.environ.get("GEO_BUCKET", "geo-accelator")

USER_ID = os.environ.get("USER_ID", "brand_user_001")
RUN_ID = os.environ.get("RUN_ID", "run_001")

INPUT_PREFIX = os.environ.get("INPUT_PREFIX", f"{USER_ID}/{RUN_ID}/Input")
OUTPUT_PREFIX = os.environ.get("OUTPUT_PREFIX", f"{USER_ID}/{RUN_ID}/Output")
WORKING_PREFIX = os.environ.get("WORKING_PREFIX", f"{USER_ID}/{RUN_ID}/Working")
ARTEFACTS_PREFIX = os.environ.get("ARTEFACTS_PREFIX", f"{USER_ID}/{RUN_ID}/Artefacts")

AGENT1_EXECUTOR_PATH = os.environ.get("AGENT1_EXECUTOR_PATH", "Agent/Agent1/executor.py")

# Selected API logic from geo-web.
# Blank/missing means all supported APIs.
SELECTED_APIS_RAW = os.environ.get("SELECTED_APIS", "").strip()

SUPPORTED_APIS = ["openai", "anthropic", "perplexity", "google"]

# API keys injected from Secret Manager as env vars.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

# Optional model env vars.
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
PERPLEXITY_MODEL = os.environ.get("PERPLEXITY_MODEL", "sonar-pro")
GOOGLE_MODEL = os.environ.get("GOOGLE_MODEL", "gemini-1.5-pro")

LOCAL_INPUT_DIR = Path("Input")
LOCAL_OUTPUT_DIR = Path("Output")
LOCAL_WORKING_DIR = Path("Working")
LOCAL_ARTEFACTS_DIR = Path("Artefacts")

LOG_EVENTS: list[dict] = []


# --------------------------------------------------
# Helper functions
# --------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_selected_apis() -> list[str]:
    """
    If SELECTED_APIS is blank, run all supported APIs.
    """
    if not SELECTED_APIS_RAW:
        return SUPPORTED_APIS.copy()

    selected = []
    for item in SELECTED_APIS_RAW.split(","):
        api = item.strip().lower()
        if api and api in SUPPORTED_APIS and api not in selected:
            selected.append(api)

    return selected if selected else SUPPORTED_APIS.copy()


SELECTED_APIS = parse_selected_apis()


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def get_llm_key_status() -> dict:
    return {
        "openai": {
            "env_var": "OPENAI_API_KEY",
            "available": bool(OPENAI_API_KEY),
            "selected": "openai" in SELECTED_APIS,
            "masked": mask_secret(OPENAI_API_KEY),
            "model": OPENAI_MODEL,
        },
        "anthropic": {
            "env_var": "ANTHROPIC_API_KEY",
            "available": bool(ANTHROPIC_API_KEY),
            "selected": "anthropic" in SELECTED_APIS,
            "masked": mask_secret(ANTHROPIC_API_KEY),
            "model": ANTHROPIC_MODEL,
        },
        "perplexity": {
            "env_var": "PERPLEXITY_API_KEY",
            "available": bool(PERPLEXITY_API_KEY),
            "selected": "perplexity" in SELECTED_APIS,
            "masked": mask_secret(PERPLEXITY_API_KEY),
            "model": PERPLEXITY_MODEL,
        },
        "google": {
            "env_var": "GOOGLE_API_KEY",
            "available": bool(GOOGLE_API_KEY),
            "selected": "google" in SELECTED_APIS,
            "masked": mask_secret(GOOGLE_API_KEY),
            "model": GOOGLE_MODEL,
        },
    }


def validate_selected_llm_keys() -> list[str]:
    status = get_llm_key_status()

    selected_available = [
        api for api in SELECTED_APIS
        if status.get(api, {}).get("available")
    ]

    if not selected_available:
        raise RuntimeError(
            "No API key found for selected APIs. "
            f"selected_apis={SELECTED_APIS}. "
            "Configure corresponding secrets or leave selection blank to run all available APIs."
        )

    return selected_available


def log_event(
    event_name: str,
    status: str = "INFO",
    stage: str = "general",
    details: dict | None = None,
) -> None:
    event = {
        "timestamp": utc_now(),
        "service": "geo-pipeline-job",
        "user_id": USER_ID,
        "run_id": RUN_ID,
        "stage": stage,
        "event_name": event_name,
        "status": status,
        "details": details or {},
    }

    LOG_EVENTS.append(event)

    msg = json.dumps(event, ensure_ascii=False)
    if status == "ERROR":
        logger.error(msg)
    elif status == "WARNING":
        logger.warning(msg)
    else:
        logger.info(msg)


def get_storage_client() -> storage.Client:
    return storage.Client()


def download_gcs_prefix(bucket_name: str, prefix: str, local_dir: Path) -> None:
    bucket = get_storage_client().bucket(bucket_name)
    local_dir.mkdir(parents=True, exist_ok=True)

    blobs = list(bucket.list_blobs(prefix=prefix))
    if not blobs:
        raise FileNotFoundError(f"No files found at gs://{bucket_name}/{prefix}")

    count = 0

    for blob in blobs:
        if blob.name.endswith("/"):
            continue

        relative_path = blob.name.replace(prefix, "", 1).lstrip("/")
        if not relative_path:
            continue

        local_path = local_dir / relative_path
        local_path.parent.mkdir(parents=True, exist_ok=True)

        log_event(
            "INPUT_FILE_DOWNLOAD_STARTED",
            "INFO",
            "input",
            {
                "gcs_uri": f"gs://{bucket_name}/{blob.name}",
                "local_path": str(local_path),
            },
        )

        blob.download_to_filename(str(local_path))
        count += 1

    log_event(
        "INPUT_DOWNLOAD_COMPLETED",
        "SUCCESS",
        "input",
        {
            "bucket": bucket_name,
            "prefix": prefix,
            "file_count": count,
        },
    )


def upload_folder_to_gcs(local_dir: Path, bucket_name: str, prefix: str) -> int:
    bucket = get_storage_client().bucket(bucket_name)

    if not local_dir.exists():
        log_event(
            "UPLOAD_FOLDER_SKIPPED",
            "WARNING",
            "upload",
            {
                "local_dir": str(local_dir),
                "reason": "folder does not exist",
            },
        )
        return 0

    count = 0

    for file_path in local_dir.rglob("*"):
        if not file_path.is_file():
            continue

        relative_path = file_path.relative_to(local_dir)
        blob_name = f"{prefix}/{relative_path}".replace("\\", "/")

        log_event(
            "OUTPUT_FILE_UPLOAD_STARTED",
            "INFO",
            "upload",
            {
                "local_path": str(file_path),
                "gcs_uri": f"gs://{bucket_name}/{blob_name}",
            },
        )

        bucket.blob(blob_name).upload_from_filename(str(file_path))
        count += 1

    log_event(
        "UPLOAD_FOLDER_COMPLETED",
        "SUCCESS",
        "upload",
        {
            "bucket": bucket_name,
            "prefix": prefix,
            "file_count": count,
        },
    )

    return count


def write_run_log_locally() -> Path:
    LOCAL_WORKING_DIR.mkdir(parents=True, exist_ok=True)

    run_log_path = LOCAL_WORKING_DIR / "run_log.json"

    payload = {
        "user_id": USER_ID,
        "run_id": RUN_ID,
        "selected_apis_raw": SELECTED_APIS_RAW,
        "selected_apis_effective": SELECTED_APIS,
        "generated_at": utc_now(),
        "event_count": len(LOG_EVENTS),
        "events": LOG_EVENTS,
    }

    run_log_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return run_log_path


def write_llm_runtime_config() -> Path:
    LOCAL_WORKING_DIR.mkdir(parents=True, exist_ok=True)

    config_path = LOCAL_WORKING_DIR / "llm_runtime_config.json"

    payload = {
        "generated_at": utc_now(),
        "user_id": USER_ID,
        "run_id": RUN_ID,
        "selected_apis_raw": SELECTED_APIS_RAW,
        "selected_apis_effective": SELECTED_APIS,
        "llm_keys": get_llm_key_status(),
        "note": "Raw API keys are not written here. They remain only as environment variables.",
    }

    config_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return config_path


def ensure_local_dirs() -> None:
    for folder in [
        LOCAL_INPUT_DIR,
        LOCAL_OUTPUT_DIR,
        LOCAL_WORKING_DIR,
        LOCAL_ARTEFACTS_DIR,
    ]:
        folder.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------
# Main job execution
# --------------------------------------------------

def main() -> None:
    try:
        log_event(
            "RUN_STARTED",
            "SUCCESS",
            "initialization",
            {
                "geo_bucket": GEO_BUCKET,
                "user_id": USER_ID,
                "run_id": RUN_ID,
                "input_prefix": INPUT_PREFIX,
                "output_prefix": OUTPUT_PREFIX,
                "working_prefix": WORKING_PREFIX,
                "artefacts_prefix": ARTEFACTS_PREFIX,
                "agent1_executor_path": AGENT1_EXECUTOR_PATH,
                "selected_apis_raw": SELECTED_APIS_RAW,
                "selected_apis_effective": SELECTED_APIS,
            },
        )

        ensure_local_dirs()

        selected_available_keys = validate_selected_llm_keys()
        write_llm_runtime_config()

        log_event(
            "LLM_KEY_VALIDATION_COMPLETED",
            "SUCCESS",
            "llm_config",
            {
                "selected_apis": SELECTED_APIS,
                "available_selected_keys": selected_available_keys,
                "key_status": get_llm_key_status(),
            },
        )

        download_gcs_prefix(
            bucket_name=GEO_BUCKET,
            prefix=INPUT_PREFIX,
            local_dir=LOCAL_INPUT_DIR,
        )

        brand_context_path = LOCAL_INPUT_DIR / "brand_context.md"
        if not brand_context_path.exists():
            raise FileNotFoundError("Mandatory input missing: Input/brand_context.md")

        executor_path = Path(AGENT1_EXECUTOR_PATH)
        if not executor_path.exists():
            raise FileNotFoundError(
                f"Agent executor not found at {AGENT1_EXECUTOR_PATH}"
            )

        child_env = os.environ.copy()
        child_env["SELECTED_APIS"] = ",".join(SELECTED_APIS)

        log_event(
            "AGENT1_EXECUTOR_STARTED",
            "INFO",
            "agent1",
            {
                "command": f"python {AGENT1_EXECUTOR_PATH}",
                "selected_apis": SELECTED_APIS,
            },
        )

        result = subprocess.run(
            ["python", AGENT1_EXECUTOR_PATH],
            check=False,
            capture_output=True,
            text=True,
            env=child_env,
        )

        if result.stdout:
            log_event(
                "AGENT1_EXECUTOR_STDOUT",
                "INFO",
                "agent1",
                {"stdout_tail": result.stdout[-10000:]},
            )

        if result.stderr:
            log_event(
                "AGENT1_EXECUTOR_STDERR",
                "WARNING",
                "agent1",
                {"stderr_tail": result.stderr[-10000:]},
            )

        if result.returncode != 0:
            raise RuntimeError(
                f"Agent1 executor failed with exit code {result.returncode}"
            )

        log_event("AGENT1_EXECUTOR_COMPLETED", "SUCCESS", "agent1")

        upload_folder_to_gcs(LOCAL_OUTPUT_DIR, GEO_BUCKET, OUTPUT_PREFIX)
        upload_folder_to_gcs(LOCAL_ARTEFACTS_DIR, GEO_BUCKET, ARTEFACTS_PREFIX)

        log_event("RUN_COMPLETED", "SUCCESS", "completion")

    except Exception as exc:
        log_event(
            "RUN_FAILED",
            "ERROR",
            "failure",
            {
                "error_type": exc.__class__.__name__,
                "error": str(exc),
                "traceback": traceback.format_exc()[-10000:],
            },
        )
        raise

    finally:
        write_run_log_locally()

        try:
            upload_folder_to_gcs(LOCAL_WORKING_DIR, GEO_BUCKET, WORKING_PREFIX)
        except Exception as log_exc:
            logger.error("Failed to upload Working logs: %s", str(log_exc))


if __name__ == "__main__":
    main()
