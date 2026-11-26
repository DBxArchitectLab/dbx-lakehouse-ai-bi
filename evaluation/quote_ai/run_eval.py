"""The evaluation runner: one command per suite, deterministic assertions first.

  --suite requirements   AI-05: the extraction on 30 requests, field by field (needs a model endpoint)
  --suite mapping        AI-03: the each, box and case fixtures (no model)
  --suite product_facts  AI-08: attribute accuracy per attribute on 100 hand-labelled gloves (needs the extraction
                         model service), zero AI rows verified, zero structured values overwritten; AI-09: field
                         accuracy of the supplier documents against their truth file (--document-values: a JSON file
                         {path: {label: value}} exported from quote_ai_product.document_values)
  --suite genie          AI-11: the 40 benchmark questions through the Genie Agent, compared on results (needs the
                         workspace: --space-id and --warehouse-id)
  --suite eligibility    AI-15, AI-17: 60 cases, an expectation per contract requirement, each on its evidence snapshot;
                         zero eligibility_case_failures and eligibility_violations (pure code: runs in CI)
  --suite fulfilment     AI-15: full, partial, backorder, alternative date; a late line is never on time
  --suite search         AI-17: 50 requests, Recall@5 (a metric) and zero eligibility violations (a gate);
                         --variant baseline | hybrid | hybrid_section | hybrid_rerank (| leaky_summary: must fail)
  --suite comparison     AI-16, AI-17: 30 questions; zero truth-layer, cross-account and no-evidence failures; citation
                         accuracy and source recall. --variant build (the RAG chain; --endpoint for the chat model, else
                         the extractive stand-in) | buy (--ka-endpoint) | a retriever variant
  --mlflow               run through mlflow.genai.evaluate into --experiment (default /Shared/quote_ai/eval), one run
                         per variant; without it the same scorers run locally and print
  --suite pricing        AI-18, AI-21: price_quote (units, tiers, margin floor, effective dates) and the discount
                         bands; zero tolerance (pure code: runs in CI)
  --suite reliability    AI-18 to AI-21: timeouts, retries, no progress, duplicates, partial failure, the GUDID
                         fallback, stale data, concurrent approvals, double issue; zero tolerance (runs in CI)
  --suite workflow       AI-18 to AI-21: the agent's tool choice, the boundary, resume, fallback, 100 end-to-end
                         requests; --design one_agent | specialists, --variant overlapping (AI-19's break),
                         --kinds tool_choice,e2e,...; the stand-in model unless --endpoint (the chat model service)

  python evaluation/quote_ai/run_eval.py --suite requirements --target dev --catalog dbxarchitectlab_dev
  python evaluation/quote_ai/run_eval.py --suite requirements --prompt-version v2   # an unregistered version
  python evaluation/quote_ai/run_eval.py --suite mapping
  python evaluation/quote_ai/run_eval.py --suite product_facts --target dev [--document-values values.json]
  python evaluation/quote_ai/run_eval.py --suite genie --target dev --catalog dbxarchitectlab_dev --space-id ID
      --warehouse-id ID
  python evaluation/quote_ai/run_eval.py --suite eligibility
  python evaluation/quote_ai/run_eval.py --suite search --variant hybrid_rerank [--mlflow]
  python evaluation/quote_ai/run_eval.py --suite comparison --variant build --endpoint quote_ai_chat_dev
  python evaluation/quote_ai/run_eval.py --suite comparison --variant buy --ka-endpoint quote_ai_knowledge_assistant
  python evaluation/quote_ai/run_eval.py --suite pricing
  python evaluation/quote_ai/run_eval.py --suite workflow --kinds tool_choice --variant overlapping
  python evaluation/quote_ai/run_eval.py --suite workflow --endpoint quote_ai_chat_dev --design specialists

AI-22, the evaluation gate: every suite of the package in one run, against one release rule (v1/thresholds.yml):

  --suites ci            the pure-code suites (every pull request): ingestion, mapping, win_model, eligibility,
                         fulfilment, search, comparison (deterministic part), pricing, workflow, reliability
  --suites all           the quote_ai_eval job: ci + the workspace suites (requirements, product_facts, genie) and the
                         judge (--judge) and review labels (--review-labels); needs --catalog
  --suites a,b,...       named suites (model_access with --outcomes; data_authorization)
  --results-table T      append one row per suite and metric (git SHA, package version, cost) to T
                         (<catalog>.quote_ai_eval.eval_results in the job)
Exit code 1 when a required suite misses a threshold: the gate. The new single suites: --suite ingestion | win_model
| model_access (--outcomes) | data_authorization | operations (AI-26).

  python evaluation/quote_ai/run_eval.py --suites ci
  python evaluation/quote_ai/run_eval.py --suites all --catalog dbxarchitectlab_dev --target dev-shared --judge \
      --space-id ID --warehouse-id ID --results-table dbxarchitectlab_dev.quote_ai_eval.eval_results

Exit code 1 when the suite misses its target (requirements: 95% field accuracy; mapping: zero tolerance; product
facts: 90% per attribute and zero status violations; genie: 90% of the cases).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))
SUITES = HERE / "v1"
UNORDERED = {"missing", "sizes", "preferences"}


def _same(key: str, got, want) -> bool:
    if key in UNORDERED and isinstance(got, list) and isinstance(want, list):
        return sorted(got) == sorted(want)
    if key == "target_price" and isinstance(got, dict) and isinstance(want, dict):
        return all(abs(got.get(k) - v) < 1e-6 if isinstance(v, float) else got.get(k) == v for k, v in want.items())
    if isinstance(want, float) and isinstance(got, (int, float)):
        return abs(got - want) < 1e-6
    return got == want


def check(pred: dict, expected: dict) -> list[str]:
    """Each expected field against the prediction: the differences, one line each."""
    errors = []
    for k, v in expected.items():
        if not _same(k, pred.get(k), v):
            errors.append(f"{k}: expected {v!r}, got {pred.get(k)!r}")
    return errors


def load(suite: str, file: str = "cases.jsonl") -> list[dict]:
    path = SUITES / suite / file
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def score_requirements(cases: list[dict], predict: Callable[[str], dict]) -> dict:
    """Field accuracy over every expected field of every case; a case whose answer fails the schema scores 0."""
    total = right = 0
    failures = []
    for c in cases:
        try:
            pred = predict(c["request"])
            errors = check(pred, c["expected"])
            wrong = len(errors)
        except Exception as e:  # noqa: BLE001  a schema failure is a failed case, not a crash of the suite
            errors, wrong = [f"no valid answer: {e}"], len(c["expected"])
        total += len(c["expected"])
        right += len(c["expected"]) - wrong
        if errors:
            failures.append({"id": c["id"], "errors": errors})
    return {"cases": len(cases), "fields": total, "field_accuracy": right / max(total, 1), "failures": failures}


def run_requirements(a) -> dict:
    extract = _extractor(a)
    return score_requirements(load("requirements"), lambda text: extract(text).model_dump(mode="json"))


def run_mapping() -> dict:
    from quote_ai.product.mapping_review import auto_match, offerable, verify
    from datetime import datetime, timezone
    failures = []
    cases = load("mapping")
    for c in cases:
        e = c["expected"]
        m = auto_match(c["sku"], c["package_di"], c["catalog_number"], c["levels"], c["primary_di"])
        errs = check({"unit_level": m.unit_level, "gloves_per_unit": m.gloves_per_unit,
                      "mapping_confidence": m.mapping_confidence.value},
                     {k: e[k] for k in ("unit_level", "gloves_per_unit", "mapping_confidence")})
        if offerable(m):
            errs.append("offerable before a person verified it")
        try:
            ok = offerable(verify(m, "suite", datetime.now(timezone.utc)))
        except ValueError:
            ok = False
        if ok != e["verifiable"]:
            errs.append(f"verifiable: expected {e['verifiable']}, got {ok}")
        if errs:
            failures.append({"id": c["id"], "errors": errs})
    return {"cases": len(cases), "failures": failures, "passed": not failures}


# ---------------------------------------------------------------- product facts (AI-08, AI-09)

ATTRIBUTE_FIELDS = ["material", "powder_free", "textured", "length_in", "chemo_claim", "use_type", "latex_free"]


def score_attributes(cases: list[dict], predict: Callable[[str], dict]) -> dict:
    """Per-attribute accuracy against the hand labels (null is a valid answer), and the status invariants on the
    evidence rows the predictions would write: no AI row verified, no structured value overwritten."""
    from datetime import datetime, timezone

    from quote_ai.product import evidence
    right = {f: 0 for f in ATTRIBUTE_FIELDS}
    failures, violations = [], []
    now = datetime.now(timezone.utc)
    for c in cases:
        try:
            pred = predict(c["description"])
        except Exception as e:  # noqa: BLE001  a schema failure is a failed case
            failures.append({"id": c["id"], "errors": [f"no valid answer: {e}"]})
            continue
        errs = []
        for f in ATTRIBUTE_FIELDS:
            got, want = evidence.normalize(f, pred.get(f)), c["expected"].get(f)
            if _same(f, got, want):
                right[f] += 1
            else:
                errs.append(f"{f}: expected {want!r}, got {got!r}")
        if errs:
            failures.append({"id": c["id"], "errors": errs})
        di = c.get("di") or c["id"]
        structured = evidence.gudid_rows(di, {"labeled_no_nrl": "true"}, now)
        violations += evidence.invariants(evidence.evidence_rows(di, {"labeled_no_nrl": "true"}, pred, now),
                                          structured)
    n = max(len(cases), 1)
    return {"cases": len(cases), "accuracy": {f: right[f] / n for f in ATTRIBUTE_FIELDS},
            "status_violations": violations, "failures": failures}


def score_documents(truth: list[dict], values: dict[str, dict]) -> dict:
    """Field accuracy of the extracted supplier documents against the generator's truth. Units are converted the way
    the evidence does it: a thickness in mil counts only once converted to mm."""
    from quote_ai.product import evidence
    fields: dict[str, list[int]] = {}
    failures = []
    for t in truth:
        got = next((v for p, v in values.items() if p.endswith(t["path"])), None)
        if got is None:
            failures.append({"id": t["id"], "errors": ["not extracted"]})
            continue
        v = evidence.parse_extracted(got)
        pred = {"aql": v.get("aql"), "drugs": v.get("drugs"), "results": v.get("results"),
                "standard": v.get("standard"), "catalog_numbers": v.get("catalog_numbers"),
                "chemo_claim": evidence._bool(v.get("chemo_claim")),
                "material": evidence.normalize("material", v.get("material")),          # AI-15
                "powder_free": evidence.normalize("powder_free", v.get("powder_free"))}
        if v.get("thickness"):
            pred["thickness_mm"] = evidence.check_value("thickness", *evidence.parse_quantity(v["thickness"]))[0]
        if v.get("tensile_strength"):
            pred["tensile_mpa"] = evidence.parse_quantity(v["tensile_strength"])[0]
        errs = []
        for k, want in t["expected"].items():
            if k == "prices":
                continue                      # price lists: scored in the method comparison, not here
            ok = abs((pred.get(k) or 0) - want) < 1e-3 if isinstance(want, float) else pred.get(k) == want
            fields.setdefault(k, []).append(int(ok))
            if not ok:
                errs.append(f"{k}: expected {want!r}, got {pred.get(k)!r}")
        if errs:
            failures.append({"id": t["id"], "errors": errs})
    return {"documents": len(truth), "accuracy": {k: sum(v) / len(v) for k, v in fields.items()},
            "failures": failures}


def run_product_facts(a) -> tuple[dict, bool]:
    from quote_ai.llm import endpoints
    from quote_ai.llm.attributes import extract_attributes
    endpoint = a.endpoint or endpoints.model(endpoints.EXTRACT)
    r = {"attributes": score_attributes(load("product_facts"), lambda d: extract_attributes(d, endpoint))}
    ok = not r["attributes"]["status_violations"] and min(r["attributes"]["accuracy"].values()) >= a.min_accuracy
    if a.document_values:
        truth = load("product_facts", "documents.jsonl")
        r["documents"] = score_documents(truth, json.loads(Path(a.document_values).read_text(encoding="utf-8")))
    return r, ok


# ---------------------------------------------------------------- genie (AI-11)


def _canon(rows: list) -> list[tuple]:
    """A result set compared on values: rows as tuples, numbers rounded, text lower-case."""
    def cell(v):
        if isinstance(v, bool) or v is None:
            return v
        try:
            return round(float(v), 4)
        except (TypeError, ValueError):
            return str(v).strip().lower()
    return [tuple(cell(v) for v in (r.values() if isinstance(r, dict) else r)) for r in rows]


def same_result(got: list, want: list, ordered: bool = False) -> bool:
    g, w = _canon(got), _canon(want)
    return g == w if ordered else sorted(g, key=repr) == sorted(w, key=repr)


def score_genie(cases: list[dict], ask: Callable[[str], dict], run_sql: Callable[[str], list]) -> dict:
    """Each case: Genie's SQL and the expected SQL both run, and the results are compared (not the SQL text). A failed
    case keeps Genie's SQL to inspect. ask(question) -> {"sql": ..., "rows": [...] (optional)}."""
    passed, failures = 0, []
    for c in cases:
        got: dict = {}
        try:
            got = ask(c["question"])
            got_rows = got.get("rows")
            if got_rows is None:
                got_rows = run_sql(got["sql"]) if got.get("sql") else []
            ok = same_result(got_rows, run_sql(c["expected_sql"]), c.get("compare") == "ordered_result")
        except Exception as e:  # noqa: BLE001
            got, ok = got | {"error": str(e)}, False
        if ok:
            passed += 1
        else:
            failures.append({"id": c["id"], "question": c["question"], "genie_sql": got.get("sql"),
                             "error": got.get("error")})
    return {"cases": len(cases), "score": passed / max(len(cases), 1), "failures": failures}


def run_genie(a) -> tuple[dict, bool]:
    from quote_ai.semantic.genie import client as genie
    from quote_ai.storage import sql_files
    from quote_ai.storage.delta_repository import DatabricksSql
    names = sql_files.names_for(a.catalog, dict(p.split("=", 1) for p in (a.schemas or "").split(",") if p))
    sql = DatabricksSql(a.warehouse_id)
    cases = [c | {"expected_sql": sql_files.render(c["expected_sql"], **names)} for c in load("genie")]
    r = score_genie(cases, lambda q: genie.ask(a.space_id, q), sql.query)
    return r, r["score"] >= a.min_score


# ---------------------------------------------------------------- Season 4 (AI-15 to AI-17)


def run_season4(a) -> dict:
    """Separate suites, separate thresholds: a gate above zero fails the run whatever the metrics say."""
    sys.path.insert(0, str(HERE))
    import season4
    generate = ka = None
    variant = a.variant
    if a.suite == "comparison":
        if variant == "build":
            variant = "hybrid_section"
            if a.endpoint:
                from quote_ai.llm import rag
                generate = rag.llm_generate(a.endpoint)
        elif variant == "buy":
            if not a.ka_endpoint:
                raise SystemExit("--variant buy needs --ka-endpoint")
            from quote_ai.llm import knowledge_assistant
            ka = lambda q: knowledge_assistant.ask_ka(q, a.ka_endpoint)  # noqa: E731
            variant = "hybrid_section"
    extract = None
    if a.suite == "eligibility" and a.catalog:          # AI-22: elig-007 reads the request with the production prompt
        extract = _extractor(a)
    if a.mlflow:
        return season4.run_mlflow(a.suite, variant, a.experiment, generate, ka)
    return season4.run(a.suite, variant, generate, ka, extract) | {"variant": a.variant}


def _extractor(a) -> Callable[[str], object]:
    """The extraction as the app runs it: the model service and the prompt by alias (or --prompt-version)."""
    from quote_ai.llm import endpoints, prompts
    from quote_ai.llm.extract import extract_requirements, prompt_uri
    endpoint = a.endpoint or endpoints.model(endpoints.EXTRACT)
    kw = {"template": prompts.VERSIONS[a.prompt_version][0]} if a.prompt_version else         {"uri": prompt_uri(a.catalog, a.schema, a.alias)}
    return lambda text: extract_requirements(text, endpoint, **kw)


def run_season5(a) -> dict:
    """Season 5 (AI-18 to AI-21): gates must be 0; the workflow metrics are reported (the CI gate arrives in AI-22)."""
    sys.path.insert(0, str(HERE))
    import season5
    client, endpoint = None, "standin"
    if a.suite == "workflow" and a.endpoint:
        from quote_ai.llm.client import workspace_client
        client, endpoint = workspace_client(), a.endpoint
    variant = a.variant if a.variant in ("overlapping", "always_playbook") else "default"
    kinds = tuple(k for k in (a.kinds or "").split(",") if k) or None
    return season5.run(a.suite, variant, a.design, client, endpoint, kinds)


# ---------------------------------------------------------------- AI-22: the suites the gate adds

def run_ingestion() -> dict:
    """AI-02's suite, without the framework changing: every bad config refused, and the synthetic release landed twice
    recorded once (the replay guarantee). Reads the framework; never changes it."""
    import tempfile
    from datetime import date

    import yaml
    from quote_ai.ingest.config import IngestConfig, load_config
    from quote_ai.ingest.download import land_source
    from quote_ai.ingest.log import InMemoryLog
    from quote_ai.ingest.synthetic import GENERATORS
    accepted = []
    for f in sorted((SUITES / "ingestion" / "bad_configs").glob("*.yml")):
        try:
            IngestConfig.model_validate(yaml.safe_load(f.read_text(encoding="utf-8")))
            accepted.append(f.name)
        except Exception:  # noqa: BLE001  refused: what the suite wants
            pass
    case = next(c for c in load("ingestion") if c["kind"] == "replay")
    log = InMemoryLog()
    with tempfile.TemporaryDirectory() as root:
        for _ in range(case["times"]):
            land_source(None, load_config().source(case["source"]), Path(root), log,
                        today=date.fromisoformat(case["release_date"]), generators=GENERATORS)
    paths = [r.path for r in log.file_rows]
    return {"bad_config_accepted": len(accepted), "replay_duplicates": len(paths) - len(set(paths)),
            "accepted": accepted}


def run_win_model(a=None) -> dict:
    """AI-13's suite: the selection rule on its cases (CI); with --catalog and --warehouse-id, the latest
    model_selection record of the training job (beats the baseline, recovers the planted signal)."""
    from quote_ai.ml import train
    failures = []
    for c in load("win_model"):
        d = train.decide([train.RunResult(**r) for r in c["candidates"]])
        got = {"status": d["status"], "run_name": d["run_name"]}
        if got != c["expected"]:
            failures.append({"id": c["id"], "expected": c["expected"], "got": got})
    out = {"decision_failures": len(failures), "failures": failures}
    if a is not None and getattr(a, "catalog", None) and getattr(a, "warehouse_id", None):
        out |= latest_selection(a.catalog, a.warehouse_id, a.schemas)
    return out


def latest_selection(catalog: str, warehouse_id: str, schemas: str | None = None) -> dict:
    """The newest quote_ai_ml.model_selection row as the gate's two numbers. Verify before recording: the record's
    columns (status, record JSON with candidates and top_features) as quote_ai.jobs.ml_job writes them."""
    from quote_ai.ml import train
    from quote_ai.storage import sql_files
    from quote_ai.storage.delta_repository import DatabricksSql
    names = sql_files.names_for(catalog, dict(p.split("=", 1) for p in (schemas or "").split(",") if p))
    row = DatabricksSql(warehouse_id).query(
        f"SELECT status, record FROM {names['ml']}.model_selection ORDER BY decided_at DESC LIMIT 1")
    if not row:
        return {}
    rec = json.loads(row[0]["record"]) if isinstance(row[0]["record"], str) else row[0]["record"]
    winner = next((c for c in rec.get("candidates", []) if c["name"] == rec.get("run_name")), None)
    return {"beats_baseline": int(row[0]["status"] == "selected"),
            "signal_recovered": int(bool(winner) and train.recovers_signal(winner.get("top_features", [])))}


def run_model_access(a) -> dict:
    """AI-06's suite, run separately as each identity (notebooks/quote_ai/06_gateway_tests.py). The notebook's
    outcomes, {case id: [outcome, ...]}, come in as --outcomes; here the verdicts. unauthorized: a call that should
    have been refused and was answered."""
    from quote_ai.llm import gateway_checks
    if not getattr(a, "outcomes", None):
        return {}
    got = json.loads(Path(a.outcomes).read_text(encoding="utf-8"))
    cases = gateway_checks.load_cases()
    wrong = [c["id"] for c in cases if not gateway_checks.verdict(c, got.get(c["id"], []))]
    unauthorized = sum(1 for c in cases if c["expected"] != "allowed"
                       for o in got.get(c["id"], [])[c.get("allowed_first", 0):] if o == "allowed")
    return {"unauthorized": unauthorized, "unexpected_outcomes": len(wrong), "failing": wrong}


def run_data_authorization(a=None) -> dict:
    """AI-24: sec-01 to sec-05, each as a named identity through the app's API (evaluation/quote_ai/security.py).
    --variant app_identity (the gap before AI-24) and grant_price_quote_to_knowledge must fail."""
    sys.path.insert(0, str(HERE))
    import security
    variant = getattr(a, "variant", None)
    variant = variant if variant in ("app_identity", "grant_price_quote_to_knowledge") else "default"
    return security.run(variant)


def run_operations(a=None) -> dict:
    """AI-26: the Operations suite (evaluation/quote_ai/operations.py): the daily run, freshness before issue, Glove
    B's report, freshness and cost on fixtures, live scoring. With --catalog and --warehouse-id, also production's
    freshness breaches and whether cost per quote is reported (shown, not gated)."""
    sys.path.insert(0, str(HERE))
    import operations
    return operations.run(getattr(a, "catalog", None), getattr(a, "warehouse_id", None), getattr(a, "schemas", None))


# ---------------------------------------------------------------- AI-22: one runner, one release rule

def _reliability_metrics(r: dict) -> dict:
    by = {p["case_id"]: p["reliability_failures"] for p in r.get("per_case", [])}
    scen = {c["case_id"]: c["scenario"] for c in load("reliability")}
    dup = {"duplicate_lookup_one_call", "proposal_is_idempotent", "double_issue_one_event"}
    def held(*names) -> int:          # every case of these scenarios ran and passed (none ran: not held)
        got = [n for cid, n in by.items() if scen.get(cid) in names]
        return int(bool(got) and not any(got))
    return {"duplicate_effects": sum(n for cid, n in by.items() if scen.get(cid) in dup),
            "stale_issue_refused": held("stale_gudid_blocks_issue"),
            "concurrent_approvals_handled": held("concurrent_approve_one_wins", "stale_version_409"),
            "reliability_failures": r["gates"]["reliability_failures"]}


def collect(name: str, a) -> tuple[dict, dict]:
    """One suite run -> (the metrics the release rule names, the detail for the log). The suite runners stay the one
    place each suite's logic lives; this only renames their outputs to the thresholds' metric names."""
    if name == "ingestion":
        r = run_ingestion()
        return {k: r[k] for k in ("bad_config_accepted", "replay_duplicates")}, r
    if name == "mapping":
        r = run_mapping()
        unverified = sum(1 for f in r["failures"] for e in f["errors"] if e.startswith("offerable before"))
        return {"unverified_offered": unverified, "mapping_failures": len(r["failures"])}, r
    if name == "requirements":
        r = run_requirements(a)
        return {"field_accuracy": r["field_accuracy"]}, r
    if name == "model_access":
        r = run_model_access(a)
        return {k: r[k] for k in ("unauthorized", "unexpected_outcomes") if k in r}, r
    if name == "product_facts":
        r, _ = run_product_facts(a)
        m = {"status_violations": len(r["attributes"]["status_violations"]),
             "min_attribute_accuracy": min(r["attributes"]["accuracy"].values())}
        if "documents" in r:
            acc = r["documents"]["accuracy"]
            m["match_rate"] = min(acc.values()) if acc else 0.0
        return m, r
    if name == "genie":
        r, _ = run_genie(a)
        return {"benchmark": r["score"]}, r
    if name == "win_model":
        r = run_win_model(a)
        return {k: r[k] for k in ("decision_failures", "beats_baseline", "signal_recovered") if k in r}, r
    if name in ("eligibility", "fulfilment", "search", "comparison"):
        a.suite = name
        r = run_season4(a)
        g, m = r["gates"], r["metrics"]
        if name == "eligibility":
            return {k: g[k] for k in ("eligibility_violations", "unknown_as_match", "eligibility_case_failures")}, r
        if name == "fulfilment":
            return {"wrong_decision": g["fulfilment_failures"]}, r
        if name == "search":
            return {"recall_at_5": m["recall_at_5"], "eligibility_violations": g["eligibility_violations"]}, r
        out = {"layer_cited": m["layer_cited"], "cross_account_violations": g["cross_account_violations"],
               "no_evidence_failures": g["no_evidence_failures"]}
        if getattr(a, "judge", False):
            j = run_judge(a)
            out["grounded_rate"] = j.get("grounded_rate")
            r["judge"] = j
        if getattr(a, "review_labels", None):
            import scorers
            labels = json.loads(Path(a.review_labels).read_text(encoding="utf-8"))
            out["judge_human_agreement"] = scorers.judge_human_agreement([(l["judge"], l["human"]) for l in labels])
        return out, r
    if name in ("pricing", "reliability", "workflow"):
        a.suite = name
        r = run_season5(a)
        if name == "pricing":
            return {"errors": r["gates"]["pricing_failures"]}, r
        if name == "reliability":
            return _reliability_metrics(r), r
        g = r["gates"]
        return {"illegal_transitions": g["illegal_transitions"], "commits_outside_quote_service": g["state_changes"],
                "rule_fidelity_violations": g["rule_fidelity_violations"],
                "tool_order_ok": r["metrics"]["tool_order_ok"]}, r
    if name == "data_authorization":
        r = run_data_authorization(a)
        return {k: r[k] for k in ("unauthorized",) if k in r}, r
    if name == "operations":
        sys.path.insert(0, str(HERE))
        import operations
        r = run_operations(a)
        return {k: r[k] for k in operations.METRICS}, r
    return {}, {"note": f"{name}: not run by the gate"}


def run_judge(a) -> dict:
    """The comparison suite through mlflow.genai.evaluate with the grounded_comparison guideline judge (the eval job:
    judge calls through Unity Gateway, QUOTE_AI_JUDGE_ENDPOINT). Verify before recording: the judge's metric name in
    results.metrics ("grounded_comparison/mean") and its value type (yes/no -> a share)."""
    sys.path.insert(0, str(HERE))
    import season4
    r = season4.run_mlflow("comparison", "hybrid_section", a.experiment)
    return {"grounded_rate": r["metrics"].get("grounded_comparison/mean"), "run_id": r.get("run_id"),
            "model_calls": len(load("comparison"))}


def run_gate(a) -> tuple[list[dict], bool]:
    """Every selected suite, checked against the release rule. A suite that crashes produces no metrics, so each of
    its thresholds fails as not produced: a broken suite never passes."""
    sys.path.insert(0, str(HERE))
    import gate
    suites = gate.load_thresholds()
    have = {k for k, on in (("judge", a.judge), ("review", bool(a.review_labels)),
                            ("workspace", bool(a.catalog))) if on}
    results, costs = [], {}
    for name in gate.select(suites, a.suites):
        if suites[name]["runs"] == "monitored":
            continue
        try:
            metrics, detail = collect(name, a)
        except Exception as e:  # noqa: BLE001  reported, and fails the suite
            metrics, detail = {}, {"error": f"{type(e).__name__}: {e}"}
        res = gate.check(name, suites[name], metrics, frozenset(have))
        if "error" in detail:
            res["error"] = detail["error"]
        if "judge" in detail:
            costs[name] = {"model_calls": detail["judge"].get("model_calls")}
        results.append(res)
    if a.results_table:
        gate.write_results(gate.result_rows(results, costs=costs), a.results_table)
    return results, gate.verdict(results)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--suite", choices=["requirements", "mapping", "product_facts", "genie", "eligibility",
                                        "fulfilment", "search", "comparison", "pricing", "reliability", "workflow",
                                        "ingestion", "win_model", "model_access", "data_authorization",
                                        "operations"])
    p.add_argument("--suites", help="AI-22 gate: ci | all | required | a comma list (v1/thresholds.yml)")
    p.add_argument("--judge", action="store_true", help="gate: run the comparison judge (MLflow, Unity Gateway)")
    p.add_argument("--review-labels", help="gate: JSON [{judge, human}] from the review app's labeling session")
    p.add_argument("--results-table", help="gate: append the results to this table (catalog.schema.eval_results)")
    p.add_argument("--outcomes", help="model_access: JSON {case id: [outcome, ...]} from the gateway notebook")
    p.add_argument("--judge-endpoint", help="gate: the judge's model service (Unity Gateway); QUOTE_AI_JUDGE_ENDPOINT")
    p.add_argument("--git-sha", help="gate: the commit evaluated (default: GITHUB_SHA or git rev-parse)")
    p.add_argument("--design", default="one_agent", choices=["one_agent", "specialists"], help="workflow (AI-21)")
    p.add_argument("--kinds", help="workflow: a comma list of case kinds (tool_choice, boundary, resume, fallback, e2e)")
    p.add_argument("--variant", default="hybrid_section", help="Season 4 suites: a retriever variant, build or buy")
    p.add_argument("--mlflow", action="store_true", help="Season 4 suites: through mlflow.genai.evaluate")
    p.add_argument("--experiment", default="/Shared/quote_ai/eval")
    p.add_argument("--ka-endpoint", help="comparison --variant buy: the Knowledge Assistant endpoint")
    p.add_argument("--target", help="bundle target (dev, dev-shared, prod): picks the model service names")
    p.add_argument("--catalog", help="for the registered prompt: <catalog>.<schema>.extract_requirements")
    p.add_argument("--schema", default="quote_ai_ai")
    p.add_argument("--alias", default="champion", help="AI-23: champion (prod) or challenger (an experiment)")
    p.add_argument("--prompt-version", choices=["v1", "v2", "v3", "v4"], help="a local prompt version instead of the alias")
    p.add_argument("--endpoint", help="override the model service (default: quote_ai.llm.endpoints.EXTRACT)")
    p.add_argument("--min-accuracy", type=float, default=None,
                   help="requirements: 0.95 field accuracy; product_facts: 0.90 per attribute")
    p.add_argument("--document-values", help="product_facts: JSON {path: {label: value}} from document_values")
    p.add_argument("--schemas", help="genie: logical=physical schema names (a personal dev copy)")
    p.add_argument("--space-id", help="genie: the Genie Agent (space) ID")
    p.add_argument("--warehouse-id", help="genie: the SQL warehouse that runs the expected SQL")
    p.add_argument("--min-score", type=float, default=0.90)
    return p


def main(argv=None) -> int:
    p = parser()
    a = p.parse_args(argv)
    if a.min_accuracy is None:
        a.min_accuracy = 0.90 if a.suite == "product_facts" else 0.95
    if a.target:
        os.environ["BUNDLE_TARGET"] = a.target
    if a.judge_endpoint:
        os.environ["QUOTE_AI_JUDGE_ENDPOINT"] = a.judge_endpoint
    if a.git_sha:
        os.environ["QUOTE_AI_GIT_SHA"] = a.git_sha
    if a.suites:
        sys.path.insert(0, str(HERE))
        import gate
        results, ok = run_gate(a)
        print(f"QuoteAI evaluation package {gate.package_version()}, git {gate.git_sha()[:12]}")
        print(gate.table(results))
        for r in results:
            if r.get("error"):
                print(f"{r['suite']}: {r['error']}")
        print("GATE", "PASSED" if ok else "FAILED")
        return 0 if ok else 1
    if not a.suite:
        p.error("--suite or --suites is required")
    if a.suite in ("ingestion", "win_model", "model_access", "data_authorization", "operations"):
        sys.path.insert(0, str(HERE))
        import gate
        metrics, r = collect(a.suite, a)
        res = gate.check(a.suite, gate.load_thresholds()[a.suite], metrics)
        print(json.dumps(r | {"checks": res["checks"]}, indent=2, default=str))
        return 0 if res["passed"] else 1
    if a.suite in ("pricing", "reliability", "workflow"):
        r = run_season5(a)
        ok = r["passed"]
        r.pop("per_case", None)
    elif a.suite in ("eligibility", "fulfilment", "search", "comparison"):
        r = run_season4(a)
        ok = r["passed"]
        r.pop("per_case", None)
    elif a.suite == "mapping":
        r = run_mapping()
        ok = r["passed"]
    elif a.suite == "product_facts":
        r, ok = run_product_facts(a)
    elif a.suite == "genie":
        if not (a.catalog and a.space_id and a.warehouse_id):
            p.error("--suite genie needs --catalog, --space-id and --warehouse-id")
        r, ok = run_genie(a)
    else:
        if not a.prompt_version and not a.catalog:
            p.error("--catalog is needed to load the registered prompt (or pass --prompt-version)")
        r = run_requirements(a)
        ok = r["field_accuracy"] >= a.min_accuracy
    print(json.dumps(r, indent=2, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
