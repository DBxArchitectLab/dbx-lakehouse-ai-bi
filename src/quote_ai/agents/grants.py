"""Per-tool grants (AI-24): each specialist may EXECUTE only the tool functions its job needs.

config/quote_ai/tool_grants.yml says who may run what; the specialists' code says what each uses (TOOLS). Both must
agree, and an exclusive tool (price_quote) belongs to one specialist only. findings() lists every difference: the data
authorization suite's sec-05 counts them (zero tolerance), and the quote_ai_security_setup job grants from the same
file, so a grant added on purpose in a pull request turns the suite red before it reaches Unity Catalog.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GRANTS_YML = ROOT / "config" / "quote_ai" / "tool_grants.yml"
LOCAL_TOOLS = {"knowledge_search", "ask_genie", "load_skill", "propose_transition", "propose_memory"}   # not UC functions


def load(path: Path | None = None) -> dict:
    import yaml
    return yaml.safe_load((path or GRANTS_YML).read_text(encoding="utf-8"))


def code_tools() -> dict[str, set[str]]:
    """What each specialist's code may call (its TOOLS), the UC functions among them."""
    from quote_ai.agents.specialists import analyst, catalog, knowledge
    return {"catalog": set(catalog.TOOLS) - LOCAL_TOOLS, "knowledge": set(knowledge.TOOLS) - LOCAL_TOOLS,
            "analyst": set(analyst.TOOLS) - LOCAL_TOOLS, "pricing": {"price_quote", "win_probability"}}


def findings(grants: dict | None = None, uses: dict[str, set[str]] | None = None) -> list[str]:
    """Every grant beyond least privilege: a tool granted that the specialist's code does not use, a tool its code
    uses without a grant, an exclusive tool held by anyone else."""
    g = grants or load()
    uses = uses or code_tools()
    out = []
    for name, tools in g["specialists"].items():
        extra = set(tools) - uses.get(name, set())
        out += [f"{name} is granted {t}, which its code does not use" for t in sorted(extra)]
        missing = uses.get(name, set()) - set(tools)
        out += [f"{name} uses {t} without a grant" for t in sorted(missing)]
        for t in tools:
            owner = g.get("exclusive", {}).get(t)
            if owner and owner != name:
                out.append(f"{name} holds {t}, which only {owner} may run")
    return sorted(set(out))


def statements(ai_schema: str, principals: dict[str, str], grants: dict | None = None) -> list[str]:
    """GRANT EXECUTE per specialist principal ({specialist: principal}); a specialist without its own principal is
    left out (its tools reach it through the agent's identity, agent_job grants)."""
    g = grants or load()
    out = []
    for name, tools in g["specialists"].items():
        who = principals.get(name)
        if not who:
            continue
        if not all(c.isalnum() or c in "-_ ." for c in who):
            raise ValueError(f"not a principal name: {who!r}")
        out += [f"GRANT EXECUTE ON FUNCTION {ai_schema}.{t} TO `{who}`" for t in tools]
    return out
