"""A deterministic stand-in for the chat model (AI-18 onwards): unit tests, the CI suites and the local demo, with no
workspace. Like the extractive stand-in of AI-16, it is labelled as one and never ships.

It answers the OpenAI-compatible chat call the loop makes (client.chat.completions.create(model, messages, tools)):
it reads the last user message, the system prompt (the quote, the skills, the customer facts) and the tool results
already in the conversation, and either asks for the next tool call or writes the answer from the tool outputs. It
chooses only among the tools it is given, by reading their descriptions: give it two overlapping descriptions and
its choice between them becomes a coin flip (AI-19's break). Tokens are estimated (4 characters a token), so the
cost of a long prompt shows.

demo_win_model: the planted signal of the synthetic history (quote_ai.mock.catalog.win_probability) as a stand-in for
the win model endpoint, at Lakeside's $8.00 today and a 7-day lead time.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from types import SimpleNamespace

LABEL = "stand-in model (deterministic, local demo)"
_GLOVE = re.compile(r"\b(?:glove[ -])([a-r])\b", re.I)
_SIZE = re.compile(r"\bsize (xs|s|m|l|xl|xxl)\b", re.I)
_ORDINAL = {"first": 0, "second": 1, "third": 2}
SEARCH_WORDS = {"find", "gloves", "glove", "products", "candidates", "request", "search", "options"}


def demo_win_model(records: list[dict]) -> list[list[float]]:
    from quote_ai.mock.catalog import win_probability
    return [[1 - (p := win_probability(r["unit_price"], 8.00, 7, True)), p] for r in records]


def _tokens(x) -> int:
    return max(1, len(json.dumps(x, default=str)) // 4)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", (text or "").lower()))


class _Ctx:
    """What the stand-in can see: the user message, the system prompt, earlier answers and this turn's results."""

    def __init__(self, messages: list[dict], tools: list[dict]) -> None:
        self.tools = {t["function"]["name"]: t["function"] for t in tools or []}
        self.system = next((m.get("content") or "" for m in messages if m.get("role") == "system"), "")
        last_user = max(i for i, m in enumerate(messages) if m.get("role") == "user")
        self.text = messages[last_user].get("content") or ""
        self.low = self.text.lower()
        self.history = [m.get("content") or "" for m in messages[:last_user] if m.get("role") == "assistant"]
        calls: dict[str, tuple[str, dict]] = {}
        self.done: list[tuple[str, dict, dict]] = []
        for m in messages[last_user + 1:]:
            for c in m.get("tool_calls") or []:
                calls[c["id"]] = (c["function"]["name"], json.loads(c["function"]["arguments"] or "{}"))
            if m.get("role") == "tool":
                name, args = calls[m["tool_call_id"]]
                self.done.append((name, args, json.loads(m["content"])))

    def has(self, name: str) -> bool:
        return name in self.tools

    def last(self, name: str) -> dict | None:
        return next((o for n, _, o in reversed(self.done) if n == name), None)

    def all(self, name: str) -> list[dict]:
        return [o for n, _, o in self.done if n == name]

    def asked(self, name: str, args: dict) -> bool:
        return any(n == name and a == args for n, a, _ in self.done)

    def gloves(self) -> list[str]:
        return [f"GLOVE-{g.upper()}" for g in dict.fromkeys(_GLOVE.findall(self.text))]

    def quote_product(self) -> str | None:
        m = re.search(r"product (GLOVE-[A-Z])", self.system)
        return m.group(1) if m else None

    def options(self) -> list[str]:
        for h in reversed(self.history):
            m = re.search(r"Options in order: (.+)", h)
            if m:
                return re.findall(r"GLOVE-[A-Z]", m.group(1))
        return []

    def facts(self) -> dict[str, str]:
        m = re.search(r"confirms it on the quote\): (.+)", self.system)
        return dict(f.split(" = ", 1) for f in m.group(1).split("; ")) if m else {}

    def pick(self, preferred: str = "search_candidates") -> str | None:
        """The product-search tool, read from names and descriptions: every tool that says it finds or searches
        gloves or products, except one whose description says it is not for finding candidates. One such tool: it is
        chosen. Two (AI-19's overlapping find_gloves): a model can't tell them apart, so the choice is a coin flip on
        the message."""
        def stems(text: str) -> set[str]:
            return {w.rstrip("s") for w in _words(text)}
        matching = []
        for n, f in self.tools.items():
            text = n.replace("_", " ") + " " + f["description"]
            w = stems(text)
            if ({"find", "search"} & w) and ({"glove", "product"} & w) and \
                    not re.search(r"not for finding", f["description"], re.I):
                matching.append(n)
        if len(matching) > 1:
            h = int(hashlib.sha256(self.text.encode()).hexdigest(), 16)
            return sorted(matching)[h % len(matching)]
        return matching[0] if matching else (preferred if self.has(preferred) else None)


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _name(pid: str) -> str:
    return pid.replace("GLOVE-", "Glove ")


# ------------------------------------------------------------------ plans: the next step, or None to answer

def _discovery(c: _Ctx):
    """search -> eligibility -> fulfilment -> price each eligible product -> win chance of the first on time."""
    search = c.pick()
    if search and not c.last(search):
        return search, {}
    found = c.last(search) or {}
    cands = [x["product_id"] for x in found.get("candidates", [])]
    if cands and c.has("check_eligibility") and not c.last("check_eligibility"):
        return "check_eligibility", {"product_ids": cands}
    elig = [r["product_id"] for r in (c.last("check_eligibility") or {}).get("results", [])
            if r["status"] == "eligible"]
    if elig and c.has("check_fulfilment") and not c.last("check_fulfilment"):
        return "check_fulfilment", {"product_ids": elig}
    for pid in elig[:3]:
        if c.has("price_quote") and not c.asked("price_quote", {"product_id": pid}):
            return "price_quote", {"product_id": pid}
    on_time = [r["product_id"] for r in (c.last("check_fulfilment") or {}).get("results", []) if r["on_time"]]
    priced = {o["product_id"]: o for o in c.all("price_quote") if "lines" in o}
    if on_time and c.has("win_probability") and not c.last("win_probability") and on_time[0] in priced:
        return "win_probability", {"product_id": on_time[0], "prices": [priced[on_time[0]]["unit_price"]]}
    return None


def _target(c: _Ctx) -> str:
    if "issue" in c.low or "send" in c.low:
        return "ISSUED"
    return "APPROVED" if "approve" in c.low else "PENDING_APPROVAL" if "submit" in c.low else "PRICED"


def plan(c: _Ctx):
    low, gloves = c.low, c.gloves()
    product = (gloves or [None])[0] or c.quote_product() or (c.options() or [None])[0]
    if re.search(r"\b(issue|send the quote|submit|approve)\b", low):
        if c.has("propose_transition") and not c.last("propose_transition"):
            return "propose_transition", {"target": _target(c), "reason": f"The rep asked: {c.text.strip()}"}
        return None
    if re.search(r"\b(save|remember)\b|always latex", low):
        if c.has("propose_memory") and not c.last("propose_memory"):
            return "propose_memory", {"fact_key": "latex_free", "fact_value": "true"}
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", low)
    if m or "discount" in low:
        if c.has("load_skill") and not c.last("load_skill"):
            return "load_skill", {"name": "quoting-playbook"}
        if product and c.has("price_quote") and not c.last("price_quote"):
            return "price_quote", {"product_id": product}
        p = c.last("price_quote")
        if m and p and "lines" in p and not any("discount_band" in o for o in c.all("price_quote")):
            asked = round(p["list_price"] * (1 - float(m.group(1)) / 100), 2)
            return "price_quote", {"product_id": product, "asked_unit_price": asked}
        return None
    if re.search(r"bought|buying history|purchase|paid|pay last|last (quarter|year)|win rate", low):
        if c.has("ask_genie") and not c.last("ask_genie"):
            return "ask_genie", {"question": c.text}
        if "current" in low:
            return _current(c, gloves)
        return None
    if re.search(r"\bcurrent\b|\brecord\b", low) and gloves:
        return _current(c, gloves)
    if re.search(r"why|differ|explain|more expensive|thicker", low) and c.has("knowledge_search"):
        if not c.last("knowledge_search"):
            return "knowledge_search", {"question": c.text, "product_ids": gloves}
        for pid in gloves if "expensive" in low else []:
            if c.has("price_quote") and not c.asked("price_quote", {"product_id": pid}):
                return "price_quote", {"product_id": pid}
        return None
    if re.search(r"\bjust quote\b|\bquote (glove|it)\b|probably fine", low) and gloves:
        if not c.last("check_eligibility"):
            return "check_eligibility", {"product_ids": gloves}
        st = {r["product_id"]: r["status"] for r in c.last("check_eligibility")["results"]}
        if st.get(gloves[0]) == "eligible" and not c.last("price_quote"):
            return "price_quote", {"product_id": gloves[0]}
        return None
    if re.search(r"chemo|latex|powder|evidence|tested", low) and gloves:
        attr = ("chemo_tested" if "chemo" in low else "latex_free" if "latex" in low else
                "powder_free" if "powder" in low else None)
        args = {"product_id": gloves[0]} | ({"attribute": attr} if attr else {})
        if c.has("get_evidence") and not c.last("get_evidence"):
            return "get_evidence", args
        return None
    if re.search(r"out of stock|runs? out|ran out", low):
        size = (_SIZE.search(c.text) or [None, "M"])[1].upper()
        options = c.options()
        if not options:
            step = _discovery(c) if not c.last("check_fulfilment") else None
            if step and step[0] in ("search_candidates", "check_eligibility"):
                return step
            options = [r["product_id"] for r in (c.last("check_eligibility") or {}).get("results", [])
                       if r["status"] == "eligible"]
        on_quote = product if product in options else options[0]
        what_if = {"product_ids": options, "unavailable_skus": [f"{on_quote}-{size}"]}
        if c.has("check_fulfilment") and not c.asked("check_fulfilment", what_if):
            return "check_fulfilment", what_if
        if c.has("check_eligibility") and not any(set(a.get("product_ids", [])) == set(options)
                                                  for n, a, _ in c.done if n == "check_eligibility"):
            return "check_eligibility", {"product_ids": options}
        ful = {r["product_id"]: r for r in c.last("check_fulfilment")["results"]}
        for pid in [p for p in options if ful.get(p, {}).get("status") != "backorder"]:
            if c.has("price_quote") and not c.asked("price_quote", {"product_id": pid}):
                return "price_quote", {"product_id": pid}
        return None
    om = re.search(r"\b(first|second|third) option\b", low)
    if om and c.options():
        pid = c.options()[_ORDINAL[om.group(1)]]
        if c.has("check_fulfilment") and not c.last("check_fulfilment"):
            return "check_fulfilment", {"product_ids": [pid]}
        if c.has("price_quote") and not c.last("price_quote"):
            return "price_quote", {"product_id": pid}
        return None
    pm = re.search(r"\$\s?(\d+(?:\.\d+)?)", c.text)
    if pm and product:
        asked = float(pm.group(1))
        if c.has("price_quote") and not c.last("price_quote"):
            return "price_quote", {"product_id": product, "asked_unit_price": asked}
        p = c.last("price_quote") or {}
        if c.has("win_probability") and "lines" in p and not c.last("win_probability"):
            return "win_probability", {"product_id": product, "prices": sorted({asked, p["unit_price"]})}
        return None
    return _discovery(c)


def _current(c: _Ctx, gloves: list[str]):
    if not gloves:
        return None
    if c.has("get_device") and not c.last("get_device"):
        return "get_device", {"product_id": gloves[0]}
    dev = c.last("get_device") or {}
    if c.has("gudid_live_lookup") and dev.get("primary_di") and not c.last("gudid_live_lookup") \
            and dev.get("source") != "accessgudid_live":
        return "gudid_live_lookup", {"di": dev["primary_di"], "product_id": gloves[0]}
    return None


# ------------------------------------------------------------------ the answer, from the tool outputs only

def _unavailable(c: _Ctx) -> list[str]:
    seen = []
    for n, _, o in c.done:
        if "unavailable" in o and o["missing"] not in seen:
            seen.append(o["missing"])
    return [f"{m[0].upper()}{m[1:]} is unavailable right now; I won't guess it." for m in seen]


def answer(c: _Ctx) -> str:
    out: list[str] = _unavailable(c)
    for o in c.all("propose_transition"):
        if "proposal" in o:
            p = o["proposal"]
            out.append(f"I can't change the quote myself. Proposed: move {p['quote_id']} from {p['from_state']} to "
                       f"{p['target']} ({p['reason']}). Confirm it in the app; QuoteService checks the rules "
                       f"(issuing needs the price policy and, above 5%, a manager's approval).")
    for o in c.all("propose_memory"):
        if "memory_proposal" in o:
            out.append(o["memory_proposal"]["question"] + " Nothing is saved until you confirm.")
    if (g := c.last("ask_genie")) and "text" in g:
        out.append(f"Buying history (Genie): {g['text']}" + (f" [SQL: {g['sql']}]" if g.get("sql") else ""))
    for o in c.all("get_device") + c.all("gudid_live_lookup"):
        if "primary_di" in o or "brand_name" in o:
            when = ("live GUDID record, retrieved today" + (f" (version {o['public_version_number']})"
                                                            if o.get("public_version_number") else "")
                    if o.get("source") == "accessgudid_live" else f"lakehouse copy as of {o.get('as_of')}")
            dist = o.get("in_commercial_distribution", o.get("commercial_distribution"))
            out.append(f"{o.get('brand_name')} (DI {o.get('primary_di')}): "
                       f"{'in commercial distribution' if dist in (True, 'In Commercial Distribution') else 'distribution: ' + str(dist)}; "
                       f"{when}." + (f" {o['fallback']}." if o.get("fallback") else ""))
    for o in c.all("get_evidence"):
        pid, attr = o.get("product_id"), o.get("attribute") or "the attribute"
        if o.get("verified"):
            src = next(r["source_id"] for r in o["rows"] if r["verification_status"] == "verified")
            out.append(f"{_name(pid)}: {attr} is verified ({src}).")
        elif o.get("rows") is not None:
            out.append(f"{_name(pid)}: {attr} is not verified; only claims are on file, so evidence is needed "
                       f"before it can be offered for this.")
    if (k := c.last("knowledge_search")) and k.get("chunks"):
        out.append("From the documents: " + " ".join(f"{ch['text'][:160].strip()} [{ch['chunk_id']}]"
                                                     for ch in k["chunks"][:2]))
    elig = c.last("check_eligibility")
    if elig:
        for r in elig["results"]:
            if r["status"] == "eligible":
                out.append(f"{_name(r['product_id'])} is eligible: every hard requirement verified.")
            elif r["status"] == "needs_evidence":
                out.append(f"{_name(r['product_id'])} needs evidence ({', '.join(r['failed'])} unknown); I can't "
                           f"mark it as a match.")
            else:
                out.append(f"{_name(r['product_id'])} is excluded ({', '.join(r['failed'])}).")
    ful = {r["product_id"]: r for r in (c.last("check_fulfilment") or {}).get("results", [])}
    assumed = (c.last("check_fulfilment") or {}).get("assumed_out_of_stock")
    if ful:
        if assumed:
            out.append(f"If {', '.join(assumed)} ran out:")
        for pid, r in ful.items():
            rest = ("" if r["on_time"] else f", the rest on day {r['ready_day']}" if r.get("ready_day")
                    else ", no date known for the rest")
            out.append(f"{_name(pid)}: {r['label']} ({r['status']}{rest}).")
    priced = [o for o in c.all("price_quote") if "lines" in o]
    for p in {o["product_id"]: o for o in priced}.values():
        out.append(f"{_name(p['product_id'])}: {_money(p['unit_price'])} a box from the price book "
                   f"({len(p['lines'])} lines, {_money(p['total'])} in total, {p['price_book_ids'][0]}...).")
        if "discount_band" in p:
            b = p["discount_band"]
            what = {"NONE": "within the rep's limit, no approval needed",
                    "MANAGER": "a manager must approve this quote version",
                    "REFUSED": "refused by the pricing policy; there is no approval for it"}[b["approval"]]
            out.append(f"At {_money(b['unit_price'])} ({b['discount_pct']}% off {_money(b['list_price'])}): {what}.")
    for o in c.all("price_quote"):
        if "refused" in o:
            out.append(f"No price: {o['reason']}.")
    if (w := c.last("win_probability")) and "chances" in w:
        out.append("Win chance (" + w["label"] + "): " + ", ".join(
            f"{round(100 * x['win_chance'])}% at {_money(x['price'])}" for x in w["chances"]) + ".")
    if (s := c.last("load_skill")) and "instructions" in s:
        out.append("Per the DBxLab quoting playbook: I explain the band from price_quote and never promise a price.")
    order = [pid for pid in ful if ful[pid]["on_time"]] + [pid for pid in ful if not ful[pid]["on_time"]]
    if order and not assumed:
        out.append("Options in order: " + ", ".join(f"{i + 1}. {pid}" for i, pid in enumerate(order)))
    facts = c.facts()
    if facts and "requirements" not in c.low and not c.done:
        out.append("From memory (confirmed earlier): " + ", ".join(f"{k} = {v}" for k, v in facts.items())
                   + ". Confirm on the quote to make it a hard requirement.")
    return "\n".join(out) or "I found nothing to answer with; please rephrase."


def route(text: str) -> str:
    """AI-21: which specialist an open question goes to (the orchestrator's model-based route, stand-in)."""
    low = text.lower()
    if re.search(r"bought|buying|purchase|paid|pay last|last (quarter|year)|win rate|history", low):
        return "analyst"
    if re.search(r"why|explain|differ|thicker|more expensive|document|report", low):
        return "knowledge"
    return "catalog"


class StandInClient:
    """client.chat.completions.create(...) like an OpenAI-compatible client; records each call (tests)."""

    def __init__(self, planner: Callable[[_Ctx], tuple[str, dict] | None] = plan) -> None:
        self.planner = planner
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model=None, messages=(), tools=(), **kw):
        messages = list(messages)
        self.requests.append({"model": model, "tools": [t["function"]["name"] for t in tools or []]})
        c = _Ctx(messages, list(tools or []))
        step = None if c.system.startswith("ROUTER") else self.planner(c)
        if c.system.startswith("ROUTER"):                 # AI-21: the orchestrator asks for one word
            msg = SimpleNamespace(content=route(c.text), tool_calls=None)
        elif step is None:
            msg = SimpleNamespace(content=answer(c), tool_calls=None)
        else:
            name, args = step
            n = sum(len(m.get("tool_calls") or []) for m in messages) + 1
            msg = SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
                id=f"call-{n}", type="function",
                function=SimpleNamespace(name=name, arguments=json.dumps(args)))])
        prompt = _tokens(messages) + _tokens(list(tools or []))
        completion = _tokens(msg.content or [c.function.arguments for c in msg.tool_calls])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion))
