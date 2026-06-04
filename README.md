# martech-geo-v2 — Complete GEO Accelerator Code

This is the complete Git-ready code package for the current architecture.

## Current requirements implemented

- No login/sign-up page.
- Brand form is served by `geo-web`.
- `user_id` is generated automatically from Brand Name.
- Spaces are removed from Brand Name.
- `user_id` is made unique with timestamp + UUID suffix.
- Data is stored in one GCS bucket:
  `gs://geo-accelator`
- GCS structure:
  `gs://geo-accelator/{generated_user_id}/{run_id}/Input/`
  `gs://geo-accelator/{generated_user_id}/{run_id}/Output/`
  `gs://geo-accelator/{generated_user_id}/{run_id}/Working/`
  `gs://geo-accelator/{generated_user_id}/{run_id}/Artefacts/`
- API selection from web form:
  OpenAI, Anthropic, Perplexity, Google.
- If no API is selected, all available configured APIs run.
- API keys supported:
  `OPENAI_API_KEY`
  `ANTHROPIC_API_KEY`
  `PERPLEXITY_API_KEY`
  `GOOGLE_API_KEY`
- Job-level structured logs are written to Cloud Logging and to:
  `Working/run_log.json`
- Masked key availability is written to:
  `Working/llm_runtime_config.json`

## Project structure

```text
martech-geo-v2/
├── job_runner.py
├── Dockerfile
├── requirements.txt
├── README.md
├── .gitignore
│
├── geo-web/
│   ├── main.py
│   ├── requirements.txt
│   ├── Procfile
│   ├── runtime.txt
│   └── README.md
│
├── Agent/
│   └── Agent1/
│       ├── executor.py
│       ├── README.md
│       ├── SubAgents/
│       └── Prompts/
│
├── Common/
├── Input/
├── Output/
├── Working/
└── Artefacts/
```

## Build Cloud Run Job image from repo root

```bash
gcloud builds submit . \
  --tag asia-south2-docker.pkg.dev/martech-497412/cloud-run-source-deploy/martech-geo-pipeline:complete-v1
```

## Update Cloud Run Job

```bash
gcloud run jobs update martech-geo-llm \
  --region=asia-south2 \
  --image=asia-south2-docker.pkg.dev/martech-497412/cloud-run-source-deploy/martech-geo-pipeline:complete-v1
```

## Attach all API key secrets to Cloud Run Job

```bash
gcloud run jobs update martech-geo-llm \
  --region=asia-south2 \
  --update-secrets=OPENAI_API_KEY=OPENAI_API_KEY:latest,ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,PERPLEXITY_API_KEY=PERPLEXITY_API_KEY:latest,GOOGLE_API_KEY=GOOGLE_API_KEY:latest
```

## geo-web Cloud Run Service env vars

```text
GCP_PROJECT_ID=martech-497412
REGION=asia-south2
GEO_BUCKET=geo-accelator
PROCESSOR_JOB_NAME=martech-geo-llm
```

## Cloud Run Job default env vars

```text
GEO_BUCKET=geo-accelator
USER_ID=brand_user_001
RUN_ID=run_001
INPUT_PREFIX=brand_user_001/run_001/Input
OUTPUT_PREFIX=brand_user_001/run_001/Output
WORKING_PREFIX=brand_user_001/run_001/Working
ARTEFACTS_PREFIX=brand_user_001/run_001/Artefacts
OPENAI_MODEL=gpt-4o-mini
ANTHROPIC_MODEL=claude-3-5-sonnet-latest
PERPLEXITY_MODEL=sonar-pro
GOOGLE_MODEL=gemini-1.5-pro
```

When the web form triggers the job, `USER_ID`, `RUN_ID`, prefixes and `SELECTED_APIS` are dynamically overridden.
