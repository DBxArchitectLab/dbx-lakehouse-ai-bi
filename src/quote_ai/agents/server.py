"""QuoteAgent on the Agent Runtime on Databricks Apps (AI-20): its own app (quote-ai-agent-<target>), its own service
principal, scaling and MLflow tracing. Model Serving is the legacy route for agents and is not used.

The agent is stateless: the QuoteAI app loads the context from Lakebase (the quote, the conversation, the confirmed
customer facts: quote_ai.agents.memory) and sends it with every request; the agent never writes a quote.

  POST /responses          the Responses-style interface (QuoteAgent.predict / predict_stream, MLflow ResponsesAgent):
                           evaluation and any standard client. stream=true -> server-sent events.
  POST /api/agent/events   the QuoteAI app's stream: the agent's plain events (step, tool_result, proposal,
                           memory_proposal, token, done), one server-sent event each
  GET  /api/health

RemoteAgent is the QuoteAI app's side: the same .events(...) as an in-process QuoteAgent, over HTTP, with the app's
service principal's OAuth token (it has CAN_USE on the agent app).
Verify before recording: the Agent Runtime's server contract on Databricks Apps (the /responses route, or MLflow's
agent server), app-to-app calls with an OAuth token (WorkspaceClient().config.authenticate()), and the app URL
(w.apps.get(name).url).
"""
# No "from __future__ import annotations": FastAPI reads the route annotations of create_app's local classes.
import json
import os
from collections.abc import Iterator
from typing import Any

AGENT_PREFIX = "quote-ai-agent-"


def create_app(agent=None):
    from fastapi import FastAPI, Request
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel

    from quote_ai.agents.agent import QuoteAgent
    from quote_ai.domain.models import Quote

    app = FastAPI(title="QuoteAI agent", description="QuoteAgent on the Agent Runtime (AI-20)")
    app.state.agent = agent

    def the_agent() -> QuoteAgent:
        if app.state.agent is None:
            app.state.agent = QuoteAgent()
        return app.state.agent

    class EventsIn(BaseModel):
        messages: list[dict]
        quote: dict | None = None
        rep: str | None = None
        customer_facts: list[dict] | None = None

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/agent/events")
    def events(body: EventsIn):
        quote = Quote.model_validate(body.quote) if body.quote else None

        def stream() -> Iterator[str]:
            for e in the_agent().events(body.messages, quote, body.rep, body.customer_facts):
                yield f"event: {e['type']}\ndata: {json.dumps(e, default=str)}\n\n"
        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/responses")
    async def responses(request: Request):
        from mlflow.types.responses import ResponsesAgentRequest
        payload = await request.json()
        stream = bool(payload.pop("stream", False))
        req = ResponsesAgentRequest(**payload)
        agent = the_agent()
        if not stream:
            return agent.predict(req).model_dump(exclude_none=True)

        def sse() -> Iterator[str]:
            for ev in agent.predict_stream(req):
                yield f"data: {ev.model_dump_json(exclude_none=True)}\n\n"
        return StreamingResponse(sse(), media_type="text/event-stream")

    return app


def parse_sse(lines: Iterator[str]) -> Iterator[dict]:
    """Server-sent events (event:/data: lines, a blank line ends one) -> the data of each, as a dict."""
    data: list[str] = []
    for raw in lines:
        line = raw.decode() if isinstance(raw, bytes) else raw
        line = line.rstrip("\r\n")
        if not line:
            if data:
                yield json.loads("\n".join(data))
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield json.loads("\n".join(data))


class RemoteAgent:
    """The QuoteAI app's client of the agent app. post(url, json, headers) -> an iterable of SSE lines (tests pass
    one; by default requests with stream=True)."""

    def __init__(self, url: str, headers: Any = None, post: Any = None, timeout_s: float = 120.0) -> None:
        self.url, self.timeout_s = url.rstrip("/"), timeout_s
        self._headers, self._post = headers, post

    @classmethod
    def from_env(cls) -> "RemoteAgent":
        url = os.environ.get("QUOTE_AI_AGENT_URL")
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        if not url:
            app = os.environ.get("QUOTE_AI_AGENT_APP") or agent_app_name(os.environ.get("DATABRICKS_APP_NAME", ""))
            url = w.apps.get(app).url
        return cls(url, headers=lambda: w.config.authenticate())

    def _lines(self, body: dict) -> Iterator[str]:
        headers = (self._headers() if callable(self._headers) else self._headers) or {}
        if self._post is not None:
            yield from self._post(self.url + "/api/agent/events", body, headers)
            return
        import requests
        with requests.post(self.url + "/api/agent/events", json=body, headers=headers, stream=True,
                           timeout=self.timeout_s) as r:
            r.raise_for_status()
            yield from r.iter_lines(decode_unicode=True)

    def events(self, messages: list[dict], quote=None, rep: str | None = None,
               customer_facts: list[dict] | None = None) -> Iterator[dict]:
        body = {"messages": messages, "quote": quote.model_dump(mode="json") if quote is not None else None,
                "rep": rep, "customer_facts": [{k: f[k] for k in ("fact_key", "fact_value")}
                                               for f in customer_facts or []]}
        yield from parse_sse(self._lines(body))


def agent_app_name(app_name: str) -> str:
    """quote-ai-dev -> quote-ai-agent-dev: the agent app of the same target."""
    if not app_name.startswith("quote-ai-") or app_name.startswith(AGENT_PREFIX):
        raise ValueError(f"not the QuoteAI app's name: {app_name!r} (set QUOTE_AI_AGENT_URL or QUOTE_AI_AGENT_APP)")
    return AGENT_PREFIX + app_name[len("quote-ai-"):]
