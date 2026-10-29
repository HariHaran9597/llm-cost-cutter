# LLM Cost Cutter

An inspectable OpenAI-compatible gateway that tries exact-cache reuse, routes only eligible requests to a cheaper model, and records enough metadata to compare cost and latency against a fixed-model baseline.

**Release status:** v0.1 release candidate. Benchmark values are replay results from the included hand-checked dataset, not live production savings. Live provider calls require a local API key and are disabled in the public demo.

## Quick start

Requirements: Python 3.11+.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Set UPSTREAM_API_KEY and GATEWAY_API_TOKEN in .env before live use.
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`. The API accepts the supported subset of OpenAI Chat Completions at `POST /v1/chat/completions`. Send `Authorization: Bearer <GATEWAY_API_TOKEN>`. Live calls require a configured provider key and `LLMCUTTER_DEMO_MODE=false`; keep demo mode enabled to disable all upstream calls and use only the read-only dashboard/replay. Do not expose an unauthenticated instance to the internet.

## What v0.1 does

- One configurable OpenAI-compatible upstream; model identifiers and rates live in `config/pricing.json`.
- Exact cache over normalized request content and model/policy configuration. Raw prompts are neither persisted nor returned in analytics.
- Deterministic routing: explicit `x-costcutter-tier: quality` selects baseline; otherwise bounded, low-risk requests can use the cheaper model. Hard reasoning cues select baseline. Every choice has a reason.
- Token, latency, model, cache, estimated USD cost, and route metadata are stored in SQLite.
- A replayable benchmark compares baseline and optimized paths on the same 24 labelled examples. A deterministic rubric checks expected outcomes; no LLM judge is used.
- Static GitHub Pages demo reads a sanitized benchmark report and cannot proxy live requests.

Semantic cache, streaming, tools, multi-turn workflows, multiple providers, and public live inference are deferred. Exact cache avoids changing answer behavior. Semantic reuse needs a separately validated embedding and false-hit evaluation before it can be enabled.

## API and controls

Supported request fields: `model` (optional, ignored in favor of configured gateway routing), `messages` (system/user only, 1–20 messages), `temperature` (0–2), `max_tokens` (1–2048). Unsupported fields are rejected. Set `MAX_REQUESTS_PER_MINUTE`, `MAX_DAILY_SPEND_USD`, `REQUEST_TIMEOUT_SECONDS`, and retry settings in `.env`. Retries are limited to transient upstream failures. The gateway token protects this single-user local service; it is not multi-tenant identity or a substitute for a production edge gateway.

## Benchmark and business metrics

Run `python -m app.benchmark --write-report`. The dataset and rubric are in `data/benchmark.json`. The generated report includes the denominator, exact-match task score, estimated model cost from the dated price file, cost change versus baseline, route mix, and latency percentiles. Replay timings are local simulation timings and must not be presented as provider latency. The toy dataset supports product validation, not a claim of real-world production savings.

Estimated cost is `(input_tokens * input_usd_per_million + output_tokens * output_usd_per_million) / 1_000_000`. Price source checked 2026-10-29: [OpenAI API pricing](https://developers.openai.com/api/docs/pricing/). Pricing excludes taxes, discounts, cached-input rates, tools, and other provider charges. Refresh before using the estimates for budgets.

## Development

```powershell
python -m pytest
python -m app.benchmark --write-report
```

## Security and privacy

- Keep provider keys server-side in environment variables; `.env` is ignored.
- The live endpoint requires a gateway token, applies request and spend limits, and rejects unsupported fields.
- Logs store message hashes and operational metadata, not raw prompts or completions. Exact-cache values are stored locally because cache reuse requires them; protect the SQLite file and disable caching if that is not acceptable.
- Demo mode disables upstream calls. GitHub Pages serves only benchmark report files.
- Use localhost or a trusted private network. Before internet deployment, add TLS termination, secret management, stronger identity, distributed rate limiting, durable database controls, and operational monitoring.

## Resume bullet (fill only after a real evaluation)

Built an OpenAI-compatible LLM gateway with exact caching and deterministic model routing; on **[dataset/version, n=24]**, changed estimated token spend by **[measured amount]** while retaining **[measured rubric score]**, with per-request route, cache, token, latency, and cost traces.
