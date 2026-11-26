"""The agent never overrides a rule, and never commits (AI-18). Enforced in code, checked in tests.

  check_answer     every price in the answer must be a number a tool returned (or one the customer stated), and a
                   product called eligible must be eligible in check_eligibility's output. A violation withholds the
                   model's text: the rep sees the tool results instead.
  facts            the rule results of a run (eligibility per product, price per product), from the tool outputs:
                   what the app shows next to the answer
  proposals        the transitions the agent proposed (written nowhere)
  writes_in_trace  whether any step called a tool that writes (WRITE_TOOLS is empty: always False for QuoteAgent;
                   the test agent given a write tool shows the failing design)
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from quote_ai.agents.tools import WRITE_TOOLS

_PRICE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)")
_GLOVE = re.compile(r"\b(?:Glove |GLOVE-)([A-Z])\b")
PRICE_KEYS = {"unit_price", "list_price", "total", "price", "floor", "asked_unit_price"}
_NOT_ELIGIBLE = re.compile(r"not eligible|ineligible|isn't eligible|is not eligible|needs evidence|not .{0,20}eligible"
                           r"|cannot mark .{0,30}eligible|can't mark .{0,30}eligible", re.I)


def prices_in(text: str) -> list[float]:
    return [float(m.replace(",", "")) for m in _PRICE.findall(text or "")]


def _walk(x, out: set[float], take_all: bool = False) -> None:
    if isinstance(x, Mapping):
        for k, v in x.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and (take_all or k in PRICE_KEYS):
                out.add(round(float(v), 2))
            else:
                _walk(v, out, take_all or k == "rows")
    elif isinstance(x, (list, tuple)):
        for v in x:
            if take_all and isinstance(v, (int, float)) and not isinstance(v, bool):
                out.add(round(float(v), 2))
            elif take_all and isinstance(v, str):
                try:
                    out.add(round(float(v), 2))
                except ValueError:
                    pass
            else:
                _walk(v, out, take_all)


def allowed_prices(outputs: Iterable[Mapping]) -> set[float]:
    """Every price-like number a tool returned (Genie's result rows count: they are data, with their SQL)."""
    out: set[float] = set()
    for o in outputs:
        _walk(o, out)
    return out


def statuses(outputs: Iterable[Mapping]) -> dict[str, str]:
    st: dict[str, str] = {}
    for o in outputs:
        for r in o.get("results", []) if isinstance(o, Mapping) else []:
            if "status" in r and "checks" in r:
                st[r["product_id"]] = r["status"]
    return st


def check_answer(answer: str, outputs: list[Mapping], user_texts: Iterable[str] = ()) -> list[str]:
    """The violations of one answer against the tool outputs of its run; [] means it may be shown."""
    allowed = allowed_prices(outputs) | {round(p, 2) for t in user_texts for p in prices_in(t)}
    problems = [f"price ${p:.2f} is not a number any tool returned" for p in prices_in(answer)
                if not any(abs(p - a) < 0.005 for a in allowed)]
    st = statuses(outputs)
    for sentence in re.split(r"(?<=[.!?])\s+|\n", answer or ""):
        if re.search(r"\beligible\b", sentence, re.I) and not _NOT_ELIGIBLE.search(sentence):
            for letter in _GLOVE.findall(sentence):
                pid = f"GLOVE-{letter}"
                if st.get(pid) != "eligible":
                    problems.append(f"{pid} is called eligible, but check_eligibility says {st.get(pid, 'nothing')}")
    return problems


def facts(outputs: list[tuple[str, dict, dict]]) -> dict:
    """(tool, args, output) of a run -> the rule results the app shows beside the answer."""
    elig, prices, sources = {}, {}, set()
    for name, _args, out in outputs:
        sources.add(out.get("source") or ("unavailable" if "unavailable" in out else "none"))
        if name == "check_eligibility":
            elig |= {r["product_id"]: r["status"] for r in out.get("results", [])}
        if name == "price_quote" and "lines" in out:
            prices[out["product_id"]] = {"unit_price": out["unit_price"], "total": out["total"],
                                         "price_book_ids": out["price_book_ids"]}
    return {"eligibility": elig, "prices": prices, "sources": sorted(sources)}


def withheld(violations: list[str], fx: dict) -> str:
    lines = ["The draft answer contradicted the rules, so it is not shown. From the tools:"]
    lines += [f"- {pid}: {st}" for pid, st in sorted(fx["eligibility"].items())]
    lines += [f"- {pid}: ${p['unit_price']:.2f} a box, ${p['total']:,.2f} in total ({', '.join(p['price_book_ids'])})"
              for pid, p in sorted(fx["prices"].items()) if p["unit_price"] is not None]
    lines.append("Not shown: " + "; ".join(violations))
    return "\n".join(lines)


def proposals(events: Iterable[Mapping]) -> list[dict]:
    return [e["proposal"] for e in events if e.get("type") == "proposal"]


def writes_in_trace(events: Iterable[Mapping], write_tools: Iterable[str] = WRITE_TOOLS) -> bool:
    w = set(write_tools)
    return any(e.get("type") == "step" and e.get("name") in w for e in events)
