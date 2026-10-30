from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "demo-frames"
W, H = 1280, 720
BG, PANEL, TEXT, MUTED, GREEN, CYAN = "#0b1020", "#121a2c", "#f4f7ff", "#9ba9c5", "#8ff0bd", "#78d9f5"
FONT = Path("C:/Windows/Fonts/segoeui.ttf")
FONT_BOLD = Path("C:/Windows/Fonts/segoeuib.ttf")


def font(size, bold=False):
    return ImageFont.truetype(str(FONT_BOLD if bold else FONT), size)


def scene(index, kicker, title, subtitle, rows, callout):
    image = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((36, 32, W - 36, H - 32), radius=28, fill=PANEL, outline="#26334d", width=2)
    d.text((76, 66), "LLM COST CUTTER  /  MODEL & PROMPT EVALUATION", font=font(19, True), fill=CYAN)
    d.text((W - 165, 66), f"{index:02d} / 06", font=font(18), fill=MUTED)
    d.text((76, 122), kicker.upper(), font=font(16, True), fill=GREEN)
    d.text((76, 158), title, font=font(42, True), fill=TEXT)
    d.text((76, 216), subtitle, font=font(22), fill=MUTED)
    y = 290
    for left, right in rows:
        d.rounded_rectangle((76, y, W - 76, y + 58), radius=12, fill="#182338")
        d.text((98, y + 14), left, font=font(18, True), fill=TEXT)
        d.text((W - 100, y + 14), right, font=font(18), fill=GREEN, anchor="ra")
        y += 72
    d.rounded_rectangle((76, 596, W - 76, 652), radius=12, fill="#183329")
    d.text((98, 612), callout, font=font(19, True), fill=GREEN)
    image.save(OUT / f"scene-{index:02}.png")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    scenes = [
        ("THE DECISION", "Find the right model for your task", "Run the same labeled workload through different models or prompt versions.", [("One task and one fixed case set", "CONTROLLED"), ("Side-by-side outputs", "INSPECTABLE"), ("Quality, cost and latency", "MEASURED")], "Question answered: can a cheaper option do this job well enough?"),
        ("LIVE PRICE CARD", "Compare token cost across providers", "Enter a shared token volume to see a transparent list-price estimate.", [("GPT-6 Luna  ·  1k in / 200 out", "$0.20 / 1k requests"), ("Claude Haiku 5.5  ·  same volume", "$0.20 / 1k requests"), ("Kimi K2.6  ·  same volume", "$1.75 / 1k requests")], "Same token counts isolate rates; actual tokenization and billing vary."),
        ("BRING YOUR OWN KEY", "Run experiments from your machine", "Keys are entered for a run and sent to the local API, never the Pages site.", [("OpenAI key", "REQUEST ONLY"), ("Anthropic key", "REQUEST ONLY"), ("Moonshot / Kimi key", "REQUEST ONLY")], "Choose your cases, prompts, models, and an explicit spend cap."),
        ("PUBLIC REPRODUCIBLE TASK", "Banking77 intent classification", "A deterministic slice from the official held-out test set.", [("385 examples", "5 × 77 intents"), ("Fixed zero-shot prompt", "SAME FOR EACH MODEL"), ("GPT-6 Luna · Claude Haiku · Kimi K2.6", "3 CANDIDATES")], "Pinned dataset + seed + protocol make the run repeatable."),
        ("WHAT THE REPORT MEASURES", "Read more than one headline score", "Every number is tied to cases, model IDs, date and protocol.", [("Accuracy + 95% Wilson interval", "QUALITY"), ("Paired bootstrap quality delta", "UNCERTAINTY"), ("Cost / 1,000 · p50/p95 · errors", "OPERATIONS")], "Errors count as incorrect; latency percentiles use successful calls."),
        ("HONEST RELEASE STATUS", "The runner is ready; live score is pending", "No model outcome is published until real provider calls complete.", [("Three providers × 385 examples", "1,155 CALLS"), ("Approximate preflight spend", "ABOUT $0.335"), ("Run cap + provider keys", "OWNER CONTROLLED")], "The hosted page is static. No public proxy and no fabricated result."),
    ]
    for i, values in enumerate(scenes, start=1):
        scene(i, *values)


if __name__ == "__main__":
    main()
