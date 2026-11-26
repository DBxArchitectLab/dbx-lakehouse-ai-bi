"""The agent's tools: what the model reads (TOOL_SPECS) and what runs (Toolbox). AI-18, extended in AI-19 and AI-20.

A tool spec is a name, a description and typed parameters. The description is context: it decides which tool the
model picks, so each one says what the tool is for and what it is not for. Every executor calls code that already
exists and is tested on its own; a tool decides nothing new:

  search_candidates  quote_ai.search.retriever (AI Search, AI-15): candidates only, never a decision
  check_eligibility  the eligibility engine (quote_ai.rules.eligibility; quote_ai_ai.check_eligibility, AI-15)
  get_evidence       the evidence rows behind one attribute (AI-08, AI-09)
  get_device         the GUDID record: the lakehouse copy (AI-18); from AI-20 the live record first, 3 s, and the
                     copy as a labelled fallback
  check_fulfilment   quote_ai.rules.fulfilment (AI-15), with a what-if: sizes assumed out of stock
  price_quote        quote_ai.rules.pricing.price_quote (AI-18): every line from the price book
  win_probability    the win model (AI-14), at the prices asked; informs, never sets a price
  propose_transition writes nothing: a proposal the rep confirms in the app; QuoteService commits it (AI-18)
  gudid_live_lookup  AccessGUDID v3 for ONE device, cached a day per DI (AI-19; the copy as fallback from AI-20)
  ask_genie          the DBxLab sales analyst Genie Agent (AI-11, through MCP in the workspace, AI-19)
  knowledge_search   the knowledge index (AI-16): documents and explanations, filtered to the rep's accounts
  load_skill         an Agent Skill, loaded on demand (the quoting playbook, AI-19)
  propose_memory     writes nothing: a customer fact the rep may confirm (AI-20)

Every result carries "source" (rules, lakehouse_copy, accessgudid_live, genie, ai_search, skill, model, proposal):
the app's source badges (AI-19) come from it, never from the model's text. A rule's refusal (a PricingError, a
product not offered) is returned as an answer ({"refused": ...}); transient failures raise, for reliability.py.
No tool writes a quote, now or later: WRITE_TOOLS is empty, and a test keeps it so.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from quote_ai.domain.models import Eligibility, Quote, QuoteLine, QuoteState
from quote_ai.rules import fulfilment, pricing
from quote_ai.rules.policy import Policy

WRITE_TOOLS: frozenset[str] = frozenset()      # tools that change a quote: none, by design
STATES = [s.value for s in QuoteState]


def spec(name: str, description: str, properties: dict, required: list[str]) -> dict:
    """An OpenAI-compatible function tool spec (what Unity Gateway's chat endpoints accept)."""
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required,
                       "additionalProperties": False}}}


_IDS = {"type": "array", "items": {"type": "string"}, "description": "Product IDs, e.g. GLOVE-A"}
TOOL_SPECS: list[dict] = [
    spec("search_candidates",
         "Candidates for a request; not a decision. Finds DBxLab glove products whose catalog text matches a need "
         "(AI Search, hybrid: meaning and catalog numbers). Every candidate must still pass check_eligibility before "
         "it is offered. Not for documents or explanations: use knowledge_search.",
         {"need": {"type": "string", "description": "The need in words; leave out to use the quote's confirmed "
                                                    "requirements"},
          "sizes": {"type": "array", "items": {"type": "string"}}}, []),
    spec("check_eligibility",
         "Deterministic hard requirement checks against the quote's confirmed requirements. Returns verified_match, "
         "verified_mismatch or unknown per check, and a status per product: eligible, excluded or needs_evidence. "
         "Never treat unknown as eligible.",
         {"product_ids": _IDS}, ["product_ids"]),
    spec("get_evidence",
         "The evidence rows behind one product attribute (chemo_tested, latex_free, powder_free, material, ...): "
         "value, source document and verification status. Use for questions like 'is Glove B chemo-tested?'. An "
         "unverified row is a claim, not a fact.",
         {"product_id": {"type": "string"}, "attribute": {"type": "string"}}, ["product_id"]),
    spec("get_device",
         "The GUDID device record of a product's supplier device: brand, description, latex labelling, sterility, "
         "commercial distribution. From the lakehouse copy, with its release date.",
         {"product_id": {"type": "string"}}, ["product_id"]),
    spec("check_fulfilment",
         "Whether each product ships the quote's lines in full by the delivery date, partly, on backorder, or only "
         "on a later date (an alternative the customer must accept). Optional what-if: SKUs assumed out of stock "
         "(e.g. GLOVE-A-M).",
         {"product_ids": _IDS, "delivery_days": {"type": "integer"},
          "unavailable_skus": {"type": "array", "items": {"type": "string"}}}, ["product_ids"]),
    spec("price_quote",
         "Prices every line of the quote for one product from the price book: unit conversion, account tier, volume "
         "tier, margin floor. The only source of a price. With asked_unit_price it also returns the discount band "
         "(whether a manager must approve); it never sets a price.",
         {"product_id": {"type": "string"}, "asked_unit_price": {"type": "number"}}, ["product_id"]),
    spec("win_probability",
         "The win model's chance (demo data) that the customer accepts the quote for one product at each price. "
         "Informs the rep; never sets the price.",
         {"product_id": {"type": "string"}, "prices": {"type": "array", "items": {"type": "number"}}},
         ["product_id", "prices"]),
    spec("propose_transition",
         "Propose moving the quote to another state (for example PRICED or ISSUED), with a reason. Writes nothing: "
         "the rep confirms in the app and QuoteService commits it, if the rules allow it.",
         {"target": {"type": "string", "enum": STATES}, "reason": {"type": "string"}}, ["target", "reason"]),
    # AI-19
    spec("gudid_live_lookup",
         "Latest public GUDID record for ONE device identifier (DI), from the AccessGUDID API v3. Use only to "
         "confirm a single device is current; use get_device or search_candidates for everything else.",
         {"di": {"type": "string"}, "product_id": {"type": "string"}}, ["di"]),
    spec("ask_genie",
         "Sales analytics answered by the DBxLab sales analyst (Genie Agent) over the sales metric views: what an "
         "account bought, at what price, win rates. Returns the answer and the SQL it ran.",
         {"question": {"type": "string"}}, ["question"]),
    spec("knowledge_search",
         "Documents and explanations: supplier quality documents, GMDN notes, the rep's account price sheets. Use to "
         "explain why products differ, with sources. Not for finding candidate products.",
         {"question": {"type": "string"}, "product_ids": _IDS}, ["question"]),
    spec("load_skill",
         "Load an Agent Skill's instructions when its description matches the conversation. The skills are listed "
         "in the system prompt. Optional file: a file the skill refers to.",
         {"name": {"type": "string"}, "file": {"type": "string"}}, ["name"]),
    # AI-20
    spec("propose_memory",
         "Propose saving a fact the customer told us (for example latex_free = true) to the account's long-term "
         "memory. Writes nothing: the rep confirms it in the app.",
         {"fact_key": {"type": "string"}, "fact_value": {"type": "string"}}, ["fact_key", "fact_value"]),
]
SPECS = {s["function"]["name"]: s for s in TOOL_SPECS}
AI18_TOOLS = ["search_candidates", "check_eligibility", "get_evidence", "get_device", "check_fulfilment",
              "price_quote", "win_probability", "propose_transition"]
# What each tool would have told the rep: an unavailable result names it (AI-19)
MISSING = {"ask_genie": "buying history", "gudid_live_lookup": "the live GUDID record",
           "get_device": "the device record", "win_probability": "the win chance",
           "knowledge_search": "the supporting documents", "search_candidates": "the candidate search",
           "check_eligibility": "the eligibility check", "check_fulfilment": "the fulfilment check",
           "price_quote": "the price", "get_evidence": "the evidence"}
LIVE_TIMEOUT_S = 3.0     # get_device's live attempt before the lakehouse copy (AI-20)


@dataclass
class ToolDeps:
    """What the tools read. The app builds it from its Context, the deployed agent from its environment
    (quote_ai.agents.runtime); the tests from the demo stores."""
    catalog: Any                                    # quote_ai.storage.product_catalog.ProductCatalog
    evidence: Any                                   # quote_ai.storage.evidence_store.EvidenceStore
    rules: Any = None                               # quote_ai.services.shortlist LocalRules / SqlRules
    sku_index: Any = None
    win_model: Callable[[list[dict]], list] | None = None
    genie: Callable[[str], dict] | None = None      # AI-19
    knowledge_index: Any = None                     # AI-19
    accounts: Callable[[str], list[str]] | None = None
    live_gudid: Any = None                          # AI-19: quote_ai.gudid.api_client.LiveGudid
    skills: Any = None                              # AI-19: quote_ai.agents.skills.SkillLibrary
    policy: Policy = field(default_factory=Policy)
    live_first: bool = False                        # AI-20: get_device tries the live record first


class Toolbox:
    """The tools for one quote and one rep. run(name, args) -> the result dict."""

    def __init__(self, deps: ToolDeps, quote: Quote | None = None, rep: str | None = None,
                 tier: str = "standard", names: list[str] | None = None) -> None:
        self.deps, self.quote, self.rep, self.tier = deps, quote, rep, tier
        self.names = [n for n in (names or list(SPECS)) if n in SPECS and self._configured(n)]

    def _configured(self, name: str) -> bool:
        d = self.deps
        need = {"search_candidates": d.sku_index, "check_eligibility": d.rules, "gudid_live_lookup": d.live_gudid,
                "ask_genie": d.genie, "knowledge_search": d.knowledge_index, "load_skill": d.skills}
        return name not in need or need[name] is not None

    def specs(self) -> list[dict]:
        return [SPECS[n] for n in self.names]

    def run(self, name: str, args: dict) -> dict:
        if name not in self.names:
            return {"refused": name, "error": "UNKNOWN_TOOL", "reason": f"{name} is not one of this agent's tools"}
        missing = [p for p in SPECS[name]["function"]["parameters"]["required"] if p not in args]
        if missing:
            return {"refused": name, "error": "BAD_ARGUMENTS", "reason": f"missing {', '.join(missing)}"}
        try:
            return getattr(self, f"_{name}")(**args)
        except pricing.PricingError as e:          # a rule's answer, never retried
            return {"refused": name, "reason": str(e), "source": "rules"}
        except LookupError as e:                    # not found (a DI, a skill): an answer too, never a guess
            if isinstance(e, KeyError):
                raise
            return {"refused": name, "reason": str(e)}
        except TypeError as e:
            return {"refused": name, "error": "BAD_ARGUMENTS", "reason": str(e)}

    # ------------------------------------------------------------ helpers

    def _need_quote(self) -> Quote:
        if self.quote is None:
            raise pricing.PricingError("open a quote first: this tool works on a quote's lines")
        return self.quote

    def _products(self, ids: list[str]):
        return self.deps.catalog.products(list(dict.fromkeys(ids)))

    def _lines(self) -> list[QuoteLine]:
        q = self._need_quote()
        if not q.lines:
            raise pricing.PricingError("the quote has no confirmed lines yet")
        return q.lines

    # ------------------------------------------------------------ AI-18

    def _search_candidates(self, need: str | None = None, sizes: list[str] | None = None) -> dict:
        from quote_ai.search import retriever
        req = self.quote.requirements if self.quote else None
        need = need or (retriever.need_text(req) if req else "")
        sizes = sizes or (req.sizes if req else None)
        hits = retriever.search_candidates(self.deps.sku_index, need, sizes)
        ids = retriever.candidate_products(hits, 8)
        names = {p.product_id: p.name for p in self._products(ids)}
        return {"candidates": [{"product_id": i, "name": names.get(i, i)} for i in ids], "query": need,
                "note": "candidates only: check_eligibility decides", "source": "lakehouse_copy"}

    def _check_eligibility(self, product_ids: list[str]) -> dict:
        q = self._need_quote()
        products = self._products(product_ids)
        results, snapshot = self.deps.rules.eligibility(products, q.requirements)
        unknown = sorted(set(product_ids) - {p.product_id for p in products})
        return {"results": [{"product_id": r.product_id, "status": r.status.value,
                             "checks": {c.requirement: c.result.value for c in r.checks},
                             "failed": [c.requirement for c in r.checks if c.result.value != "verified_match"]}
                            for r in results],
                "unknown_products": unknown, "evidence_snapshot": snapshot,
                "rule": "unknown is never eligible", "source": "rules"}

    def _get_evidence(self, product_id: str, attribute: str | None = None) -> dict:
        rows = [r for r in self.deps.evidence.for_product(product_id)
                if attribute is None or r.get("attribute_name") == attribute]
        keep = ("attribute_name", "attribute_value", "unit", "source_type", "source_id", "document_id",
                "document_version", "verification_status", "verified_by")
        rows = [{k: r.get(k) for k in keep} for r in rows][:25]
        verified = [r for r in rows if r["verification_status"] == "verified"]
        return {"product_id": product_id, "attribute": attribute, "rows": rows,
                "verified": bool(verified), "note": "only verified rows are facts; the rest are claims",
                "source": "lakehouse_copy"}

    def _device_copy(self, product_id: str) -> dict:
        rec = self.deps.catalog.device(product_id)
        if rec is None:
            raise pricing.PricingError(f"no GUDID device record for {product_id}")
        return dict(rec) | {"source": "lakehouse_copy", "as_of": rec.get("release_date")}

    def _get_device(self, product_id: str) -> dict:
        copy = self._device_copy(product_id)
        if not (self.deps.live_first and self.deps.live_gudid):
            return copy
        try:                                        # AI-20: live first, 3 s; the copy if it fails, labelled
            live = self.deps.live_gudid.lookup(copy["primary_di"], timeout=LIVE_TIMEOUT_S)
            return live | {"product_id": product_id}
        except Exception as e:  # noqa: BLE001  any failure of the live API: degrade, say so
            return copy | {"fallback": f"live GUDID unavailable ({type(e).__name__}); lakehouse copy as of "
                                       f"{copy['as_of']}"}

    def _check_fulfilment(self, product_ids: list[str], delivery_days: int | None = None,
                          unavailable_skus: list[str] | None = None) -> dict:
        lines = self._lines()
        days = delivery_days if delivery_days is not None else (self.quote.requirements.delivery_days or 0)
        products = self._products(product_ids)
        stock, snapshot = self.deps.catalog.stock([s.sku_id for p in products for s in p.skus])
        out_skus = {s.upper() for s in unavailable_skus or []}
        if out_skus:                                # what-if: nothing in stock now for these SKUs
            stock = {k: v.model_copy(update={"available": 0}) if k.upper() in out_skus else v
                     for k, v in stock.items()}
        results = []
        for p in products:
            r = fulfilment.check_product(p, lines, stock, days, snapshot)
            results.append({"product_id": p.product_id} | fulfilment.summary(r)
                           | {"lines": [{"size": l.size, "status": l.status.value, "now": l.now, "later": l.later,
                                         "ready_day": l.ready_day, "late": l.late} for l in r.lines]})
        return {"results": results, "delivery_days": days, "assumed_out_of_stock": sorted(out_skus),
                "inventory_snapshot": snapshot, "source": "rules"}

    def _price_lines(self, product_id: str) -> tuple[list[dict], dict]:
        lines = self._lines()
        product, = self._products([product_id]) or [None]
        if product is None:
            raise pricing.PricingError(f"unknown product {product_id}")
        skus = {}
        for l in lines:
            sku = product.sku_for(l.size)
            if sku is None:
                raise pricing.PricingError(f"{product_id} has no size {l.size}")
            skus[l.size] = sku.sku_id
        book = self.deps.catalog.price_book()
        pack = self.deps.catalog.packaging(list(skus.values()))
        priced = [pricing.price_quote(skus[l.size], l.quantity, l.unit, self.tier, book, pack) | {"size": l.size}
                  for l in lines]
        return priced, {r.id: r for r in book}

    def _price_quote(self, product_id: str, asked_unit_price: float | None = None) -> dict:
        priced, rows = self._price_lines(product_id)
        prices = {p["unit_price"] for p in priced}
        out = {"product_id": product_id, "lines": priced, "total": round(sum(p["total"] for p in priced), 2),
               "unit_price": prices.pop() if len(prices) == 1 else None, "list_price": priced[0]["list_price"],
               "price_book_ids": [p["price_book_id"] for p in priced], "source": "rules"}
        if asked_unit_price is not None:
            row = rows[priced[0]["price_book_id"]]
            out["discount_band"] = pricing.discount_band(row.list_price, float(asked_unit_price), row,
                                                         self.deps.policy.pricing)
        return out

    def _win_probability(self, product_id: str, prices: list[float]) -> dict:
        from quote_ai.ml import score
        if self.deps.win_model is None:
            return {"unavailable": "win_probability", "missing": MISSING["win_probability"],
                    "message": "no win model is configured"}
        q = self._need_quote()
        priced, _ = self._price_lines(product_id)
        lines = [{"sku_id": p["sku_id"], "quantity": p["boxes"], "list_price": p["list_price"],
                  "unit_price": p["unit_price"]} for p in priced]
        try:
            rows = score.what_if_rows(q.quote_id, q.account, lines, sorted({float(p) for p in prices}))
            chances = score.win_chances(rows, self.deps.win_model)
        except score.ScoringError as e:
            return {"refused": "win_probability", "reason": str(e), "source": "model"}
        return {"product_id": product_id, "chances": [{"price": c["price"], "win_chance": c["win_chance"]}
                                                      for c in chances],
                "label": score.LABEL, "source": "model"}

    def _propose_transition(self, target: str, reason: str) -> dict:
        """Writes nothing. The idempotency key makes the same proposal twice one proposal (AI-19)."""
        q = self._need_quote()
        if target not in STATES:
            return {"refused": "propose_transition", "reason": f"unknown state {target}"}
        key = hashlib.sha256(f"{q.quote_id}:{q.revision}:{target}".encode()).hexdigest()[:16]
        return {"proposal": {"quote_id": q.quote_id, "from_state": q.state.value, "target": target,
                             "reason": reason, "version": q.version, "revision": q.revision,
                             "idempotency_key": key},
                "note": "a proposal only: the rep confirms in the app; QuoteService commits if the rules allow",
                "source": "proposal"}

    # ------------------------------------------------------------ AI-19

    def _gudid_live_lookup(self, di: str, product_id: str | None = None) -> dict:
        """The live record (cached per DI for a day). From AI-20, a failure falls back to the lakehouse copy, labelled
        with its release date; without a product to fall back to, the failure goes to reliability.py."""
        try:
            return self.deps.live_gudid.lookup(di)
        except Exception as e:  # noqa: BLE001
            if not (self.deps.live_first and product_id):
                raise
            copy = self._device_copy(product_id)
            return copy | {"fallback": f"live GUDID unavailable ({type(e).__name__}); lakehouse copy as of "
                                       f"{copy['as_of']}"}

    def _ask_genie(self, question: str) -> dict:
        from quote_ai.agents.reliability import ServiceUnavailable
        try:
            a = self.deps.genie(question)
        except Exception as e:  # noqa: BLE001  a paused or failing Genie space is a transient failure here
            raise ServiceUnavailable(f"Genie: {e}") from e
        return {"text": a.get("text"), "sql": a.get("sql"), "columns": a.get("columns", []),
                "rows": (a.get("rows") or [])[:50], "source": "genie"}

    def _knowledge_search(self, question: str, product_ids: list[str] | None = None) -> dict:
        from quote_ai.search import retriever
        accounts = self.deps.accounts(self.rep) if (self.deps.accounts and self.rep) else []
        chunks = retriever.retrieve_chunks(self.deps.knowledge_index, question, product_ids, 5, accounts)
        return {"chunks": [{"chunk_id": c.get("chunk_id"), "doc_id": c.get("doc_id"), "section": c.get("section"),
                            "truth_layer": c.get("truth_layer"), "text": (c.get("text") or "")[:500]}
                           for c in chunks], "source": "ai_search"}

    def _load_skill(self, name: str, file: str | None = None) -> dict:
        return self.deps.skills.load(name, file) | {"source": "skill"}

    # ------------------------------------------------------------ AI-20

    def _propose_memory(self, fact_key: str, fact_value: str) -> dict:
        q = self._need_quote()
        return {"memory_proposal": {"account": q.account, "fact_key": fact_key, "fact_value": str(fact_value),
                                    "question": f"Save for {q.account}: {fact_key.replace('_', ' ')} = "
                                                f"{fact_value}?"},
                "note": "nothing is saved until the rep confirms", "source": "proposal"}


def eligible_ids(result: dict) -> list[str]:
    return [r["product_id"] for r in result.get("results", []) if r["status"] == Eligibility.ELIGIBLE.value]


def dumps(out: Any) -> str:
    return json.dumps(out, default=str)
