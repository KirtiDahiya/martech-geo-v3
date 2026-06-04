# Agent 1

`executor.py` is a complete reference executor.

It reads:

```text
Input/brand_context.md
```

It reads API selection from:

```text
SELECTED_APIS
```

It supports these providers:

```text
openai
anthropic
perplexity
google
```

It writes:

```text
Output/output.json
```

You can replace the reference logic with your full GEO pipeline while keeping the same inputs/outputs.
