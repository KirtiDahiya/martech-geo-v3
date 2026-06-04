# geo-web

FastAPI UI service.

## Behavior

- Opens a form at `/`
- User fills Brand Name and brand context fields.
- User may select APIs:
  - OpenAI
  - Anthropic
  - Perplexity
  - Google
- If no API is selected, all available configured APIs run.
- Generates `user_id` from Brand Name.
- Generates `run_id`.
- Uploads `brand_context.md` to:
  `gs://geo-accelator/{generated_user_id}/{run_id}/Input/brand_context.md`
- Triggers Cloud Run Job `martech-geo-llm`.

## Start command

```text
web: sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"
```
