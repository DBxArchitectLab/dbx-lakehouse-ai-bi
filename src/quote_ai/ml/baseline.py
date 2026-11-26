"""The rule baseline (AI-13): what a sales manager would write on a whiteboard.

    win if our price is at or below what the customer pays today, and we can deliver in time

Any model must beat it on months it has never seen, or it doesn't ship. To be compared like a model (AUC, Brier,
calibration), the rule outputs a probability: the win rate of its bucket (rule true / rule false) in the training
months. It is logged to MLflow as a pyfunc model, in the same experiment as the candidates.

The history does not record the date each customer asked for, so "in time" is DBxLab's standard ask: 10 days
(Lakeside's too).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

REQUESTED_DAYS = 10


def rule(row: Mapping, requested_days: int = REQUESTED_DAYS) -> bool:
    pct, lead = row.get("price_vs_customer_current_pct"), row.get("lead_time_days")
    return pct is not None and lead is not None and pct <= 0 and lead <= requested_days


def _records(model_input) -> list[Mapping]:
    """A pandas DataFrame (pyfunc) or a list of dicts."""
    if hasattr(model_input, "to_dict"):
        return model_input.to_dict(orient="records")
    return list(model_input)


class RuleBaseline:
    """pyfunc-compatible: predict(context, model_input) returns P(win) per row."""

    def __init__(self, requested_days: int = REQUESTED_DAYS) -> None:
        self.requested_days = requested_days
        self.p_true = self.p_false = None

    def fit(self, rows: Sequence[Mapping], y: Sequence[int]) -> RuleBaseline:
        """Win rate per bucket, smoothed (+1 win, +2 quotes) so an empty bucket is 0.5, not a division by zero."""
        hits = {True: [0, 0], False: [0, 0]}
        for r, t in zip(rows, y):
            b = hits[rule(r, self.requested_days)]
            b[0] += t
            b[1] += 1
        self.p_true = (hits[True][0] + 1) / (hits[True][1] + 2)
        self.p_false = (hits[False][0] + 1) / (hits[False][1] + 2)
        return self

    def predict_proba(self, rows: Iterable[Mapping]) -> list[float]:
        if self.p_true is None:
            raise RuntimeError("fit the baseline on the training months first")
        return [self.p_true if rule(r, self.requested_days) else self.p_false for r in rows]

    def predict(self, context, model_input, params=None) -> list[float]:
        return self.predict_proba(_records(model_input))

    def params(self) -> dict:
        return {"rule": "price_vs_customer_current_pct <= 0 and lead_time_days <= requested_days",
                "requested_days": self.requested_days, "p_win_rule_true": round(self.p_true, 4),
                "p_win_rule_false": round(self.p_false, 4)}

    def as_pyfunc(self):
        """The same object as an mlflow.pyfunc.PythonModel, for mlflow.pyfunc.log_model (MLflow imported here only)."""
        import mlflow.pyfunc

        baseline = self

        class RuleBaselineModel(mlflow.pyfunc.PythonModel):
            def predict(self, context, model_input, params=None):
                return baseline.predict(context, model_input, params)
        return RuleBaselineModel()
