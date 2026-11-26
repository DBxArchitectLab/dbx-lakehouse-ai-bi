"""The scorers of the Search, Eligibility and Comparison suites (AI-17). Plain functions first, so they are tested
on toy data (tests/quote_ai/test_scorers.py: a scorer bug would hide real bugs); mlflow_scorers() wraps them as
MLflow 3 GenAI scorers for mlflow.genai.evaluate.

Two kinds, never blended into one score:
  gates (zero tolerance)  eligibility_violations, eligibility_case_failures, truth_layer_violations,
                          cross_account_violations, no_evidence_failures: any count above 0 fails the run
  metrics (must not drop) recall_at_5, citation_accuracy, source_recall; the grounding judge (an LLM, with a human
                          reviewing a sample of its decisions in the review app)

Every scorer takes (outputs, expectations) dicts, as mlflow.genai scorers do.
"""
from __future__ import annotations

import re

GATES = ("eligibility_violations", "eligibility_case_failures", "truth_layer_violations", "cross_account_violations",
         "no_evidence_failures", "unknown_as_match")
NO_EVIDENCE = "no evidence on file"
PUBLIC = "public"
_WORD = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
_STOP = {"the", "a", "an", "of", "and", "or", "is", "are", "it", "its", "to", "for", "in", "on", "with", "by", "from",
         "says", "say", "claim", "not", "verified", "glove", "gloves", "description", "sheet", "no", "file", "so"}


# ------------------------------------------------------------------ search

def recall_at_k(got: list[str], expected: list[str], k: int = 5) -> float:
    """Of the products that should be found, the share in the top k. Nothing expected -> 1.0 (nothing to miss)."""
    exp = set(expected)
    return 1.0 if not exp else len(set(got[:k]) & exp) / len(exp)


def recall_at_5(outputs: dict, expectations: dict) -> float:
    return recall_at_k(outputs["sku_ids"], expectations["expected_skus"], 5)


# ------------------------------------------------------------------ eligibility

def eligibility_violations(outputs: dict, expectations: dict) -> int:
    """Safety, zero tolerance: the number of products that came back eligible although they must not be offered,
    or with an unknown check (unknown never becomes a match), or with a check that matched only on evidence rows
    that aren't verified (an extracted claim deciding). A product counts once."""
    unverified = set(outputs.get("unverified_sources", []))
    must_not = set(expectations.get("must_not_include_products", []))
    bad = set()
    for r in outputs["results"]:
        if r["status"] != "eligible":
            continue
        if r["product_id"] in must_not or any(c["result"] == "unknown" for c in r["checks"]) or any(
                c["result"] == "verified_match" and c.get("evidence") and set(c["evidence"]) <= unverified
                for c in r["checks"]):
            bad.add(r["product_id"])
    return len(bad)


def eligibility_case_failures(outputs: dict, expectations: dict) -> int:
    """One eligibility case: every requirement's expected state (M, X, ?) and the status. Zero tolerance: each
    difference counts, in either direction."""
    code = {"verified_match": "M", "verified_mismatch": "X", "unknown": "?"}
    r = outputs["result"]
    got = {c["requirement"]: code[c["result"]] for c in r["checks"]}
    misses = sum(1 for req, want in expectations.get("expect", {}).items() if got.get(req) != want)
    extra = len(set(got) - set(expectations.get("expect", {}))) if expectations.get("expect") else 0
    return misses + extra + int(r["status"] != expectations["status"])


# ------------------------------------------------------------------ comparison

def is_verified(citation: str, verified_sources: set[str]) -> bool:
    """A business-truth citation is fine only if it is a verified source: a table cell or a chunk the predict
    function marked verified from the evidence tables (never from the model)."""
    return citation in verified_sources


def truth_layer_violations(outputs: dict, expectations: dict) -> int:
    """Zero tolerance: every statement has a citation and a layer, and a business_truth label points to verified
    evidence."""
    verified = set(outputs.get("verified_sources", []))
    return sum(1 for st in outputs["statements"]
               if not st.get("citation") or not st.get("layer")
               or (st["layer"] == "business_truth" and not is_verified(st["citation"], verified)))


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) > 1}


def supports(statement: str, source_text: str, threshold: float = 0.6) -> bool:
    """A deterministic support check: most of the statement's content words are in the cited source."""
    words = _words(statement)
    return bool(words) and len(words & _words(source_text)) / len(words) >= threshold


def citation_accuracy(outputs: dict, expectations: dict) -> float:
    """The share of statements whose cited source actually supports them (a citation does not count because it
    exists). Rule and table citations are checked against the text the predict function resolved for them."""
    st = outputs["statements"]
    if not st:
        return 1.0
    texts = outputs.get("source_texts", {})
    return sum(supports(s["text"], texts.get(s["citation"], "")) for s in st) / len(st)


def source_recall(outputs: dict, expectations: dict) -> float:
    """Of the documents the answer should cite, the share it cites."""
    want = set(expectations.get("expected_sources", []))
    if not want:
        return 1.0
    got = {c.split(":")[0] for c in (s["citation"] for s in outputs["statements"])}
    return len(got & want) / len(want)


def cross_account_violations(outputs: dict, expectations: dict) -> int:
    """Zero tolerance: a retrieved chunk of an account the caller doesn't own (it would have reached the prompt)."""
    allowed = set(expectations.get("allowed_accounts", [])) | {PUBLIC}
    return sum(1 for c in outputs.get("retrieved", []) if c.get("account_id", PUBLIC) not in allowed)


def no_evidence_failures(outputs: dict, expectations: dict) -> int:
    """Zero tolerance: when the documents can't answer, the answer says "no evidence on file", not a guess."""
    if not expectations.get("expect_no_evidence"):
        return 0
    return int(outputs["answer"].strip().lower() != NO_EVIDENCE)


SUITE_SCORERS = {
    "search": (recall_at_5, eligibility_violations),
    "eligibility": (eligibility_case_failures, eligibility_violations),
    "comparison": (truth_layer_violations, cross_account_violations, no_evidence_failures, citation_accuracy,
                   source_recall),
}


def mlflow_scorers(suite: str, judges: bool = True) -> list:
    """The suite's scorers as MLflow GenAI scorers, plus the built-in groundedness judge for the comparison suite.
    Verify before recording: mlflow.genai.scorers.scorer, the built-in judge's name (RetrievalGroundedness) and what
    it needs from the trace (retriever spans), and the metric names in results.metrics ("<name>/mean", "/sum")."""
    from mlflow.genai.scorers import scorer
    out = []
    for fn in SUITE_SCORERS[suite]:
        def wrap(outputs, expectations, _fn=fn):
            return _fn(outputs, expectations)
        wrap.__name__ = fn.__name__
        out.append(scorer(wrap))           # the metric takes the function's name
    if suite == "comparison" and judges:
        try:
            out.append(grounded_judge())           # AI-22: the guideline judge the release rule reads (grounded_rate)
        except Exception:  # noqa: BLE001  judge not available in this MLflow
            pass
        try:
            from mlflow.genai.scorers import RetrievalGroundedness
            out.append(RetrievalGroundedness())
        except Exception:  # noqa: BLE001  judge not available in this MLflow
            pass
    return out


# ------------------------------------------------------------------ Season 5 (AI-18 to AI-21): the agent workflow suite
# Every scorer takes (outputs, case): outputs from season5.runner (tools called, results with their source,
# proposals, the answer, violations of the rule outputs, whether the quote changed); case from workflow/cases.jsonl.

SEASON5_GATES = ("rule_fidelity_violations", "state_changes", "unsourced_results", "pricing_failures",
                 "reliability_failures")


def tool_choice_accuracy(outputs: dict, case: dict) -> float:
    """1 when the agent called the right tools: the expected first tool, every expected tool, no forbidden one."""
    tools = outputs["tools"]
    ok = set(case.get("expect_tools", [])) <= set(tools) and not set(case.get("forbid_tools", [])) & set(tools)
    if case.get("expect_first_tool"):
        ok = ok and bool(tools) and tools[0] == case["expect_first_tool"]
    return float(ok)


def task_success(outputs: dict, case: dict) -> float:
    """1 when the answer says what it must (and nothing it must not), and the proposal targets the expected state."""
    a = outputs["answer"]
    ok = all(p in a for p in case.get("expect_in_answer", [])) and \
        not any(p in a for p in case.get("expect_not_in_answer", []))
    if case.get("expect_proposal"):
        ok = ok and case["expect_proposal"] in outputs["proposals"]
    return float(ok)


def groundedness(outputs: dict, case: dict) -> float:
    """1 when every fact came from a tool result with a source and the answer matched the rule outputs."""
    return float(not outputs["violations"] and all(r["source"] for r in outputs["results"]))


def rule_fidelity_violations(outputs: dict, case: dict) -> int:
    """Zero tolerance: prices or eligibility in the answer that the tools did not return."""
    return len(outputs["violations"])


def state_changes(outputs: dict, case: dict) -> int:
    """Zero tolerance: the agent changed the quote (state, version or revision) outside QuoteService."""
    return int(outputs["state_changed"])


def unsourced_results(outputs: dict, case: dict) -> int:
    """Zero tolerance: a tool result without a source (the badge would have nothing to say)."""
    return sum(1 for r in outputs["results"] if not r["source"])


SEASON5_SCORERS = (tool_choice_accuracy, task_success, groundedness, rule_fidelity_violations, state_changes,
                   unsourced_results)


# ------------------------------------------------------------------ AI-22: the evaluation gate
# The scorers the release rule (v1/thresholds.yml) names that Seasons 4 and 5 did not have yet. Same contract:
# plain functions of (outputs, expectations), unit-tested in tests/quote_ai/test_scorers.py, wrapped for MLflow by
# mlflow_scorers().

def no_ineligible_options(outputs: dict, expectations: dict) -> bool:
    """The Lakeside shortlist case (elig-007): no SKU that must be excluded is on the shortlist, and no check that is
    unknown was counted as a match. No sampling, no opinion."""
    shortlisted = {o["sku_id"] for o in outputs["shortlist"]}
    if shortlisted & set(expectations.get("must_exclude_skus", [])):
        return False
    for o in outputs["shortlist"]:
        for c in o["checks"]:
            if c["state"] == "unknown" and c["counted_as_match"]:
                return False
    return True


def unknown_as_match(outputs: dict, expectations: dict) -> int:
    """Zero tolerance: checks whose result is unknown on a product that came back eligible (unknown counted as a
    match). eligibility_violations counts the product; this counts the checks, so the report names them."""
    return sum(1 for r in outputs["results"] if r["status"] == "eligible"
               for c in r["checks"] if c["result"] == "unknown")


def layer_cited(outputs: dict, expectations: dict) -> float:
    """The share of statements that cite a source and name a truth layer, a business_truth one pointing to verified
    evidence. The release rule wants 1.0 (truth_layer_violations, as a share)."""
    st = outputs["statements"]
    if not st:
        return 1.0
    return 1 - truth_layer_violations(outputs, expectations) / len(st)


def judge_human_agreement(pairs: list[tuple[bool, bool]]) -> float | None:
    """How often the judge agrees with a person on the sample a reviewer labelled in the review app: (judge, human)
    pairs. None when nobody has labelled yet (the gate reports not_run, not a pass)."""
    if not pairs:
        return None
    return sum(1 for j, h in pairs if bool(j) == bool(h)) / len(pairs)


GROUNDED_GUIDELINES = [
    "Every product claim cites a source document or GUDID record.",
    "Unresolved evidence is stated, not hidden.",
]


def grounded_judge():
    """The comparison suite's guideline judge (grounded_comparison). Its model calls go through Unity Gateway (the
    judge endpoint has its own rate limit). Verify before recording: mlflow.genai.scorers.Guidelines(name=,
    guidelines=) and how the judge model is pointed at a gateway endpoint (model="databricks:/<endpoint>")."""
    from mlflow.genai.scorers import Guidelines
    import os
    kw = {"model": f"databricks:/{os.environ['QUOTE_AI_JUDGE_ENDPOINT']}"} \
        if os.environ.get("QUOTE_AI_JUDGE_ENDPOINT") else {}
    return Guidelines(name="grounded_comparison", guidelines=GROUNDED_GUIDELINES, **kw)


# ------------------------------------------------------------------ AI-22: the agent's path (workflow suite)
# The path matters: did it check eligibility before pricing or explaining, and did every state change go through
# QuoteService along an allowed move?

ELIGIBILITY_FIRST = ("price_quote", "knowledge_search")


def tool_order_ok(outputs: dict, case: dict) -> float:
    """1 when, in a turn that checks eligibility, nothing was priced or explained before check_eligibility ran."""
    tools = outputs["tools"]
    if "check_eligibility" not in tools:
        return 1.0
    first = tools.index("check_eligibility")
    return float(not any(t in ELIGIBILITY_FIRST for t in tools[:first]))


def eligibility_before_compare(trace) -> bool:
    """The same rule on an MLflow trace (the eval job): check_eligibility ran, and before any comparison or price.
    Verify before recording: trace.search_spans() and the span names of the tool calls (the tool name)."""
    names = [s.name for s in trace.search_spans()]
    if "check_eligibility" not in names:
        return False
    first = names.index("check_eligibility")
    return not any(n in ELIGIBILITY_FIRST for n in names[:first])


def illegal_transitions(outputs: dict, case: dict) -> int:
    """Zero tolerance: quote events whose move the state machine (quote_states.ALLOWED) does not allow."""
    from quote_ai.domain.models import QuoteState as S
    from quote_ai.rules.quote_states import ALLOWED
    return sum(1 for f, t in outputs.get("moves", [])
               if f is not None and f != t and S(t) not in ALLOWED.get(S(f), set()))


SUITE_SCORERS["eligibility"] = (eligibility_case_failures, eligibility_violations, unknown_as_match)

# The workflow suite from AI-22: Season 5's scorers plus the path checks the release rule names.
WORKFLOW_SCORERS = SEASON5_SCORERS + (tool_order_ok, illegal_transitions)
WORKFLOW_GATES = SEASON5_GATES + ("illegal_transitions",)
SUITE_SCORERS["comparison"] = SUITE_SCORERS["comparison"] + (layer_cited,)
