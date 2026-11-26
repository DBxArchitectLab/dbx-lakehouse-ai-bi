"""Least privilege on the tools (AI-19): the MCP servers are scoped to the ai schema, a call the agent's identity has
no grant for comes back PERMISSION_DENIED and is never retried, and the agent-only tools stay local."""
from types import SimpleNamespace

from agent_fixtures import confirmed, toolbox, user
from quote_ai.agents import mcp
from quote_ai.agents.loop_by_hand import run
from quote_ai.agents.reliability import Turn
from agent_fixtures import ScriptedClient


class FakeServer:
    """A managed MCP server: lists tools; a call outside the agent's grants is refused by the platform."""

    def __init__(self, tools, denied=()):
        self._tools, self.denied, self.calls = tools, set(denied), []

    def list_tools(self):
        return [SimpleNamespace(name=n, description=d, inputSchema={"type": "object", "properties": {}})
                for n, d in self._tools]

    def call_tool(self, name, args):
        self.calls.append(name)
        if name in self.denied:
            raise RuntimeError(f"PERMISSION_DENIED: User does not have EXECUTE on Routine '{name}'")
        return SimpleNamespace(content=[SimpleNamespace(text='{"ok": true}')])


def test_the_servers_are_scoped_to_the_ai_schema():
    urls = mcp.server_urls("https://adb-1.azuredatabricks.net/", "dbxarchitectlab_dev", "quote_ai_ai", "01ef")
    assert urls == {"vector_search": "https://adb-1.azuredatabricks.net/api/2.0/mcp/vector-search/dbxarchitectlab_dev/quote_ai_ai",
                    "functions": "https://adb-1.azuredatabricks.net/api/2.0/mcp/functions/dbxarchitectlab_dev/quote_ai_ai",
                    "genie": "https://adb-1.azuredatabricks.net/api/2.0/mcp/genie/01ef"}


def test_tools_are_discovered_not_hand_wired():
    fns = FakeServer([("dbxarchitectlab_dev__quote_ai_ai__check_eligibility", "Deterministic hard requirement checks"),
                      ("dbxarchitectlab_dev__quote_ai_ai__price_quote", "Prices every quote line")])
    genie = FakeServer([("query_space_01ef", "Ask the DBxLab sales analyst")])
    tools = mcp.McpTools({"functions": fns, "genie": genie})
    assert sorted(tools.tools) == ["check_eligibility", "price_quote", "query_space_01ef"]
    assert {s["function"]["name"] for s in tools.specs()} == set(tools.tools)
    out = tools.run("price_quote", {"product_id": "GLOVE-A"})
    assert out == {"ok": True, "source": "rules", "via": "mcp:functions"}


def test_a_call_outside_the_grants_is_permission_denied_and_not_retried():
    fns = FakeServer([("cat__quote_ai_sales__read_price_book_cost", "Reads the price book with costs")],
                     denied={"cat__quote_ai_sales__read_price_book_cost"})
    tools = mcp.McpTools({"functions": fns})
    out, how = Turn().call("read_price_book_cost", {}, tools.run, sleep=lambda s: None)
    assert how == "refused" and out["error"] == "PERMISSION_DENIED" and len(fns.calls) == 1


def test_the_trace_shows_the_refusal_and_the_answer_has_no_cost():
    _, q = confirmed()
    fns = FakeServer([("cat__quote_ai_sales__read_price_book_cost", "Reads the price book with costs")],
                     denied={"cat__quote_ai_sales__read_price_book_cost"})
    box = mcp.CompositeToolbox(toolbox(q), mcp.McpTools({"functions": fns}))
    script = ScriptedClient([[("read_price_book_cost", {})], "I can't read the cost column: access is refused."])
    out = run([user("Read the cost column of the price book")], box, script, "test")
    result = next(e for e in out.events if e["type"] == "tool_result")
    assert result["source"] == "refused" and result["output"]["error"] == "PERMISSION_DENIED"
    assert "$" not in out.answer


def test_agent_only_tools_stay_local_and_write_nothing():
    svc, q = confirmed()
    fns = FakeServer([("cat__quote_ai_ai__propose_transition", "a server must not own proposals")])
    box = mcp.CompositeToolbox(toolbox(q), mcp.McpTools({"functions": fns}))
    out = box.run("propose_transition", {"target": "CANCELLED", "reason": "test"})
    assert out["source"] == "proposal" and fns.calls == []
    assert svc.repo.get(q.quote_id).state == q.state
