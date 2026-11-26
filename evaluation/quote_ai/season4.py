"""The Search, Eligibility, Fulfilment and Comparison suites (AI-15 to AI-17): the predict functions and the runs.

Locally (CI, no workspace) every suite runs on the synthetic data the app's demo uses: the evidence as of a named
snapshot, the SKU search document and the knowledge chunks in quote_ai.search.local indexes. In the workspace the
same predict functions take the AI Search indexes and the Unity Catalog functions (--backend workspace).

  SNAPSHOTS          evidence snapshot name -> the demo evidence it stands for. product_evidence@v41: before Glove
                     B's report; @v57: Glove B's report verified (AI-26). In the workspace a case's snapshot is a
                     Delta version (VERSION AS OF) of the evidence tables. Verify before recording.
  predict_search     a search case -> the top products (one SKU each) and the engine's answer on the candidates
  predict_eligibility  an eligibility case -> the engine's answer for one glove on its snapshot
  predict_comparison   a comparison question -> the statements, what was retrieved and which sources are verified
  run                one suite and variant -> per case scores, the metrics and the gates (any gate above 0 fails)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAPSHOTS = {
    "product_evidence@v41": {"scenarios": ("glove_a_report",), "signoffs": {("RPT-CHEMO-A", 1): "qa.demo"}},
    "product_evidence@v57": {"scenarios": ("glove_a_report", "glove_b_report"),
                             "signoffs": {("RPT-CHEMO-A", 1): "qa.demo", ("RPT-CHEMO-B", 1): "qa.demo"}},
}
DEFAULT_SNAPSHOT = "product_evidence@v41"
COMPARE_TEMPLATE: str | None = None     # AI-23, E1: the compare_explain version an experiment runs (None: v1)
LAKESIDE_SCOPE = ["GLOVE-A", "GLOVE-C", "GLOVE-D"]


def load(suite: str) -> list[dict]:
    path = HERE / "v1" / suite / "cases.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


@lru_cache(maxsize=None)
def store(snapshot: str = DEFAULT_SNAPSHOT):
    from quote_ai.storage.evidence_store import DemoEvidenceStore
    s = SNAPSHOTS[snapshot]
    return DemoEvidenceStore(scenarios=s["scenarios"], signoffs=dict(s["signoffs"]), snapshot=snapshot)


@lru_cache(maxsize=None)
def sku_index(snapshot: str = DEFAULT_SNAPSHOT):
    from quote_ai.search import docs, local
    return local.InMemoryIndex(docs.demo_docs(store(snapshot).rows))


@lru_cache(maxsize=None)
def knowledge_index(snapshot: str = DEFAULT_SNAPSHOT):
    from quote_ai.mock import knowledge
    from quote_ai.search import local
    return local.InMemoryIndex(knowledge.demo_chunks(store(snapshot).rows), text_column="text",
                               primary_key="chunk_id", keyword_columns=())


def requirements(case: dict):
    """A case's hard requirements as the confirmed requirements (r2): sizes, the selling unit, the glove count."""
    from quote_ai.domain.models import Requirements
    r = dict(case["requirements"])
    sizes = r.pop("sizes")
    return Requirements(version=2, sizes=sizes, unit="box", gloves_per_unit=r.pop("gloves_per_unit", 100),
                        quantity_by_size={s: 10 for s in sizes}, missing=[], **r)


@dataclass
class Engine:
    """The rules a run uses. leaky_summary is the variant that must fail: it lets what the search document says
    (every claim, verified or not) count as evidence, so Glove B's description makes it eligible."""
    leaky_summary: bool = False
    relax_latex: bool = False
    unverified: set = field(default_factory=set)

    def check(self, products, req, snapshot):
        from quote_ai.domain.models import Check, Verification
        from quote_ai.product.evidence import rows_to_domain
        from quote_ai.rules import eligibility
        rows = store(snapshot).for_products([p.product_id for p in products])
        self.unverified = {r["source_id"] for r in rows if r["verification_status"] != "verified"}
        ev = rows_to_domain(rows)
        if self.leaky_summary:
            ev = [e.model_copy(update={"verification": Verification.VERIFIED}) for e in ev]
        out = []
        for p in products:
            r = eligibility.check_product(p, req, ev, snapshot)
            if self.relax_latex:      # the demo's deliberate break: unknown latex accepted as a pass
                checks = [c.model_copy(update={"result": Check.MATCH}) if c.requirement == "latex_free"
                          and c.result == Check.UNKNOWN else c for c in r.checks]
                r = r.model_copy(update={"checks": checks, "status": eligibility.status_of(checks)})
            out.append(r)
        return out


VARIANT_ENGINES = {"leaky_summary": Engine(leaky_summary=True), "relax_latex": Engine(relax_latex=True)}


def predict_search(case: dict, variant: str = "hybrid_section", index=None, max_products: int = 8) -> dict:
    from quote_ai.search import retriever
    from quote_ai.storage.product_catalog import DemoProductCatalog
    cfg = retriever.VARIANTS.get(variant, retriever.VARIANTS["hybrid_section"])
    snapshot = case.get("evidence_snapshot", DEFAULT_SNAPSHOT)
    hits = retriever.search_candidates(index or sku_index(snapshot), case["need"], case["sizes"], k=cfg.k * 2,
                                       query_type=cfg.query_type, rerank=cfg.rerank, hybrid_weight=cfg.hybrid_weight)
    ids = retriever.candidate_products(hits, max_products)
    engine = VARIANT_ENGINES.get(variant, Engine())
    results = engine.check(DemoProductCatalog().products(ids), requirements(case), snapshot)
    return {"sku_ids": [f"{p}-{case['sizes'][0]}" for p in ids], "candidates": ids,
            "results": [r.model_dump(mode="json") for r in results], "unverified_sources": sorted(engine.unverified)}


def predict_shortlist(case: dict, variant: str = "", extract=None) -> dict:
    """A Lakeside shortlist case (AI-22, elig-007): the request's requirements -> search -> the rules, as the app's
    Shortlist screen builds it. With `extract` (the eval job: the extraction model service and the production prompt)
    the requirements come from the request text, so a prompt that drops a requirement shows up here; without it (CI)
    from the case. The options are what the screen lists: eligible and needs evidence."""
    from quote_ai.domain.models import QuoteLine, Requirements
    from quote_ai.services.shortlist import LocalRules, build_shortlist
    from quote_ai.storage.product_catalog import DemoProductCatalog
    snapshot = case.get("evidence_snapshot", DEFAULT_SNAPSHOT)
    if extract is not None:
        from quote_ai.llm.requirements_schema import to_domain
        r1 = to_domain(extract(case["request_text"]))
        fields = {k: getattr(r1, k) for k in ("material", "intended_use", "powder_free", "latex_free",
                                              "chemo_tested", "sterile", "delivery_days")}
        sizes = r1.sizes or case["requirements"]["sizes"]
    else:
        fields = {k: v for k, v in case["requirements"].items() if k != "sizes"}
        sizes = case["requirements"]["sizes"]
    split = {s: 10 for s in sizes}
    req = Requirements(version=2, sizes=sizes, unit="box", gloves_per_unit=100, quantity_by_size=split, missing=[],
                       **fields)
    lines = [QuoteLine(line_no=i + 1, size=s, quantity=n, unit="box") for i, (s, n) in enumerate(split.items())]
    cat = DemoProductCatalog()
    s = build_shortlist(req, lines, sku_index(snapshot), cat, LocalRules(store(snapshot), cat))
    options = [r for r in s.results if r.status.value in ("eligible", "needs_evidence")]
    shortlist = [{"sku_id": f"{r.product_id}-{size}", "status": r.status.value,
                  "checks": [{"requirement": c.requirement, "state": "unknown" if c.result.value == "unknown"
                              else c.result.value, "counted_as_match": c.result.value == "verified_match"}
                             for c in r.checks]} for r in options for size in sizes]
    return {"shortlist": shortlist, "results": [r.model_dump(mode="json") for r in s.results],
            "unverified_sources": [], "requirements": req.model_dump(mode="json")}


def predict_eligibility(case: dict, variant: str = "", extract=None) -> dict:
    if case.get("kind") == "shortlist":
        return predict_shortlist(case, variant, extract)
    from quote_ai.storage.product_catalog import DemoProductCatalog
    pid = f"GLOVE-{case['glove']}"
    engine = VARIANT_ENGINES.get(variant, Engine())
    r, = engine.check(DemoProductCatalog().products([pid]), requirements(case), case["evidence_snapshot"])
    d = r.model_dump(mode="json")
    return {"result": d, "results": [d], "unverified_sources": sorted(engine.unverified)}


@lru_cache(maxsize=None)
def lakeside_table(snapshot: str = DEFAULT_SNAPSHOT) -> dict:
    """The Lakeside quote's comparison table, from its shortlist (search, then the rules), as the app builds it."""
    from quote_ai.mock import catalog
    from quote_ai.product import compare
    from quote_ai.storage.product_catalog import DemoProductCatalog
    from quote_ai.domain.models import Requirements
    req = Requirements(version=2, material="nitrile", powder_free=True, intended_use="exam", latex_free=True,
                       chemo_tested=True, sizes=catalog.SIZES, unit="box", gloves_per_unit=100,
                       quantity_by_size={"S": 60, "M": 140, "L": 140, "XL": 60}, delivery_days=10,
                       target_unit_price=8.00)
    from quote_ai.domain.models import QuoteLine
    from quote_ai.services.shortlist import LocalRules, build_shortlist
    cat = DemoProductCatalog()
    lines = [QuoteLine(line_no=i + 1, size=s, quantity=n, unit="box") for i, (s, n) in
             enumerate(req.quantity_by_size.items())]
    s = build_shortlist(req, lines, sku_index(snapshot), cat, LocalRules(store(snapshot), cat))
    candidates = [{"product_id": r.product_id, "status": r.status.value,
                   "failed": [c.requirement for c in r.checks if c.result.value != "verified_match"],
                   "label": s.fulfilment[r.product_id].label if r.product_id in s.fulfilment else None}
                  for r in s.results]
    eligible = [c["product_id"] for c in candidates if c["status"] == "eligible"]
    names = {p.product_id: p.name for p in s.products.values()}
    waiting = [c["product_id"] for c in candidates if c["status"] == "needs_evidence"]
    stock, _ = cat.stock([k.sku_id for p in s.products.values() for k in p.skus])
    return compare.comparison_table(eligible, names, store(snapshot).for_products(eligible + waiting), candidates,
                                    cat.price_book(), stock, 8.00)


def _source_texts(table: dict, chunks: list[dict]) -> tuple[dict, set]:
    """What each citation stands for, and which citations are verified: from the evidence and the table, never
    from the model."""
    texts, verified = {}, set()
    for c in table["columns"] + table["needs_evidence"]:
        for attr, cell in c["specs"].items():
            key = f"table:{c['product_id']}:{attr}"
            unit = f" {cell['unit']}" if cell.get("unit") else ""
            texts[key] = (f"{c['name']}: {attr.replace('_', ' ')} {cell['value']}{unit} ({cell['status']}, "
                          f"{cell['source_id']})")
            if cell["status"] == "verified":
                verified.add(key)
        for req in c.get("failed", []):
            what = "chemo test report" if req == "chemo_tested" else f"verified {req.replace('_', ' ')}"
            texts[f"rule:{c['product_id']}:{req}"] = (f"{c['name']}: no {what} on file, so it is not offered for "
                                                      f"this until a verified document arrives.")
    verified_docs = {(r["document_id"], int(r["document_version"])) for r in store().rows
                     if r.get("document_id") and r["verification_status"] == "verified"}
    for ch in chunks:
        texts[ch["chunk_id"]] = ch["text"]
        if (ch["doc_id"], int(ch["doc_version"])) in verified_docs:
            verified.add(ch["chunk_id"])
    return texts, verified


def predict_comparison(case: dict, variant: str = "hybrid_section", generate=None, ka=None, index=None) -> dict:
    """build (default): the RAG chain, with the model if `generate` is given, else the extractive stand-in.
    buy: the Knowledge Assistant (`ka`: question -> response)."""
    from quote_ai.llm import knowledge_assistant, rag
    from quote_ai.mock import knowledge
    from quote_ai.search import retriever
    cfg = retriever.VARIANTS.get(variant, retriever.VARIANTS["hybrid_section"])
    table = lakeside_table()
    accounts = knowledge.accounts_of(case.get("rep", "rep.alice"))
    idx = index or knowledge_index()

    def retrieve(question, scope, k, accts):
        return retriever.retrieve_chunks(idx, question, scope, k, accts, query_type=cfg.query_type,
                                         rerank=cfg.rerank, chunking=cfg.chunking)

    if ka is not None:
        chunks = retrieve(case["question"], case["scope"], 20, accounts)
        statements, _ = rag.ground(knowledge_assistant.to_statements(ka(case["question"]), chunks), chunks, table)
        out = {"answer": rag.answer_text(statements), "statements": statements, "retrieved": chunks}
    else:
        out = rag.explain_comparison(case["question"], case["scope"], table, retrieve, generate, accounts,
                                     template=COMPARE_TEMPLATE)
        out["retrieved"] = out["retrieved"]
    texts, verified = _source_texts(table, out["retrieved"])
    return {"answer": out["answer"], "statements": out["statements"],
            "retrieved": [{"chunk_id": c["chunk_id"], "account_id": c.get("account_id")} for c in out["retrieved"]],
            "source_texts": texts, "verified_sources": sorted(verified)}


def predict_fulfilment(case: dict, variant: str = "") -> dict:
    from quote_ai.domain.models import QuoteLine
    from quote_ai.rules.fulfilment import Stock, check_line
    r = check_line(QuoteLine(line_no=1, size="M", quantity=case["quantity"], unit="box"), "X",
                   Stock(sku_id="X", **case["stock"]), case["delivery_days"])
    return {"status": r.status.value, "late": r.late}


def expectations(suite: str, case: dict) -> dict:
    if suite == "search":
        return {"expected_skus": case["expected_skus"],
                "must_not_include_products": sorted({s.rsplit("-", 1)[0] for s in case["must_not_include"]})}
    if suite == "eligibility" and case.get("kind") == "shortlist":
        return {"must_exclude_skus": case["must_exclude_skus"],
                "must_not_be_match_if_unknown": case.get("must_not_be_match_if_unknown", []),
                "must_not_include_products": sorted({s.rsplit("-", 1)[0] for s in case["must_exclude_skus"]})}
    if suite == "eligibility":
        return {"expect": case.get("expect", {}), "status": case["status"],
                "must_not_include_products": [] if case["status"] == "eligible" else [f"GLOVE-{case['glove']}"]}
    if suite == "comparison":
        from quote_ai.mock import knowledge
        return {"expected_sources": case.get("expected_sources", []),
                "expect_no_evidence": case.get("expect_no_evidence", False),
                "allowed_accounts": knowledge.accounts_of(case.get("rep", "rep.alice"))}
    return case["expected"]


def predictor(suite: str, variant: str, generate=None, ka=None, extract=None):
    if suite == "search":
        return lambda c: predict_search(c, variant)
    if suite == "eligibility":
        return lambda c: predict_eligibility(c, variant, extract)
    if suite == "comparison":
        return lambda c: predict_comparison(c, variant, generate, ka)
    return lambda c: predict_fulfilment(c, variant)


def score_case(suite: str, outputs: dict, exp: dict) -> dict:
    import scorers
    if suite == "fulfilment":
        return {"fulfilment_failures": int(outputs != exp)}
    if suite == "eligibility" and "shortlist" in outputs:          # AI-22: a Lakeside shortlist case
        return {"eligibility_case_failures": int(not scorers.no_ineligible_options(outputs, exp)),
                "eligibility_violations": scorers.eligibility_violations(outputs, exp),
                "unknown_as_match": scorers.unknown_as_match(outputs, exp)}
    return {fn.__name__: fn(outputs, exp) for fn in scorers.SUITE_SCORERS[suite]}


def run(suite: str, variant: str = "hybrid_section", generate=None, ka=None, extract=None) -> dict:
    """Every case of a suite, scored; metrics are means, gates are sums (must be 0)."""
    import scorers
    cases = load(suite)
    predict = predictor(suite, variant, generate, ka, extract)
    per_case = []
    for c in cases:
        out = predict(c)
        per_case.append({"case_id": c["case_id"]} | score_case(suite, out, expectations(suite, c)))
    names = [k for k in per_case[0] if k != "case_id"] if per_case else []
    gate_names = [n for n in names if n in scorers.GATES or n == "fulfilment_failures"]
    metrics = {n: round(sum(p[n] for p in per_case) / len(per_case), 4) for n in names if n not in gate_names}
    gates = {n: sum(p[n] for p in per_case) for n in gate_names}
    failing = [p for p in per_case if any(p[g] for g in gate_names)]
    return {"suite": suite, "variant": variant, "cases": len(cases), "metrics": metrics, "gates": gates,
            "passed": not any(gates.values()), "failing_cases": failing[:20], "per_case": per_case}


def run_mlflow(suite: str, variant: str, experiment: str, generate=None, ka=None) -> dict:
    """The same suite through mlflow.genai.evaluate: one run per variant in the experiment, the per-case view in
    MLflow. Verify before recording: the data format ({"inputs", "expectations"}), predict_fn receiving the inputs
    as keyword arguments, and results.metrics names."""
    import mlflow
    import scorers
    mlflow.set_experiment(experiment)
    predict = predictor(suite, variant, generate, ka)
    data = [{"inputs": {"case": c}, "expectations": expectations(suite, c)} for c in load(suite)]
    with mlflow.start_run(run_name=f"{suite}-{variant}"):
        mlflow.log_params({"suite": suite, "variant": variant})
        results = mlflow.genai.evaluate(data=data, predict_fn=lambda case: predict(case),
                                        scorers=scorers.mlflow_scorers(suite))
    metrics = dict(results.metrics)
    gates = {k: v for k, v in metrics.items() if k.split("/")[0] in scorers.GATES and k.endswith("/sum")}
    return {"suite": suite, "variant": variant, "metrics": metrics, "gates": gates,
            "passed": not any(gates.values()), "run_id": getattr(results, "run_id", None)}
