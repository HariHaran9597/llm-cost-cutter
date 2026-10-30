# LLM Cost Cutter

**Find the cheapest model and prompt combination that still does your task well.**

LLM Cost Cutter is a local model and prompt evaluation workbench for developers and small teams. Give it examples from one real workload, run the same inputs through candidate model/prompt combinations, inspect the outputs, quality scores, provider-reported tokens, actual measured latency, and estimated API charges. Promote a passing candidate into a task-specific policy for the compatible gateway.

The public Pages site explains the workflow. Live experiments run on your machine so your provider key and evaluation data stay under your control.

## How you use it

1. Pick one task your product sends to an LLM, such as support-ticket classification, structured extraction, or answer drafting.
2. Prepare 5–10 representative inputs with expected answers. Use sanitized examples; the local database stores your inputs and model responses so you can inspect failures.
3. Add candidate variants. Each variant has a model, system prompt, and user template. Compare model changes with the prompt held constant, prompt changes with the model held constant, or compare both together.
4. Choose a scorer: normalized exact match, required-text containment, or an LLM judge with your rubric and a separately visible judge cost. Exact/contains scores are reproducible; judge scores are subjective and should be spot-checked.
5. Review the side-by-side score, cost per case, p50/p95 measured latency, errors, and every output. The recommendation is the lowest-cost variant within five quality points of the baseline, provided it has no provider errors. It falls back to the baseline when none qualify.
6. Promote the candidate. The local gateway can then apply that evaluated model and prompt when the caller supplies the matching `x-costcutter-task` header.

**The product solves a concrete decision:** “For this workload and these examples, which tested model/prompt gives acceptable answers at the lowest observed cost and latency?” It does not claim that one model is universally better or promise savings before you test your workload.

## Start locally

Requirements: Python 3.11+.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `GATEWAY_API_TOKEN` to a random local secret. Set `UPSTREAM_API_KEY` (or `OPENAI_API_KEY`) in `.env`, set `LLMCUTTER_DEMO_MODE=false`, then run:

```powershell
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`, enter the gateway token in the page, and configure a comparison. The provider key stays on the server. The app never sends it to the browser.

If you only want to inspect the product page, GitHub Pages is [here](https://hariharan9597.github.io/llm-cost-cutter/). The hosted page cannot run experiments because it has no access to your local provider key or database.

## Evaluation details

- An experiment runs the full Cartesian product of 1–10 cases and 2–4 variants. It is limited to 48 provider calls per run.
- Before execution, the server estimates token cost from configured model prices and requested output-token limits. The run is rejected if that estimate exceeds the user's selected budget. Token estimates are approximate, so this is not a hard billing cap; actual charges can vary by tokenizer, cached tokens, model behavior, and provider pricing. Set provider-side spending limits too.
- Every candidate sees the same cases. Temperature is fixed at zero. Provider usage is used when returned; otherwise a clearly marked token estimate is recorded.
- The judge mode adds a judge call for every candidate/case. Judge costs count toward the run total; failed or malformed judge responses require manual review.
- The recommendation uses the first variant as baseline and requires quality within 5 percentage points plus zero provider errors. Inspect the individual outputs before promoting any policy.
- Runs, inputs, prompts, expected answers, and outputs are stored locally in SQLite for review. Delete a run in the UI to erase its experiment record. The gateway's exact cache separately stores responses for reuse.

Model prices are editable in `config/pricing.json`, checked 2026-10-30 against [OpenAI API pricing](https://developers.openai.com/api/docs/pricing/). The defaults are GPT-6 Luna, GPT-6.1 Terra, GPT-6.1 Sol, and GPT-6 Astra. Only choose models available to your provider account. Update rates before budgeting; prices vary by provider and service tier.

## Gateway integration

`POST /v1/chat/completions` accepts the supported OpenAI-compatible request subset and requires `Authorization: Bearer <GATEWAY_API_TOKEN>`. A promoted policy is selected with `x-costcutter-task: <task-type>`. Without a promoted policy, a conservative fallback routes only recognizable bounded tasks to the configured cheap model and unknown/complex requests to the baseline.

Supported fields: `messages` with system/user roles, `temperature`, and `max_tokens` (1–2048). Unsupported request fields are rejected. Streaming, tools, and multi-turn agent flows are not supported.

## Business metrics to report

For a workload-specific result, report the dataset name/version and size, scorer and rubric, baseline, candidate, quality denominator, provider and date-stamped rates, total tokens, cost per request, p50/p95 latency, and failure count. Compare only runs with the same cases and record the prompt/model variant IDs. Do not use the former illustrative replay numbers as results; they have been removed.

Resume bullet after a real run: “Compared **[N]** representative **[task]** examples across **[models/prompts]** using **[scorer]**; selected **[variant]** at **[quality]** with **[measured cost change]** and **[p95 latency]**, then promoted it to a task-specific gateway policy.”

## Security and privacy

- Keep provider keys in local environment variables. `.env` is ignored by Git.
- The gateway token is required for runs, experiment history, policy promotion, and live chat calls.
- The app stores prompts and responses locally by design so users can inspect failures. Avoid sensitive or identifying data unless your provider and local retention settings allow it.
- The public GitHub Pages deployment is static and contains no live proxy or user run data.
- Use localhost or a trusted private network. Before public hosting, add production identity, TLS, secret management, distributed abuse controls, durable database protection, and monitoring.

## Development

```powershell
python -m pytest -q
```
