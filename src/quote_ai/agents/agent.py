"""QuoteAgent: the loop by hand on the Databricks agent framework, as an MLflow ResponsesAgent (AI-18).

The framework wraps the loop; it does not change what the loop does. predict runs the same loop with the same tools
(quote_ai.agents.loop_by_hand.run_stream) and returns the answer as a response item; predict_stream yields an event
per tool call and per piece of text, so the app shows progress as it happens. A standard interface that serving, the
Agent Runtime, the app and mlflow.genai.evaluate all understand.

AI-19: the tools also come from managed MCP servers (quote_ai.agents.mcp) when QUOTE_AI_MCP=1, and the quoting
playbook is an Agent Skill, listed in the system prompt and loaded on demand.
AI-20: the agent is stateless and deployed on the Agent Runtime (quote_ai.agents.server). The app loads the context
from Lakebase (quote_ai.agents.memory) and sends it with each request in custom_inputs: the quote, the rep, the
confirmed customer facts; the conversation is the request's input.

events() is the plain-Python door (the app in-process, the tests); predict / predict_stream are the MLflow door.
Logged with mlflow.pyfunc.log_model (models from code: this file) and registered as <catalog>.quote_ai_ai.quote_agent
by the quote_ai_agent_registration job (quote_ai.jobs.agent_job).

Verify before recording: ResponsesAgent, ResponsesAgentRequest.input / custom_inputs, create_text_output_item,
create_function_call_item, create_function_call_output_item, create_text_delta and ResponsesAgentStreamEvent in the
installed MLflow 3; mlflow.models.set_model for models from code.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from quote_ai.agents.loop_by_hand import MAX_STEPS, run_stream, system_prompt
from quote_ai.agents.tools import Toolbox

try:
    import mlflow
    from mlflow.pyfunc import ResponsesAgent
    from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse, ResponsesAgentStreamEvent
    _MLFLOW = True
except ImportError:          # unit tests and CI: the plain-Python door still works
    ResponsesAgent = object  # type: ignore[assignment,misc]
    _MLFLOW = False


def _text(content: Any) -> str:
    """A message's text: a string, or a list of content parts ({"type": "input_text", "text": ...})."""
    if isinstance(content, str):
        return content
    parts = []
    for p in content or []:
        p = p if isinstance(p, dict) else getattr(p, "model_dump", lambda: {})()
        parts.append(p.get("text") or "")
    return "".join(parts)


def messages_from(items: list) -> list[dict]:
    """Responses-style input items -> chat messages (user and assistant text only)."""
    out = []
    for it in items or []:
        d = it if isinstance(it, dict) else it.model_dump()
        if d.get("role") in ("user", "assistant"):
            out.append({"role": d["role"], "content": _text(d.get("content"))})
    return out


class QuoteAgent(ResponsesAgent):
    def __init__(self, deps=None, client=None, endpoint: str | None = None, max_steps: int = MAX_STEPS,
                 tools: list[str] | None = None, prompt: str | None = None) -> None:
        """tools: a subset of the tool names (a specialist, AI-21); prompt: a role prepended to the system prompt."""
        self._deps, self._client, self._endpoint = deps, client, endpoint
        self.max_steps, self.tools, self.prompt = max_steps, tools, prompt

    @property
    def deps(self):
        if self._deps is None:                      # built on first use: importing the agent needs no workspace
            from quote_ai.agents import runtime
            self._deps = runtime.deps_from_env()
        return self._deps

    def _model(self) -> tuple[Any, str]:
        if self._client is None:
            from quote_ai.agents import runtime
            self._client, self._endpoint = runtime.model_client()
        return self._client, self._endpoint or "quote_ai_chat"

    def events(self, messages: list[dict], quote=None, rep: str | None = None,
               customer_facts: list[dict] | None = None, retry: dict | None = None) -> Iterator[dict]:
        """The plain events of one turn (step, tool_result, proposal, memory_proposal, token, done)."""
        toolbox = self._toolbox(quote, rep)
        skills = self.deps.skills.list() if self.deps.skills is not None and "load_skill" in toolbox.names else None
        system = system_prompt(quote, skills, customer_facts)
        if self.prompt:
            system = f"{self.prompt}\n{system}"
        client, endpoint = self._model()
        yield from run_stream(messages, toolbox, client, endpoint, self.max_steps, partial=True, system=system,
                              retry=retry)

    def _toolbox(self, quote, rep):
        local = Toolbox(self.deps, quote, rep, names=self.tools)
        import os
        if os.environ.get("QUOTE_AI_MCP") == "1":   # AI-19: discovered, not hand-wired
            from quote_ai.agents import mcp
            return mcp.CompositeToolbox(local, mcp.McpTools.from_env())
        return local

    # ------------------------------------------------------------ the MLflow door

    def _from_request(self, request) -> Iterator[dict]:
        from quote_ai.domain.models import Quote
        ci = getattr(request, "custom_inputs", None) or {}
        quote = Quote.model_validate(ci["quote"]) if ci.get("quote") else None
        return self.events(messages_from(request.input), quote, ci.get("rep"), ci.get("customer_facts"))

    def predict(self, request: "ResponsesAgentRequest") -> "ResponsesAgentResponse":
        events = list(self._from_request(request))
        done = events[-1]
        return ResponsesAgentResponse(
            output=[self.create_text_output_item(text=done["answer"], id="msg-1")],
            custom_outputs={k: done[k] for k in ("stop_reason", "usage", "violations", "facts")}
            | {"proposals": [e["proposal"] for e in events if e["type"] == "proposal"]})

    def predict_stream(self, request: "ResponsesAgentRequest") -> Iterator["ResponsesAgentStreamEvent"]:
        for e in self._from_request(request):
            yield from stream_events(self, e)


def stream_events(agent: QuoteAgent, e: dict) -> Iterator[Any]:
    """One plain event as Responses stream events. The one adapter to check against the MLflow docs."""
    if e["type"] == "step":
        yield ResponsesAgentStreamEvent(type="response.output_item.done", item=agent.create_function_call_item(
            id=e["call_id"], call_id=e["call_id"], name=e["name"], arguments=json.dumps(e["args"])))
    elif e["type"] == "tool_result":
        yield ResponsesAgentStreamEvent(type="response.output_item.done", item=agent.create_function_call_output_item(
            call_id=e["call_id"], output=json.dumps(e["output"], default=str)))
    elif e["type"] == "token":
        yield ResponsesAgentStreamEvent(**agent.create_text_delta(delta=e["text"], item_id="msg-1"))
    elif e["type"] == "done":
        yield ResponsesAgentStreamEvent(type="response.output_item.done",
                                        item=agent.create_text_output_item(text=e["answer"], id="msg-1"))


if _MLFLOW:
    mlflow.models.set_model(QuoteAgent())
