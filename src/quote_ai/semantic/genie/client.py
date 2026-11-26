"""Ask the DBxLab sales analyst a question (AI-11): the Conversation API, for the app's Ask the data panel, the genie
benchmark suite and, from AI-19, the quote agent's tool.

  ask(space_id, question)    a new conversation; waits for the answer; returns text, SQL, columns and rows
  answer_from(message, rows) the answer out of a Genie message (plain code, unit-tested with a fake message)

Verify before recording: start_conversation_and_wait and get_message_attachment_query_result in the installed
databricks-sdk, and the shape of statement_response (manifest.schema.columns, result.data_array).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

MAX_ROWS = 200


def answer_from(message: Any, rows_for: Callable[[str], tuple[list[str], list[list]]] | None = None) -> dict:
    """{"text", "sql", "description", "columns", "rows", "status"} from a GenieMessage. rows_for(attachment_id) fetches
    the query result of an attachment that has a query."""
    text, sql, description, columns, rows = [], None, None, [], []
    for a in getattr(message, "attachments", None) or []:
        if getattr(a, "text", None) is not None and a.text.content:
            text.append(a.text.content)
        q = getattr(a, "query", None)
        if q is not None and q.query:
            sql, description = q.query, q.description
            if rows_for is not None:
                columns, rows = rows_for(a.attachment_id)
    status = getattr(message, "status", None)
    return {"text": "\n\n".join(text) or description or "", "sql": sql, "description": description,
            "columns": columns, "rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS,
            "status": getattr(status, "value", status), "conversation_id": getattr(message, "conversation_id", None)}


def _rows(w, space_id: str, message) -> Callable[[str], tuple[list[str], list[list]]]:
    def fetch(attachment_id: str):
        r = w.genie.get_message_attachment_query_result(space_id, message.conversation_id, message.message_id,
                                                        attachment_id)
        st = r.statement_response
        cols = [c.name for c in (st.manifest.schema.columns or [])] if st and st.manifest else []
        data = (st.result.data_array or []) if st and st.result else []
        return cols, [list(row) for row in data]
    return fetch


def ask(space_id: str, question: str, client=None, timeout_s: int = 120) -> dict:
    """One question in a new conversation, as the identity of `client` (the app's service principal until AI-24)."""
    if client is None:
        from databricks.sdk import WorkspaceClient
        client = WorkspaceClient()
    msg = client.genie.start_conversation_and_wait(space_id, question, timeout=timedelta(seconds=timeout_s))
    return answer_from(msg, _rows(client, space_id, msg))
