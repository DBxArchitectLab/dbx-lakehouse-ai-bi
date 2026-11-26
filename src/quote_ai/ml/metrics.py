"""How a win classifier is measured (AI-13): one implementation for every candidate, the rule baseline included, so
the comparison is like for like. Plain Python, no dependencies.

  roc_auc            how well winners are ranked above losers, across every threshold (0.5 = a coin)
  brier              mean squared error of the probability (lower is better)
  calibration_error  expected calibration error: does "60%" mean 60%? (weighted gap per probability bin)
  precision_recall   of the quotes predicted to win, how many did; of the quotes won, how many were predicted
Accuracy is reported nowhere: with about 60% of quotes won, "always win" is 60% accurate and useless.
"""
from __future__ import annotations

from collections.abc import Sequence


def roc_auc(y: Sequence[int], p: Sequence[float]) -> float:
    """Mann-Whitney U: the chance a random winner scores above a random loser (ties count half)."""
    pairs = sorted(zip(p, y))
    pos = sum(y)
    neg = len(y) - pos
    if not pos or not neg:
        raise ValueError("AUC needs both outcomes")
    rank_sum, i = 0.0, 0
    while i < len(pairs):
        j = i
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2
        rank_sum += avg_rank * sum(t for _, t in pairs[i:j])
        i = j
    return (rank_sum - pos * (pos + 1) / 2) / (pos * neg)


def brier(y: Sequence[int], p: Sequence[float]) -> float:
    return sum((pi - yi) ** 2 for yi, pi in zip(y, p)) / len(y)


def calibration_bins(y: Sequence[int], p: Sequence[float], bins: int = 10) -> list[dict]:
    """The calibration plot's points: per probability bin, the mean prediction and the observed win rate."""
    groups: list[list[tuple[int, float]]] = [[] for _ in range(bins)]
    for yi, pi in zip(y, p):
        groups[min(int(pi * bins), bins - 1)].append((yi, pi))
    return [{"bin": b, "n": len(g), "predicted": sum(pi for _, pi in g) / len(g), "observed": sum(yi for yi, _ in g) / len(g)}
            for b, g in enumerate(groups) if g]


def calibration_error(y: Sequence[int], p: Sequence[float], bins: int = 10) -> float:
    n = len(y)
    return sum(b["n"] / n * abs(b["predicted"] - b["observed"]) for b in calibration_bins(y, p, bins))


def precision_recall(y: Sequence[int], p: Sequence[float], threshold: float = 0.5) -> tuple[float, float]:
    tp = sum(1 for yi, pi in zip(y, p) if pi >= threshold and yi)
    fp = sum(1 for yi, pi in zip(y, p) if pi >= threshold and not yi)
    fn = sum(1 for yi, pi in zip(y, p) if pi < threshold and yi)
    return (tp / (tp + fp) if tp + fp else 0.0), (tp / (tp + fn) if tp + fn else 0.0)


def evaluate(y: Sequence[int], p: Sequence[float], prefix: str) -> dict[str, float]:
    """Every metric of one split, named for MLflow: valid_auc, valid_brier, valid_calibration_error, ..."""
    precision, recall = precision_recall(y, p)
    return {f"{prefix}_auc": roc_auc(y, p), f"{prefix}_brier": brier(y, p),
            f"{prefix}_calibration_error": calibration_error(y, p), f"{prefix}_precision": precision,
            f"{prefix}_recall": recall, f"{prefix}_rows": float(len(y)), f"{prefix}_win_rate": sum(y) / len(y)}
