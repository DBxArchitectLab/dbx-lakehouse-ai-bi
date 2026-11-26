"""/api/compare (AI-16, AI-17): the compare view and Ask about this product. The route only calls the shared package.

  GET  /api/compare/{quote_id}
      the comparison table of the quote's eligible gloves, built by quote_ai.product.compare from the evidence, the
      price book and the stock; each glove's status from the AI-15 rules (on time / alternative, needs acceptance);
      the gloves that need evidence listed under it. No quality score.
  POST /api/compare/{quote_id}/ask   {"question": ..., "product_id": optional, "variant": "build" | "buy"}
      build: quote_ai.llm.rag.explain_comparison (retrieve -> generate through Unity Gateway -> ground -> cite);
      buy: the Knowledge Assistant (quote_ai.llm.knowledge_assistant). With product_id: scoped to that glove (an
      eligible one, or one that needs evidence). Every statement has a citation and a truth layer; nothing found ->
      "no evidence on file". Answers are cached per quote version, question, scope and the rep's accounts.

AI-17: the signed-in rep's accounts (from the identity headers Databricks Apps forwards, never from the question)
filter the knowledge index before retrieval, so another customer's price sheet never reaches the prompt or the
screen. Until AI-24 the app queries as its own service principal and this code applies the filter.
AI-26: when the comparison model fails, the answer falls back to the structured table without prose: what the rules
and the table say (cited), "fallback" says why, the failure goes to the app log, and the fallback is not cached.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from quote_ai.llm import knowledge_assistant, rag
from quote_ai.product import compare
from quote_ai.search import retriever

from ..auth import User, rep
from ..context import Context
from ..deps import context
from .quotes import _own

router = APIRouter(prefix="/api/compare", tags=["compare"])
log = logging.getLogger("quote_ai.app.compare")
NO_PROSE = "The comparison model is unavailable: the comparison table and the rules' findings, without prose."
ASKED_AS = ("the app's service principal; the knowledge index filtered by your accounts in QuoteAI's code "
            "(Unity Catalog enforces it from AI-24)")


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    product_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    variant: str = Field(default="build", pattern=r"^(build|buy)$")


def _table(ctx: Context, q) -> dict:
    if not q.candidates:
        raise HTTPException(status_code=409, detail="no shortlist on this quote yet: Find products first")
    if ctx.evidence is None:
        raise HTTPException(status_code=503, detail="evidence is not configured in this app")
    eligible = [c["product_id"] for c in q.candidates if c["status"] == "eligible"]
    waiting = [c["product_id"] for c in q.candidates if c["status"] == "needs_evidence"]
    products = ctx.catalog.products(eligible + waiting)
    names = {p.product_id: p.name for p in products}
    stock, _ = ctx.catalog.stock([s.sku_id for p in products for s in p.skus])
    return compare.comparison_table(eligible, names, ctx.evidence.for_products(eligible + waiting), q.candidates,
                                    ctx.catalog.price_book(), stock, q.requirements.target_unit_price)


@router.get("/{quote_id}")
def compare_view(quote_id: str, user: User = Depends(rep), ctx: Context = Depends(context)):
    q = _own(ctx, quote_id, user)
    return {"quote_id": q.quote_id, "version": q.version, "state": q.state.value,
            "evidence_snapshot": q.evidence_snapshot} | _table(ctx, q)


@router.post("/{quote_id}/ask")
def ask(quote_id: str, body: Ask, user: User = Depends(rep), ctx: Context = Depends(context)):
    q = _own(ctx, quote_id, user)
    if ctx.knowledge_index is None:
        raise HTTPException(status_code=503, detail="the knowledge index is not configured in this app")
    table = _table(ctx, q)
    allowed = {c["product_id"] for c in q.candidates if c["status"] in ("eligible", "needs_evidence")}
    if body.product_id and body.product_id not in allowed:
        raise HTTPException(status_code=422, detail=f"{body.product_id} is not on this quote's shortlist")
    scope = [body.product_id] if body.product_id else [c["product_id"] for c in table["columns"]]
    accounts = ctx.accounts(user.id) if ctx.accounts else []
    key = (q.quote_id, q.version, body.question.strip().lower(), body.product_id, body.variant, tuple(accounts))
    if key in ctx.cache:
        return ctx.cache[key] | {"cached": True}

    def retrieve(question, product_ids, k, accts):
        return retriever.retrieve_chunks(ctx.knowledge_index, question, product_ids, k, accts, chunking="section")

    if body.variant == "buy":
        if ctx.ka is None:
            raise HTTPException(status_code=503, detail="no Knowledge Assistant endpoint configured")
        chunks = retrieve(body.question, scope, 20, accounts)
        statements, problems = rag.ground(knowledge_assistant.to_statements(ctx.ka(body.question), chunks), chunks,
                                          table)
        out = {"question": body.question, "answer": rag.answer_text(statements), "statements": statements,
               "sources": sorted({s["citation"] for s in statements}), "problems": problems, "variant": "buy",
               "retrieved": [{k: c.get(k) for k in ("chunk_id", "doc_id", "doc_version", "section", "truth_layer",
                                                    "text")} for c in chunks if c["chunk_id"] in
                             {s["citation"] for s in statements}]}
    else:
        try:
            out = rag.explain_comparison(body.question, scope, table, retrieve, ctx.generate, accounts,
                                         template=ctx.compare_template, prompt_version=ctx.compare_prompt_version)
        except Exception:  # noqa: BLE001  AI-26: the comparison model failed; the table without prose
            if ctx.generate is None:
                raise
            log.exception("comparison model call failed for %s; falling back to the table", q.quote_id)
            out = rag.explain_comparison(body.question, scope, table, retrieve, _no_prose, accounts,
                                         template=ctx.compare_template, prompt_version=ctx.compare_prompt_version)
            return out | {"scope": scope, "asked_as": ASKED_AS, "cached": False, "trace_id": None,
                          "fallback": NO_PROSE}
    out |= {"scope": scope, "asked_as": ASKED_AS, "cached": False, "trace_id": _last_trace_id()}
    ctx.cache[key] = out
    return out


def _no_prose(prompt: str) -> str:
    """No model: no generated statements; the rules' findings on the table remain (rag.rule_statements)."""
    return '{"statements": []}'


def _last_trace_id() -> str | None:
    """AI-22: the MLflow trace of the answer just made (explain_comparison is traced), for the feedback buttons.
    Verify before recording: mlflow.get_last_active_trace_id() in the installed MLflow 3."""
    try:
        import mlflow
        return mlflow.get_last_active_trace_id()
    except Exception:  # noqa: BLE001  no MLflow (tests, the local demo): no trace to rate
        return None
