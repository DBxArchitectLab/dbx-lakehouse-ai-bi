"""Compare and explain (AI-16): the custom RAG chain, the "build" option.

  retrieve -> assemble context (each chunk with its ID and truth layer) -> generate (the compare_explain prompt from
  the Prompt Registry, through Unity Gateway) -> ground -> cite

The model writes statements; code checks them (ground): a statement must cite a retrieved chunk, a table cell
(table:<product>:<attribute>) or a rule (rule:<product>:<requirement>), and its truth layer is taken from what it
cites, never from the model. A "business truth" label therefore always points to verified evidence; a statement
whose citation was not retrieved is dropped and reported. Nothing retrieved that supports an answer -> "no evidence
on file", not a guess. The table and each glove's status come from data and the rules (quote_ai.product.compare),
not from here.

  explain_comparison   the chain, traced with MLflow when MLflow is there (retrieved chunks, prompt version, latency)
  ground               the deterministic check behind every answer (and the Comparison suite's truth-layer assertion)
  extractive_generate  a model-free stand-in that quotes the retrieved text: tests, the local demo, the suites offline
  llm_generate         the chat model service (quote_ai_chat_<target>) as the generator
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable

from quote_ai.llm import prompts
from quote_ai.product.compare import cell_layer
from quote_ai.search.local import concepts

NO_EVIDENCE = "no evidence on file"
LAYERS = ("business_truth", "extracted_claim", "source_truth", "no_evidence")
PROMPT_NAME = "compare_explain"
# question concept -> the attributes / requirements it is about
TOPICS = {"chemo": ("chemo_tested",), "latex_free": ("latex_free",), "latex": ("latex_free",),
          "powder_free": ("powder_free",), "thickness": ("thickness",), "thin": ("thickness",), "thick": ("thickness",),
          "aql": ("aql",), "tensile": ("tensile_strength",), "strength": ("tensile_strength",),
          "nitrile": ("material",), "material": ("material",), "sterile": ("sterile",), "exam": ("intended_use",),
          "surgical": ("intended_use",), "quality": ("aql", "thickness", "tensile_strength"),
          "differences": ("aql", "thickness", "tensile_strength"), "difference": ("aql", "thickness", "tensile_strength"),
          "differ": ("aql", "thickness", "tensile_strength"), "compare": ("aql", "thickness", "tensile_strength")}
GENERIC = {"glove", "product", "customer", "explain", "offer", "support", "supports", "claim", "tell", "us", "about",
           "rep", "why", "dbxlab", "would", "should", "could", "has", "have", "they", "them", "these",
           "three", "two", "options", "option", "quote", "differences", "difference", "differ", "compare", "quality",
           "evidence", "behind", "say", "says", "a", "b", "c", "d", "e", "f", "price", "sheet", "negotiated",
           "agree", "agreed", "contract", "did", "on", "file", "who"}


def _trace(fn):
    try:
        import mlflow
        return mlflow.trace(fn)
    except Exception:  # noqa: BLE001  no MLflow (tests)
        return fn


def load_template(uri: str | None = None) -> tuple[str, str]:
    """(template, version) from the Prompt Registry (prompts:/<catalog>.<schema>.compare_explain@champion), else
    the local v1: for notebooks only. The app reads by alias with prompts.load_prompt, which raises instead (AI-23). Verify before recording: mlflow.genai.load_prompt and the .template / .version attributes."""
    if uri:
        try:
            import mlflow
            p = mlflow.genai.load_prompt(uri)
            return p.template, f"{PROMPT_NAME}/v{p.version}"
        except Exception:  # noqa: BLE001  fall back to the code's version, and say so
            pass
    return prompts.COMPARE_EXPLAIN_V1, f"{PROMPT_NAME}/v1-local"


def context_text(chunks: list[dict]) -> str:
    return "\n\n".join(f"[{c['chunk_id']}] ({c['truth_layer']}) {c['text']}" for c in chunks)


def parse_statements(text: str) -> list[dict]:
    """The model's JSON ({"statements": [...]}) or a bare list; anything else is no statements."""
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    try:
        data = json.loads(t)
    except ValueError:
        return []
    items = data.get("statements", []) if isinstance(data, dict) else data
    return [s for s in items if isinstance(s, dict) and s.get("text")]


def ground(statements: list[dict], chunks: list[dict], table: dict | None) -> tuple[list[dict], list[str]]:
    """Every statement against what it cites. The layer comes from the source; a citation that wasn't retrieved (or
    a table cell that doesn't exist) drops the statement. Returns (kept, problems)."""
    by_id = {c["chunk_id"]: c for c in chunks}
    kept, problems = [], []
    for st in statements:
        cit = str(st.get("citation") or "").strip().strip("[]")
        if cit.startswith("table:") and table is not None and cit.count(":") == 2:
            _, pid, attr = cit.split(":")
            layer = cell_layer(table, pid, attr)
        elif cit.startswith("rule:"):
            layer = "no_evidence"
        else:
            layer = by_id[cit]["truth_layer"] if cit in by_id else None
        if layer is None:
            problems.append(f"dropped, not a retrieved source: {cit or '(no citation)'}: {st.get('text')!r}")
            continue
        if st.get("layer") and st.get("layer") != layer:
            problems.append(f"relabelled {cit}: {st.get('layer')} -> {layer}")
        kept.append({"text": st["text"], "citation": cit, "layer": layer})
    return kept, problems


def _topics(question: str) -> set[str]:
    out = set()
    for c in concepts(question):
        out |= set(TOPICS.get(c, ()))
    return out


def rule_statements(question: str, table: dict | None, scope: list[str]) -> list[dict]:
    """What the rules say is missing, for the gloves in scope that need evidence (AI-15), when the question is
    about that requirement: "no chemo test report on file" is a rule result, cited as such."""
    if not table:
        return []
    topics = _topics(question)
    out = []
    for n in table.get("needs_evidence", []):
        if n["product_id"] not in scope:
            continue
        for req in n.get("failed", []):
            if req in topics:
                what = "chemo test report" if req == "chemo_tested" else f"verified {req.replace('_', ' ')}"
                out.append({"text": f"{n['name']}: no {what} on file, so it is not offered for this until a verified "
                                    f"document arrives.", "citation": f"rule:{n['product_id']}:{req}",
                            "layer": "no_evidence"})
    return out


def extractive_generate(question: str, chunks: list[dict], table: dict | None, scope: list[str] | None = None,
                        max_chunks: int = 4) -> str:
    """A model-free generator that only quotes: the sentence of each retrieved chunk that shares a topic word with
    the question, and the table cells (of the gloves in scope) the question is about. Same JSON as the model, so it
    goes through ground too."""
    from quote_ai.search.chunks import is_front_matter
    wanted = {c for c in concepts(question) if c not in GENERIC and len(c) > 2}
    topics = _topics(question)
    statements = []
    for c in chunks:
        if len(statements) >= max_chunks:
            break
        if is_front_matter(c.get("section") or ""):
            continue
        body = " ".join(l.strip() for l in c["text"].splitlines()[1:] if l.strip())
        sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", body) if x.strip()]
        hit = next((x for x in sentences if wanted & set(concepts(x))), None)
        if hit is None:
            continue
        who = c["product_id"] if c["product_id"] != "*" else ""
        prefix = {"extracted_claim": f"{who}: its {'description' if c['doc_id'].startswith('GUDID') else 'sheet'} "
                                     f"says (a claim, not verified): ",
                  "business_truth": f"{who}: ", "source_truth": ""}.get(c["truth_layer"], "")
        statements.append({"text": f"{prefix}{hit}".strip(), "citation": c["chunk_id"], "layer": c["truth_layer"]})
    for col in (table or {}).get("columns", []) + (table or {}).get("needs_evidence", []):
        if scope is not None and col["product_id"] not in scope:
            continue
        for attr in sorted(topics):
            cell = col["specs"].get(attr)
            if cell and cell["status"] != "missing":
                unit = f" {cell['unit']}" if cell.get("unit") else ""
                statements.append({"text": f"{col['name']}: {attr.replace('_', ' ')} {cell['value']}{unit} "
                                           f"({cell['status']}, {cell['source_id']})",
                                   "citation": f"table:{col['product_id']}:{attr}", "layer": ""})
    return json.dumps({"statements": statements})


def llm_generate(endpoint: str, client=None, max_tokens: int | None = None) -> Callable[[str], str]:
    """The chat model service as the generator (temperature 0, JSON out). max_tokens: the comparison's limit in
    config/quote_ai/models.yml (AI-23)."""
    from quote_ai.llm.client import chat
    kw = {"max_tokens": max_tokens} if max_tokens else {}

    def generate(prompt: str) -> str:
        return chat(prompt, endpoint=endpoint, client=client, response_format={"type": "json_object"}, **kw).text
    return generate


def answer_text(statements: list[dict]) -> str:
    if not statements:
        return NO_EVIDENCE
    return " ".join(f"{s['text']} [{i + 1}]" for i, s in enumerate(statements))


@_trace
def explain_comparison(question: str, scope: list[str], table: dict | None, retrieve: Callable[..., list[dict]],
                       generate: Callable[[str], str] | None = None, accounts: list[str] | None = None, k: int = 8,
                       template: str | None = None, prompt_version: str = f"{PROMPT_NAME}/v1-local") -> dict:
    """scope: the product IDs the question is about (all compared gloves, or one for Ask about this product).
    retrieve(question, product_ids, k, accounts) -> chunks: the account filter is applied inside retrieval.
    generate(prompt) -> the model's JSON; None = the extractive stand-in."""
    start = time.perf_counter()
    chunks = retrieve(question, scope, k, accounts)
    if generate is None:
        raw = extractive_generate(question, chunks, table, scope)
    else:
        prompt = prompts.fill(template or prompts.COMPARE_EXPLAIN_V1, question=question,
                              table=_table_md(table), context=context_text(chunks))
        raw = generate(prompt)
    statements, problems = ground(parse_statements(raw), chunks, table)
    for r in rule_statements(question, table, scope):
        if r["citation"] not in {s["citation"] for s in statements}:
            statements.append(r)
    cited = {s["citation"] for s in statements}
    return {"question": question, "answer": answer_text(statements), "statements": statements,
            "sources": [c["chunk_id"] for c in chunks if c["chunk_id"] in cited],
            "retrieved": [{k_: c.get(k_) for k_ in ("chunk_id", "doc_id", "doc_version", "section", "truth_layer",
                                                     "account_id", "text")} for c in chunks],
            "problems": problems, "variant": "build", "prompt_version": prompt_version,
            "latency_s": round(time.perf_counter() - start, 3)}


def _table_md(table: dict | None) -> str:
    if not table:
        return "(no table)"
    from quote_ai.product.compare import table_markdown
    return table_markdown(table)
