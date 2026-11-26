"""Operations fixtures (AI-26): a week of demo traffic and the episode's scenarios, shared by the tests, the
Operations suite and the local demo's Ops page. Synthetic, deterministic, labelled demo data.

  monitoring_data(now, paused_hours=None)   every table the monitoring SQL reads: the AI-02 ingestion log (daily runs
                                            at 06:00; paused: the last success paused_hours ago), the supplier
                                            documents, the inputs' release dates, a week of trace usage, the
                                            compute per day and the live scores
  leaves_distribution(rows, product_id)     today's GUDID daily update: the glove is out of commercial distribution;
                                            the evidence rows as the daily job leaves them (a new record version)
  synced_docs(rows, product_id)             the AI Search documents after the index sync
  report_arrives()                          Glove B's chemo report registered and signed off by QA: the evidence store
                                            on the next daily run (snapshot product_evidence@v57)
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from quote_ai.fixtures.lakeside import NOW

SOURCES = ("gudid_daily_updates", "gudid_api_device_history")
STEP_SERVICE = {"extraction": "quote_ai_extract", "retrieval": "quote_ai_embed", "comparison": "quote_ai_chat",
                "agent": "quote_ai_chat"}


def _ts(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


def ingestion_log(now: datetime = NOW, paused_hours: int | None = None, days: int = 7) -> list[dict]:
    """One succeeded run per watched source per day at 06:20 UTC. Paused: the last success was paused_hours ago."""
    rows = []
    last = now.replace(hour=6, minute=20, second=0) if now.hour >= 6 else (now - timedelta(days=1)).replace(
        hour=6, minute=20, second=0)
    if paused_hours is not None:
        last = now - timedelta(hours=paused_hours)
    for i in range(days):
        ended = last - timedelta(days=i)
        for s in SOURCES:
            rows.append({"run_id": f"{s}-{ended:%Y%m%d}", "source": s, "status": "succeeded",
                         "started_at": _ts(ended - timedelta(minutes=20)), "ended_at": _ts(ended)})
    rows.append({"run_id": "full-1", "source": "gudid_full_release", "status": "succeeded",
                 "started_at": "2026-11-02 05:00:00", "ended_at": "2026-11-02 07:30:00"})
    rows.append({"run_id": "failed-1", "source": "gudid_daily_updates", "status": "failed",
                 "started_at": _ts(now - timedelta(hours=1)), "ended_at": _ts(now - timedelta(minutes=50))})
    return rows


def documents(now: datetime = NOW, waiting_hours: int | None = None) -> dict[str, list[dict]]:
    """The supplier documents: every signed-off document is evidence, unless one has been waiting waiting_hours."""
    reg = [{"document_id": "RPT-CHEMO-A", "version": 1, "is_current": 1, "registered_at": "2026-11-01 10:00:00"}]
    sign = [{"document_id": "RPT-CHEMO-A", "document_version": 1}]
    ev = [{"document_id": "RPT-CHEMO-A", "document_version": 1}]
    if waiting_hours is not None:
        reg.append({"document_id": "RPT-CHEMO-B", "version": 1, "is_current": 1,
                    "registered_at": _ts(now - timedelta(hours=waiting_hours))})
        sign.append({"document_id": "RPT-CHEMO-B", "document_version": 1})
    return {"product.document_registry": reg, "product.document_qa_signoff": sign,
            "product.supplier_quality_evidence": ev}


def inputs(now: datetime = NOW, inventory_age_hours: int = 3) -> dict[str, list[dict]]:
    day = lambda h: _ts(now - timedelta(hours=h))  # noqa: E731
    return {"gudid_gold.glove_devices": [{"primary_di": "00812345000001", "_release_date": day(3)}],
            "sales.inventory_snapshot": [{"sku_id": "GLOVE-A-M", "_release_date": day(inventory_age_hours)}],
            "sales.price_book": [{"sku_id": "GLOVE-A-M", "_release_date": day(72)}]}


def trace_usage(now: datetime = NOW, days: int = 7, seed: int = 7, stale_refusal: bool = False) -> list[dict]:
    """A week of demo traffic: 6 to 10 quotes a day, each with its traced steps; a few slow ones and one failed call.
    stale_refusal: the last quote's Issue refused on stale inputs (the job paused)."""
    rnd = random.Random(seed)
    rows = []
    for d in range(days, 0, -1):
        day = (now - timedelta(days=d)).date().isoformat()
        for q in range(rnd.randint(6, 10)):
            qid = f"Q-demo-{day[5:7]}{day[8:]}-{q}"
            for step, (pt, ct, ms) in {"extraction": (900, 250, 2200), "retrieval": (120, 0, 400),
                                       "comparison": (2400, 450, 4200), "agent": (3000, 600, 5200)}.items():
                rows.append({"day": day, "quote_id": qid, "trace_id": f"tr-{qid}-{step}", "step": step,
                             "service": STEP_SERVICE[step], "prompt_tokens": pt + rnd.randint(0, 200),
                             "completion_tokens": ct, "latency_ms": ms + rnd.randint(0, 800), "status": "ok"})
    rows[5]["status"] = "error"
    rows[9]["latency_ms"] += 15000          # one slow comparison: that day misses the latency SLO
    if stale_refusal:
        rows.append({"day": rows[-1]["day"], "quote_id": rows[-1]["quote_id"], "trace_id": "tr-issue-stale",
                     "step": "issue", "service": "none", "prompt_tokens": 0, "completion_tokens": 0,
                     "latency_ms": 90, "status": "stale_refused"})
    return rows


def daily_compute(usage: list[dict]) -> list[dict]:
    """App compute bills whether or not anyone quotes (demo: $1.20 a day), the win model's serving and the jobs."""
    days = sorted({u["day"] for u in usage})
    return [r for d in days for r in ({"day": d, "step": "app", "cost_usd": 1.20, "source": "demo"},
                                      {"day": d, "step": "serving", "cost_usd": 0.30, "source": "demo"},
                                      {"day": d, "step": "compute", "cost_usd": 0.40, "source": "demo"})]


def live_scores(usage: list[dict]) -> list[dict]:
    days = sorted({u["day"] for u in usage})
    return [r for i, d in enumerate(days) for r in (
        {"day": d, "scorer": "grounded_rate", "value": round(0.94 - 0.01 * (i % 3), 2), "n": 50},
        {"day": d, "scorer": "eligibility_violations", "value": 0, "n": 50},
        {"day": d, "scorer": "unknown_share", "value": round(0.12 + 0.01 * (i % 2), 2), "n": 50})]


def monitoring_data(now: datetime = NOW, paused_hours: int | None = None, document_waiting_hours: int | None = None
                    ) -> dict[str, list[dict]]:
    usage = trace_usage(now, stale_refusal=paused_hours is not None)
    inventory_age = 3 if paused_hours is None else paused_hours
    return ({"ops.ingestion_log": ingestion_log(now, paused_hours)} | documents(now, document_waiting_hours)
            | inputs(now, inventory_age) | {"monitoring.trace_usage": usage,
                                            "monitoring.daily_compute": daily_compute(usage),
                                            "monitoring.live_scores": live_scores(usage)})


# ------------------------------------------------------------------ the evidence scenarios

def leaves_distribution(rows: list[dict], product_id: str, record_version: int = 4,
                        at: datetime = NOW) -> list[dict]:
    """Today's GUDID daily update: the glove's commercial distribution status is now out of distribution. The
    evidence rows as the daily job leaves them: source truth, verified, citing the new GUDID record version."""
    out = []
    for r in rows:
        if r["product_id"] == product_id and r["attribute_name"] == "in_commercial_distribution":
            r = dict(r) | {"attribute_value": "false", "observed_at": at,
                           "source_id": f"{r['source_id']}@v{record_version}"}
        out.append(r)
    return out


def synced_docs(rows: list[dict], product_id: str) -> list[dict]:
    """The AI Search documents after the index sync: the glove no longer in distribution."""
    from quote_ai.search import docs
    return [d | {"in_commercial_distribution": False} if d["product_id"] == product_id else d
            for d in docs.demo_docs(rows)]


def report_arrives():
    """Glove B's supplier chemo report, registered and signed off by QA: the evidence after the next daily run."""
    from quote_ai.storage.evidence_store import DemoEvidenceStore
    return DemoEvidenceStore(scenarios=("glove_a_report", "glove_b_report"),
                             signoffs={("RPT-CHEMO-A", 1): "qa.demo", ("RPT-CHEMO-B", 1): "qa.demo"},
                             snapshot="product_evidence@v57")
