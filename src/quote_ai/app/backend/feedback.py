"""Feedback on answers (AI-22): a rep's thumbs up or down, saved on the MLflow trace of that exact answer, and a thumbs
down kept as a candidate evaluation case.

  MlflowFeedback   mlflow.log_feedback on the trace: name rep_rating, value true/false, rationale = the comment,
                   source HUMAN with the signed-in rep's ID. Candidates go to <catalog>.quote_ai_eval.candidate_cases,
                   written as the app's service principal (an eval write: what the app's own identity keeps, AI-24)
  DemoFeedback     the same contract in memory (tests, QUOTE_AI_DEMO=1)

A candidate is not a test. A person reviews it, writes the expected result, and only then does it join
evaluation/quote_ai/v1, in a pull request. Feedback never edits the package by itself.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone


def log_on_trace(trace_id: str, rep: str, thumbs_up: bool, comment: str | None) -> None:
    """The rating on the trace. Verify before recording: mlflow.log_feedback(trace_id=, name=, value=, rationale=,
    source=AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id=)) in the installed MLflow 3."""
    import mlflow
    from mlflow.entities import AssessmentSource, AssessmentSourceType
    mlflow.log_feedback(trace_id=trace_id, name="rep_rating", value=thumbs_up, rationale=comment or None,
                        source=AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id=rep))


def candidate(trace_id: str | None, rep: str, comment: str | None, quote_id: str | None, surface: str) -> dict:
    return {"candidate_id": f"cand-{uuid.uuid4().hex[:10]}", "trace_id": trace_id, "rep": rep, "quote_id": quote_id,
            "surface": surface, "comment": comment, "status": "candidate", "created_at": datetime.now(timezone.utc)}


class DemoFeedback:
    def __init__(self) -> None:
        self.ratings: list[dict] = []
        self.candidates: list[dict] = []

    def rate(self, trace_id, rep, thumbs_up, comment, quote_id=None, surface="chat") -> dict:
        if trace_id:
            self.ratings.append({"trace_id": trace_id, "name": "rep_rating", "value": thumbs_up,
                                 "rationale": comment, "source": {"source_type": "HUMAN", "source_id": rep}})
        out = {"saved": True, "on_trace": bool(trace_id), "candidate_id": None}
        if not thumbs_up:
            c = candidate(trace_id, rep, comment, quote_id, surface)
            self.candidates.append(c)
            out["candidate_id"] = c["candidate_id"]
        return out


class MlflowFeedback:
    """On Databricks: the rating on the trace (the app's traces experiment) and the candidate row in the eval schema.
    `sql` is the app's own SQL client (its service principal), the one identity allowed to write eval rows."""

    def __init__(self, sql, eval_schema: str) -> None:
        self.sql, self.table = sql, f"{eval_schema}.candidate_cases"

    def rate(self, trace_id, rep, thumbs_up, comment, quote_id=None, surface="chat") -> dict:
        if trace_id:
            log_on_trace(trace_id, rep, thumbs_up, comment)
        out = {"saved": True, "on_trace": bool(trace_id), "candidate_id": None}
        if not thumbs_up:
            c = candidate(trace_id, rep, comment, quote_id, surface)
            self.sql.execute(f"INSERT INTO {self.table} (candidate_id, trace_id, rep, quote_id, surface, comment, "
                             f"status, created_at) VALUES (:candidate_id, :trace_id, :rep, :quote_id, :surface, "
                             f":comment, :status, :created_at)", c)
            out["candidate_id"] = c["candidate_id"]
        return out
