"""Tools the standard way (AI-19): Databricks managed MCP servers. One integration per capability instead of one per
agent: the agent discovers the tools a server lists, with their schemas, and calls them. Nothing hand-wired.

  genie          /api/2.0/mcp/genie/{space_id}                       the DBxLab sales analyst (AI-11)
  vector_search  /api/2.0/mcp/vector-search/{catalog}/{ai schema}    the AI Search indexes in quote_ai_ai (AI-15, 16)
  functions      /api/2.0/mcp/functions/{catalog}/{ai schema}        our rule tools (AI-18) and gudid_live_lookup

MCP adds no security by itself: what the agent may call is decided by its identity's grants (EXECUTE on the
functions in quote_ai_ai only; SELECT through Genie on the sales metric views only). A refused call comes back as
PERMISSION_DENIED and is never retried (reliability.ToolRefused). The agent-only tools (propose_transition,
propose_memory, load_skill) stay local: they write nothing and belong to no server (CompositeToolbox).

Verify before recording: the managed MCP server URLs, the databricks-mcp package (DatabricksMCPClient(server_url,
workspace_client), list_tools(), call_tool(name, arguments)), the tool names it reports for UC functions, and Genie
MCP availability in the Azure region. An external MCP server through a UC connection is the alternative route for
AccessGUDID; this build uses the UC function over the HTTP connection (gudid_live_lookup.sql).
"""
from __future__ import annotations

import json
import os
from typing import Any

from quote_ai.agents.reliability import ServiceUnavailable, ToolRefused

SOURCES = {"genie": "genie", "vector_search": "ai_search", "functions": "rules"}
LOCAL_ONLY = ("propose_transition", "propose_memory", "load_skill")


def server_urls(host: str, catalog: str, ai_schema: str, genie_space_id: str | None = None) -> dict[str, str]:
    """ai_schema: the physical name of quote_ai_ai (a personal dev copy prefixes it); the servers are scoped to it."""
    host = host.rstrip("/")
    urls = {"vector_search": f"{host}/api/2.0/mcp/vector-search/{catalog}/{ai_schema}",
            "functions": f"{host}/api/2.0/mcp/functions/{catalog}/{ai_schema}"}
    if genie_space_id:
        urls["genie"] = f"{host}/api/2.0/mcp/genie/{genie_space_id}"
    return urls


def mcp_servers(host: str, catalog: str, ai_schema: str, genie_space_id: str | None = None,
                workspace_client: Any = None) -> dict[str, Any]:
    from databricks.sdk import WorkspaceClient
    from databricks_mcp import DatabricksMCPClient
    w = workspace_client or WorkspaceClient()
    return {kind: DatabricksMCPClient(server_url=url, workspace_client=w)
            for kind, url in server_urls(host, catalog, ai_schema, genie_space_id).items()}


def tool_name(raw: str) -> str:
    """A UC function reported as catalog__schema__name (or catalog.schema.name) -> name: what the specs, the trace
    and the badges use."""
    return raw.replace(".", "__").split("__")[-1]


def discover_tools(servers: dict[str, Any]) -> list[tuple[str, Any]]:
    """(server kind, tool) for every tool every server lists."""
    return [(kind, t) for kind, s in servers.items() for t in s.list_tools()]


def to_spec(tool: Any) -> dict:
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {"type": "object",
                                                                                          "properties": {}}
    return {"type": "function", "function": {"name": tool_name(tool.name), "description": tool.description or "",
                                             "parameters": schema}}


def _content(result: Any) -> Any:
    """An MCP call result -> JSON if the text is JSON, else the text."""
    parts = [getattr(c, "text", None) or "" for c in getattr(result, "content", None) or []]
    text = "".join(parts)
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {"text": text}


class McpTools:
    """The tools the managed MCP servers list, callable by their short names."""

    def __init__(self, servers: dict[str, Any]) -> None:
        self.servers = servers
        self.tools: dict[str, tuple[str, Any]] = {}
        for kind, t in discover_tools(servers):
            self.tools[tool_name(t.name)] = (kind, t)

    @classmethod
    def from_env(cls) -> McpTools:
        from databricks.sdk import WorkspaceClient
        from quote_ai.agents.runtime import _schemas
        w = WorkspaceClient()
        names = _schemas()
        catalog, ai = names["ai"].split(".")
        return cls(mcp_servers(w.config.host, catalog, ai, os.environ.get("QUOTE_AI_GENIE_SPACE_ID"), w))

    def specs(self) -> list[dict]:
        return [to_spec(t) for _, t in self.tools.values()]

    def run(self, name: str, args: dict) -> dict:
        kind, t = self.tools[name]
        try:
            result = self.servers[kind].call_tool(t.name, args)
        except Exception as e:  # noqa: BLE001  the server's error text decides refused or transient
            msg = str(e)
            if "PERMISSION_DENIED" in msg or "does not have" in msg.lower():
                raise ToolRefused(msg) from e
            raise ServiceUnavailable(f"{kind} MCP: {msg}") from e
        out = _content(result)
        out = out if isinstance(out, dict) else {"result": out}
        return out | {"source": out.get("source", SOURCES[kind]), "via": f"mcp:{kind}"}


class CompositeToolbox:
    """The discovered MCP tools plus the agent's local ones; a name both offer goes to MCP (governed by grants)."""

    def __init__(self, local, mcp: McpTools) -> None:
        self.local, self.mcp = local, mcp
        self.quote, self.deps = local.quote, local.deps
        self.names = list(dict.fromkeys(list(mcp.tools) + [n for n in local.names if n in LOCAL_ONLY or
                                                            n not in mcp.tools]))

    def specs(self) -> list[dict]:
        by = {s["function"]["name"]: s for s in self.mcp.specs()}
        return [by[n] if n in by else next(s for s in self.local.specs() if s["function"]["name"] == n)
                for n in self.names]

    def run(self, name: str, args: dict) -> dict:
        if name in self.mcp.tools and name not in LOCAL_ONLY:
            return self.mcp.run(name, args)
        return self.local.run(name, args)
