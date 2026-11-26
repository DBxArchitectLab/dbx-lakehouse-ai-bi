"""A quick model with no dependencies (AI-12): logistic regression by gradient descent on standardized features.

Used for the leak demo (notebooks/quote_ai/12_ml_features.py) and the tests, where it shows that a leaky feature
looks like success: near-perfect on a random split, and still suspicious on a time split. The real candidates are
scikit-learn models trained on Databricks (quote_ai.ml.train).
"""
from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping, Sequence

from quote_ai.ml import metrics


class QuickModel:
    def __init__(self, columns: Sequence[str], epochs: int = 300, rate: float = 0.5, l2: float = 1e-3) -> None:
        self.columns, self.epochs, self.rate, self.l2 = list(columns), epochs, rate, l2

    def _raw(self, row: Mapping) -> list[float]:
        out = []
        for c in self.numeric:
            v = row.get(c)
            out.append(0.0 if v is None else float(v))
            out.append(1.0 if v is None else 0.0)   # a missing indicator: absence can be information (and a leak)
        for c, values in self.levels.items():
            out += [1.0 if row.get(c) == v else 0.0 for v in values]
        return out

    def importance(self) -> dict[str, float]:
        """|weight| per input column on the standardized scale (a column's indicators and levels added up)."""
        out: dict[str, float] = {}
        for owner, w in zip(self.owners, self.w):
            out[owner] = out.get(owner, 0.0) + abs(w)
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def fit(self, rows: Sequence[Mapping], y: Sequence[int]) -> QuickModel:
        self.levels = {c: sorted({r[c] for r in rows if isinstance(r.get(c), str)}) for c in self.columns
                       if any(isinstance(r.get(c), str) for r in rows)}
        self.numeric = [c for c in self.columns if c not in self.levels]
        self.owners = [c for c in self.numeric for _ in (0, 1)] + [c for c, v in self.levels.items() for _ in v]
        X = [self._raw(r) for r in rows]
        k = len(X[0])
        self.mean = [sum(x[j] for x in X) / len(X) for j in range(k)]
        self.sd = [math.sqrt(sum((x[j] - self.mean[j]) ** 2 for x in X) / len(X)) or 1.0 for j in range(k)]
        Z = [self._scale(x) for x in X]
        self.w, self.b = [0.0] * k, 0.0
        n = len(Z)
        for _ in range(self.epochs):
            gw, gb = [0.0] * k, 0.0
            for z, t in zip(Z, y):
                e = self._sigmoid(sum(wj * zj for wj, zj in zip(self.w, z)) + self.b) - t
                gb += e
                for j in range(k):
                    gw[j] += e * z[j]
            self.w = [wj - self.rate * (g / n + self.l2 * wj) for wj, g in zip(self.w, gw)]
            self.b -= self.rate * gb / n
        return self

    def _scale(self, x: list[float]) -> list[float]:
        return [(v - m) / s for v, m, s in zip(x, self.mean, self.sd)]

    @staticmethod
    def _sigmoid(z: float) -> float:
        return 1 / (1 + math.exp(-max(min(z, 30), -30)))

    def predict_proba(self, rows: Iterable[Mapping]) -> list[float]:
        return [self._sigmoid(sum(w * z for w, z in zip(self.w, self._scale(self._raw(r)))) + self.b) for r in rows]


def random_split(rows: Sequence[Mapping], test_share: float = 0.3, seed: int = 1) -> tuple[list, list]:
    """The mistake: a random split mixes future quotes into training."""
    shuffled = list(rows)
    random.Random(seed).shuffle(shuffled)
    cut = int(len(shuffled) * (1 - test_share))
    return shuffled[:cut], shuffled[cut:]


def score(train: Sequence[Mapping], test: Sequence[Mapping], columns: Sequence[str], label: str = "won") -> float:
    """Test AUC of a quick model trained on train: the number the demo puts on screen."""
    model = QuickModel(columns).fit(train, [r[label] for r in train])
    return metrics.roc_auc([r[label] for r in test], model.predict_proba(test))
