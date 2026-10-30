from __future__ import annotations

import argparse
import json
from pathlib import Path

EXPECTED_MODELS = {"gpt-6-luna", "claude-haiku-5-5", "groq-qwen3.8-27b"}


def publish(source: Path) -> Path:
    bundle = json.loads(source.read_text(encoding="utf-8"))
    models = bundle.get("models", {})
    dataset = bundle.get("dataset", {})
    if set(models) != EXPECTED_MODELS or dataset.get("evaluated_rows") != 385:
        raise SystemExit("Refusing publication: expected the complete 385-case, three-model BANKING77 run.")
    if any(metrics.get("cases") != 385 or "accuracy_ci_95_wilson" not in metrics for metrics in models.values()):
        raise SystemExit("Refusing publication: missing denominators or confidence intervals.")
    if any(len(bundle.get("items", {}).get(model, [])) != 385 for model in EXPECTED_MODELS):
        raise SystemExit("Refusing publication: per-case results are incomplete.")
    if any(not metrics.get("successful_calls") for metrics in models.values()):
        raise SystemExit("Refusing publication: a model had no successful calls.")
    output = {
        key: value for key, value in bundle.items() if key != "items"
    }
    output["items"] = {
        model: [{k: v for k, v in row.items() if k != "error"} for row in rows]
        for model, rows in bundle["items"].items()
    }
    target = Path(__file__).resolve().parents[1] / "web" / "data" / "banking77-result.json"
    target.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish a reviewed real BANKING77 benchmark artifact to the static site.")
    parser.add_argument("result_bundle", type=Path)
    path = publish(parser.parse_args().result_bundle)
    print(f"Published benchmark artifact: {path}")


if __name__ == "__main__":
    main()
