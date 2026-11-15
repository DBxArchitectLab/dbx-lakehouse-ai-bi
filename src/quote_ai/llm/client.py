"""The first model client (AI-01): one chat call, measured.

It returns the answer, the token usage the endpoint reports (resp.usage), and the elapsed seconds timed around the
call: the baseline AI-04 compares models against. Today it calls a pay-per-token Foundation Model API endpoint
directly; from AI-06 the same function goes through Unity Gateway.

Needs the openai package (WorkspaceClient.serving_endpoints.get_open_ai_client builds an OpenAI-compatible client).
"""
from __future__ import annotations

import time
from typing import Any, NamedTuple

from databricks.sdk import WorkspaceClient


class Reply(NamedTuple):
    text: str
    usage: Any          # prompt_tokens, completion_tokens, total_tokens, as reported by the endpoint
    seconds: float


def chat(prompt: str, endpoint: str, system: str | None = None, temperature: float = 0.0,
         client: Any = None) -> Reply:
    """One chat completion. `client` is for tests; by default an OpenAI-compatible client of this workspace."""
    if client is None:
        client = WorkspaceClient().serving_endpoints.get_open_ai_client()
    messages = [{"role": "user", "content": prompt}]
    if system:
        messages.insert(0, {"role": "system", "content": system})
    start = time.perf_counter()
    resp = client.chat.completions.create(model=endpoint, messages=messages, temperature=temperature)
    secs = time.perf_counter() - start
    return Reply(resp.choices[0].message.content, resp.usage, secs)
