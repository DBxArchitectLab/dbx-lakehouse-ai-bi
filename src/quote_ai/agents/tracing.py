"""MLflow Tracing for the agent, optional: with MLflow installed every run, model call and tool call is a span; without
it (unit tests, CI) the same code runs untraced. Verify before recording: mlflow.trace, mlflow.start_span(name,
span_type=...), span.set_inputs / set_outputs and mlflow.get_last_active_trace_id in the installed MLflow 3.
"""
from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from typing import Any


def _mlflow():
    try:
        import mlflow
        return mlflow
    except ImportError:
        return None


def trace(fn: Callable) -> Callable:
    """@mlflow.trace when MLflow is there; the function unchanged otherwise."""
    mlflow = _mlflow()
    return mlflow.trace(fn) if mlflow is not None else fn


class _NoSpan:
    def set_inputs(self, *_a: Any) -> None: ...
    def set_outputs(self, *_a: Any) -> None: ...
    def set_attribute(self, *_a: Any) -> None: ...


@contextlib.contextmanager
def span(name: str, span_type: str = "UNKNOWN", inputs: Any = None) -> Iterator[Any]:
    """One span (TOOL, CHAT_MODEL, AGENT): its inputs now, its outputs set by the caller."""
    mlflow = _mlflow()
    if mlflow is None:
        yield _NoSpan()
        return
    try:
        cm = mlflow.start_span(name=name, span_type=span_type)
    except Exception:  # noqa: BLE001  tracing must never break a run
        yield _NoSpan()
        return
    with cm as s:
        if inputs is not None:
            s.set_inputs(inputs)
        yield s


def last_trace_id() -> str | None:
    mlflow = _mlflow()
    if mlflow is None:
        return None
    try:
        return mlflow.get_last_active_trace_id()
    except Exception:  # noqa: BLE001
        return None
