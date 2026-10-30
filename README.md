# LLM Cost Cutter

[![CI](https://github.com/HariHaran9597/llm-cost-cutter/actions/workflows/ci.yml/badge.svg)](https://github.com/HariHaran9597/llm-cost-cutter/actions/workflows/ci.yml) [![GitHub Pages](https://github.com/HariHaran9597/llm-cost-cutter/actions/workflows/pages.yml/badge.svg)](https://github.com/HariHaran9597/llm-cost-cutter/actions/workflows/pages.yml)

**Compare model and prompt choices on the same labeled workload.** LLM Cost Cutter answers a concrete engineering question: *Can a cheaper model or prompt handle this task with an acceptable quality loss, and what will it cost at my traffic volume?*

The hosted [project page](https://hariharan9597.github.io/llm-cost-cutter/) includes a token-cost calculator and the reproducible Banking77 sample. Live comparisons run on your machine using your own API keys. The page is static; it never receives provider keys or proxies calls.

Watch the [60-second captioned walkthrough](https://hariharan9597.github.io/llm-cost-cutter/demo.mp4). It covers the cost calculator, local BYOK workflow, benchmark protocol, report metrics, and the honest pending-score status.

## What a user does

1. Choose a workload and prepare examples with expected labels or answers. The included Banking77 sample is a stratified 385-case slice of its official held-out test set: five examples from each of 77 intents.
2. Pick model and prompt variants. The local workbench supports GPT, Claude, Kimi, and Groq-hosted Qwen 3.8 27B. Compare models with the prompt held constant, or hold the model constant and compare prompt versions.
3. Enter a BYOK key for each provider needed. Keys are used for that request only, are not saved in browser storage or SQLite, and are cleared from the form after the run.
4. Set a spend cap, then run. The app records provider-reported token counts where available, measured request latency, errors, and exact-match output scores. Cost uses the dated rate card and reported usage; it is an estimate of provider charges, not an invoice.
5. Inspect every case and compare accuracy with 95% Wilson intervals, macro-F1, cost per 1,000 requests, and p50/p95 latency. Candidate-versus-baseline accuracy differences use a paired bootstrap confidence interval over the same case IDs.
6. Optionally promote a qualifying candidate into a task-specific gateway policy. The policy recommendation uses a 3 percentage-point non-inferiority margin: the lower bound of the paired 95% interval must be above -3 points, the run must have no provider errors, and all cases must be scored. A small run will often be inconclusive; add more representative cases instead of treating a noisy point estimate as proof.

## Included public benchmark

The reproducible benchmark uses the original PolyAI BANKING77 official test split (3,080 rows, 77 intents), pinned to source revision `9d081458ff52e53cf7e848f414e6e9344e4e6696`. The committed sample is deterministically drawn with seed `20261030`, five examples per intent (385 total). The source file SHA-256 is verified before any run. Dataset license: CC BY 4.0. See the [dataset card](https://huggingface.co/datasets/PolyAI/banking77) and [original source](https://github.com/PolyAI-LDN/task-specific-datasets).

The recorded benchmark protocol compares GPT-6 Luna, Claude Haiku 5.5, and Groq Qwen 3.8 27B, with one zero-shot prompt and exact intent-label scoring. Kimi models remain selectable for local workload experiments and available in the cost calculator, but are not included in this benchmark. Provider errors count as incorrect in the accuracy denominator; p95 latency is over successful calls and labeled accordingly. The runner produces a dated result bundle with individual outputs and protocol metadata. A public headline is published only after real calls complete; no model result is inferred from list prices or mocked tests.

### Run it

Requirements: Python 3.11+.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

First inspect the zero-call cost estimate:

```powershell
python -m benchmarks.run_banking77
```

To run it, put provider keys in environment variables (or in an ignored `.env` loaded by the app):

```powershell
$env:OPENAI_API_KEY = "..."
$env:ANTHROPIC_API_KEY = "..."
$env:GROQ_API_KEY = "..."
python -m benchmarks.run_banking77 --execute --max-budget-usd 0.50
```

The CLI requires an explicit cap at or above its preflight estimate. The estimate is approximate, so configure billing limits at each provider too. Results are written under the ignored `benchmark-results/` directory. To publish a result, review the bundle and run:

```powershell
python -m benchmarks.publish_result benchmark-results/<run-id>/result.json
```

That writes a static artifact for the Pages viewer. The checked-in project currently includes no benchmark score until the above live run succeeds.

## Start the local BYOK workbench

```powershell
Copy-Item .env.example .env
```

Set a random `GATEWAY_API_TOKEN` and set `LLMCUTTER_DEMO_MODE=false` in `.env`, then start the app:

```powershell
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`. Enter the gateway token, then enter only the provider keys used in the current experiment. The form clears BYOK fields after the request. Keys are sent to the local server over localhost and passed to their selected provider; do not expose the local server to an untrusted network. The public static site cannot execute live runs.

For gateway operation beyond one-time BYOK experiments, put provider keys in server environment variables: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `MOONSHOT_API_KEY`, and/or `GROQ_API_KEY`. Gateway calls and saved experiment data remain on your machine.

## Cost model and pricing provenance

The calculator takes input/output token counts and computes a cost for each model with the same counts. This isolates price differences; it does **not** claim the models tokenize the same text identically. Live runs use provider-reported token usage where returned, then apply the configured list rates. Account-specific discounts, batch tiers, regional processing, long-context tiers, caching, and taxes may change actual billing.

Rates are USD per 1 million tokens, checked 2026-10-30, and editable in [`config/pricing.json`](config/pricing.json):

- OpenAI GPT-6 family: [official API pricing](https://developers.openai.com/api/docs/pricing/)
- Claude Haiku 5.5 and Sonnet 5.5: [Anthropic model pricing](https://docs.anthropic.com/en/docs/about-claude/pricing) and [model IDs](https://platform.claude.com/docs/en/models/haiku-5-5/overview)
- Kimi K2.6 and K3: [Moonshot/Kimi API platform](https://platform.kimi.ai/)
- Qwen 3.8 27B (Preview) served by Groq: [model ID and list price](https://console.groq.com/docs/model/qwen/qwen3.8-27b) · [OpenAI-compatible API endpoint](https://console.groq.com/docs/openai)

Groq documents a Free tier with account-specific rate limits and a paid Developer tier; the calculator shows the model's published token rate, not account-specific free usage or invoice cost. Check [Groq's billing tiers](https://console.groq.com/docs/billing-faqs) and [rate limits](https://console.groq.com/docs/rate-limits). The Qwen adapter uses non-streaming completions, 32-token benchmark output, `reasoning_effort=none`, and hidden reasoning for classification; these settings are recorded in the local request code. Refresh provider IDs and prices before purchasing. Access depends on each provider account.

## Product boundaries

- The app is an evaluation and cost decision tool, not a universal model leaderboard. Results apply to the published dataset/prompt or the user's own test set.
- It uses 0-temperature calls where the API supports it. Provider/model changes, prompt changes, and dataset version changes create a new experimental condition.
- The paired bootstrap and Wilson intervals quantify sampling uncertainty for the measured cases. They do not establish production reliability or guarantee future savings.
- Exact cache and deterministic routing are separate gateway features. Cache hit benefits must be measured on the caller's workload; this benchmark does not claim cache savings.
- Static Pages publishes the reviewed benchmark artifact only. No public model proxy, user account, user run, or secret exists on Pages.

## Development

```powershell
python -m pytest -q
node --check web/app.js
```

CI runs the test suite on pushes and pull requests. The Pages workflow deploys the static UI and reviewed data artifacts from `web/`.

## Resume claim

Use a result only after a successful, reviewed live run. Keep the denominator, confidence interval, exact data split, prompt, provider/model IDs, price date, and failed calls attached to the claim. A concise format is:

> Evaluated **[models]** on **[N]** held-out **[task]** cases; **[model]** achieved **[accuracy, 95% CI]** at **[$/1,000 requests]** and **[p95 ms]**, with **[error count]** failed calls, using **[dataset version and prompt]**.

## License

Application code: MIT. BANKING77 data remains subject to its CC BY 4.0 license and attribution to PolyAI and Casanueva et al. (2020).
