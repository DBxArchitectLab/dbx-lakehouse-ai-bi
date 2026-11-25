"""Exercise Delta log retries without Spark or optional model dependencies."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SOURCE_PATH = Path(__file__).resolve().parents[2] / "src/quote_ai/ingest/spark_io.py"


@pytest.fixture
def runtime() -> dict:
    """Load the retry helper and log writer with dependency-free test doubles."""
    module = ast.parse(SOURCE_PATH.read_text())
    nodes = [node for node in module.body if (
        isinstance(node, ast.FunctionDef) and node.name == "_retry_delta_write"
        or isinstance(node, ast.ClassDef) and node.name == "DeltaLog"
        or isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id.startswith("_DELTA_") for target in node.targets
        )
    )]
    namespace = {
        "time": SimpleNamespace(sleep=Mock()),
        "random": SimpleNamespace(uniform=Mock(return_value=1.0)),
        "_LOGGER": Mock(), "_RUN_COLS": ["run_id"], "_FILE_COLS": ["path"],
        "_lit": repr, "qualified": lambda value: value,
    }
    import __future__

    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE_PATH), "exec",
                 flags=__future__.annotations.compiler_flag), namespace)
    return namespace


@pytest.mark.parametrize("method", ["start", "finish"])
def test_run_log_retries_same_merge_after_conflict(runtime: dict, method: str) -> None:
    """Replay the same run-ID upsert after a concurrent append aborts it."""
    io = Mock()
    io.execute.side_effect = [RuntimeError("[DELTA_CONCURRENT_APPEND_WITHOUT_HINT] conflict"), None]
    record = Mock()
    record.model_dump.return_value = {"run_id": "stable-run"}

    getattr(runtime["DeltaLog"](io, "catalog.ops"), method)(record)

    assert io.execute.call_count == 2
    assert io.execute.call_args_list[0] == io.execute.call_args_list[1]
    runtime["time"].sleep.assert_called_once_with(0.5)


def test_manifest_retries_conflict_raised_during_collect(runtime: dict) -> None:
    """Retry the entire manifest statement when lazy result collection fails."""
    io = Mock()
    io.spark.sql.return_value.collect.side_effect = [
        RuntimeError("[DELTA_CONCURRENT_WRITE] conflict"), [SimpleNamespace(asDict=lambda: {"num_inserted_rows": 1})],
    ]
    record = Mock()
    record.model_dump.return_value = {"path": "file.csv"}

    assert runtime["DeltaLog"](io, "catalog.ops").add_files([record]) == 1
    assert io.spark.sql.call_count == 2


def test_non_conflict_errors_are_not_retried(runtime: dict) -> None:
    """Propagate syntax, permissions, and other unrelated failures immediately."""
    error = RuntimeError("[PARSE_SYNTAX_ERROR] invalid SQL")
    operation = Mock(side_effect=error)
    with pytest.raises(RuntimeError) as caught:
        runtime["_retry_delta_write"](operation)
    assert caught.value is error
    operation.assert_called_once()
    runtime["time"].sleep.assert_not_called()


def test_conflicts_stop_after_bounded_attempts(runtime: dict) -> None:
    """Propagate the final conflict when all five attempts are exhausted."""
    error = RuntimeError("conflict")
    error.getErrorClass = lambda: "DELTA_CONCURRENT_APPEND"
    operation = Mock(side_effect=error)
    with pytest.raises(RuntimeError) as caught:
        runtime["_retry_delta_write"](operation)
    assert caught.value is error
    assert operation.call_count == 5
    assert [call.args[0] for call in runtime["time"].sleep.call_args_list] == [0.5, 1.0, 2.0, 4.0]