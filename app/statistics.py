from __future__ import annotations

import hashlib
import random


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float] | None:
    """Two-sided Wilson score interval for a binomial proportion."""
    if total <= 0:
        return None
    p = successes / total
    z2 = z * z
    denominator = 1 + z2 / total
    center = (p + z2 / (2 * total)) / denominator
    margin = z * ((p * (1 - p) / total + z2 / (4 * total * total)) ** 0.5) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def paired_bootstrap_delta(
    baseline: list[bool], candidate: list[bool], case_ids: list[str], *, samples: int = 10_000
) -> tuple[float, float] | None:
    """Deterministic percentile CI for candidate-minus-baseline paired accuracy."""
    if not baseline or len(baseline) != len(candidate) or len(case_ids) != len(baseline):
        return None
    deltas = [int(c) - int(b) for b, c in zip(baseline, candidate)]
    observed = sum(deltas) / len(deltas)
    seed = int.from_bytes(hashlib.sha256("\0".join(case_ids).encode()).digest()[:8], "big")
    rng = random.Random(seed)
    boot = []
    for _ in range(samples):
        boot.append(sum(deltas[rng.randrange(len(deltas))] for _ in deltas) / len(deltas))
    boot.sort()
    return boot[int(0.025 * (samples - 1))], boot[int(0.975 * (samples - 1))]
