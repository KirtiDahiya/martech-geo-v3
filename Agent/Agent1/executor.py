"""
Agent 1 Executor

Purpose:
- Reads Input/brand_context.md
- Reads SELECTED_APIS from env
- If SELECTED_APIS is blank/missing, runs all configured APIs
- Supports:
  - OpenAI
  - Anthropic
  - Perplexity
  - Google Gemini
- Writes Output/output.json

This is a complete working reference executor.
You can replace/enhance it with your deeper GEO pipeline logic.
"""

import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests


INPUT_DIR = Path("Input")
OUTPUT_DIR = Path("Output")
WORKING_DIR = Path("Working")

BRAND_CONTEXT_PATH = INPUT_DIR / "brand_context.md"
OUTPUT_PATH = OUTPUT_DIR / "output.json"

SUPPORTED_APIS = ["openai", "anthropic", "perplexity", "google"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_selected_apis() -> list[str]:
    raw = os.environ.get("SELECTED_APIS", "").strip()

    if not raw:
        return SUPPORTED_APIS.copy()

    selected = []
    for item in raw.split(","):
        api = item.strip().lower()
        if api in SUPPORTED_APIS and api not in selected:
            selected.append(api)

    return selected if selected else SUPPORTED_APIS.copy()


def read_brand_context() -> str:
    if not BRAND_CONTEXT_PATH.exists():
        raise FileNotFoundError(f"Missing input file: {BRAND_CONTEXT_PATH}")

    return BRAND_CONTEXT_PATH.read_text(encoding="utf-8")


def make_prompt(brand_context_md: str) -> str:
    return f"""
You are a Generative Engine Optimization analyst.

Analyze the brand context below and provide a structured output with:
1. brand_summary
2. competitor_observations
3. geo_strengths
4. geo_weaknesses
5. recommended_content_improvements
6. priority_actions
7. suggested_next_steps

Brand Context:
{brand_context_md}
"""


def call_openai(prompt: str) -> dict:
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    if not api_key:
        return {
            "provider": "openai",
            "model": model,
            "status": "SKIPPED",
            "error": "OPENAI_API_KEY not configured",
            "response": None,
            "usage": None,
        }

    try:
        client = OpenAI(api_key=api_key)

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a practical GEO analyst."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )

        usage = None
        if getattr(response, "usage", None):
            usage = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
                "completion_tokens": getattr(response.usage, "completion_tokens", None),
                "total_tokens": getattr(response.usage, "total_tokens", None),
            }

        return {
            "provider": "openai",
            "model": model,
            "status": "SUCCESS",
            "error": None,
            "response": response.choices[0].message.content,
            "usage": usage,
        }

    except Exception as exc:
        return {
            "provider": "openai",
            "model": model,
            "status": "FAILED",
            "error_type": exc.__class__.__name__,
            "error": str(exc),
            "response": None,
            "usage": None,
        }


def call_anthropic(prompt: str) -> dict:
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    model = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")

    if not api_key:
        return {
            "provider": "anthropic",
            "model": model,
            "status": "SKIPPED",
            "error": "ANTHROPIC_API_KEY not configured",
            "response": None,
            "usage": None,
        }

    try:
        client = anthropic.Anthropic(api_key=api_key)

        response = client.messages.create(
            model=model,
            max_tokens=1200,
            temperature=0.2,
            messages=[
                {"role": "user", "content": prompt}
            ],
        )

        text_parts = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)

        usage = None
        if getattr(response, "usage", None):
            usage = {
                "input_tokens": getattr(response.usage, "input_tokens", None),
                "output_tokens": getattr(response.usage, "output_tokens", None),
            }

        return {
            "provider": "anthropic",
            "model": model,
            "status": "SUCCESS",
            "error": None,
            "response": "\n".join(text_parts),
            "usage": usage,
        }

    except Exception as exc:
        return {
            "provider": "anthropic",
            "model": model,
            "status": "FAILED",
            "error_type": exc.__class__.__name__,
            "error": str(exc),
            "response": None,
            "usage": None,
        }


def call_perplexity(prompt: str) -> dict:
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    model = os.environ.get("PERPLEXITY_MODEL", "sonar-pro")

    if not api_key:
        return {
            "provider": "perplexity",
            "model": model,
            "status": "SKIPPED",
            "error": "PERPLEXITY_API_KEY not configured",
            "response": None,
            "usage": None,
        }

    try:
        url = "https://api.perplexity.ai/chat/completions"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a practical GEO analyst."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }

        response = requests.post(url, headers=headers, json=payload, timeout=120)

        if response.status_code >= 400:
            return {
                "provider": "perplexity",
                "model": model,
                "status": "FAILED",
                "error_type": f"HTTP_{response.status_code}",
                "error": response.text,
                "response": None,
                "usage": None,
            }

        data = response.json()

        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content")
        )

        return {
            "provider": "perplexity",
            "model": model,
            "status": "SUCCESS",
            "error": None,
            "response": content,
            "usage": data.get("usage"),
        }

    except Exception as exc:
        return {
            "provider": "perplexity",
            "model": model,
            "status": "FAILED",
            "error_type": exc.__class__.__name__,
            "error": str(exc),
            "response": None,
            "usage": None,
        }


def call_google(prompt: str) -> dict:
    import google.generativeai as genai

    api_key = os.environ.get("GOOGLE_API_KEY")
    model = os.environ.get("GOOGLE_MODEL", "gemini-1.5-pro")

    if not api_key:
        return {
            "provider": "google",
            "model": model,
            "status": "SKIPPED",
            "error": "GOOGLE_API_KEY not configured",
            "response": None,
            "usage": None,
        }

    try:
        genai.configure(api_key=api_key)

        client = genai.GenerativeModel(model)
        response = client.generate_content(prompt)

        return {
            "provider": "google",
            "model": model,
            "status": "SUCCESS",
            "error": None,
            "response": getattr(response, "text", None),
            "usage": None,
        }

    except Exception as exc:
        return {
            "provider": "google",
            "model": model,
            "status": "FAILED",
            "error_type": exc.__class__.__name__,
            "error": str(exc),
            "response": None,
            "usage": None,
        }


def run_selected_apis(prompt: str, selected_apis: list[str]) -> dict:
    results = {}

    if "openai" in selected_apis:
        results["openai"] = call_openai(prompt)

    if "anthropic" in selected_apis:
        results["anthropic"] = call_anthropic(prompt)

    if "perplexity" in selected_apis:
        results["perplexity"] = call_perplexity(prompt)

    if "google" in selected_apis:
        results["google"] = call_google(prompt)

    return results


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    WORKING_DIR.mkdir(parents=True, exist_ok=True)

    selected_apis = parse_selected_apis()
    brand_context_md = read_brand_context()
    prompt = make_prompt(brand_context_md)

    started_at = utc_now()

    try:
        llm_results = run_selected_apis(prompt, selected_apis)

        status_values = [item.get("status") for item in llm_results.values()]
        success_count = status_values.count("SUCCESS")
        failed_count = status_values.count("FAILED")
        skipped_count = status_values.count("SKIPPED")

        payload = {
            "processing_status": "SUCCESS" if success_count > 0 else "SUCCESS_WITH_LLM_FAILURE",
            "generated_at": utc_now(),
            "started_at": started_at,
            "selected_apis": selected_apis,
            "summary": {
                "success_count": success_count,
                "failed_count": failed_count,
                "skipped_count": skipped_count,
            },
            "llm_results": llm_results,
        }

        OUTPUT_PATH.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(json.dumps({
            "event": "AGENT1_EXECUTOR_COMPLETED",
            "output_path": str(OUTPUT_PATH),
            "selected_apis": selected_apis,
            "success_count": success_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
        }))

    except Exception as exc:
        error_payload = {
            "processing_status": "FAILED",
            "generated_at": utc_now(),
            "selected_apis": selected_apis,
            "error_type": exc.__class__.__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }

        OUTPUT_PATH.write_text(
            json.dumps(error_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        raise


if __name__ == "__main__":
    main()
