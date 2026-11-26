"""The post-deploy smoke test (AI-25): through the app's own API, the same routes the React screens call, so a broken
front-end build or back-end route fails the release.

  smoke(http)                 GET /api/health (and /, the React build), then the Lakeside smoke request:
                              New request -> Confirm (S 60, M 140, L 140, XL 60, boxes of 100) -> Shortlist -> Price
                              (the first eligible glove that ships in full, at list price). Asserts the quote reaches
                              PRICED with zero violations; the smoke quote is then cancelled, so it never reaches a
                              pipeline or a report.
  violations(shortlist, q)    what must never happen: an eligible glove with a check that is not verified_match
                              (unknown is never a match), or a priced product the rules did not make eligible

`http` is anything with .get(path) and .post(path, json=, headers=) returning .status_code and .json(): a FastAPI
TestClient in tests/quote_ai/test_smoke_test.py, an HTTP session on the deployed app in the quote_ai_smoke_test job
(workspace_session). A failure raises SmokeFailed with the step and the reason; cd.yml then stops and points to
docs/quote_ai/runbooks/release-and-rollback.md.

  python -m quote_ai.ops.smoke_test --app quote-ai-prod
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field

LAKESIDE_SMOKE_REQUEST = ("Lakeside Regional Clinic needs 400 boxes of powder-free nitrile exam gloves, chemo-tested, "
                          "latex-free, sizes S to XL, non-sterile is fine. They pay about $8 a box today for Brand X "
                          "and need delivery in 10 days.")
LAKESIDE_ACCOUNT = "Lakeside Regional Clinic"
LAKESIDE_SPLIT = {"S": 60, "M": 140, "L": 140, "XL": 60}
RUNBOOK = "docs/quote_ai/runbooks/release-and-rollback.md"


class SmokeFailed(AssertionError):
    def __init__(self, step: str, reason: str) -> None:
        super().__init__(f"smoke test failed at {step}: {reason}. Roll back: {RUNBOOK}")
        self.step, self.reason = step, reason


@dataclass
class SmokeResult:
    quote_id: str
    state: str
    product_id: str | None
    violations: list[str]
    steps: list[str] = field(default_factory=list)


def _ok(r, step: str, status: int = 200) -> dict:
    code = r.status_code
    if code != status and not (status == 200 and code == 201):
        try:
            detail = r.json().get("detail")
        except Exception:  # noqa: BLE001  not JSON
            detail = getattr(r, "text", "")[:200]
        raise SmokeFailed(step, f"HTTP {code}: {detail}")
    return r.json()


def violations(shortlist: dict, product_id: str | None) -> list[str]:
    out = []
    for e in shortlist.get("eligible", []):
        for c in e.get("checks", []):
            if c.get("result") != "verified_match":
                out.append(f"{e['product_id']} eligible with {c.get('requirement')} = {c.get('result')}")
    eligible = {e["product_id"] for e in shortlist.get("eligible", [])}
    if product_id and product_id not in eligible:
        out.append(f"{product_id} priced but not eligible")
    return out


def run_quote_via_api(http, request: str = LAKESIDE_SMOKE_REQUEST, account: str = LAKESIDE_ACCOUNT,
                      headers: dict | None = None) -> SmokeResult:
    h = headers or {}
    steps = []
    q = _ok(http.post("/api/quotes", json={"account": account, "text": request}, headers=h), "new request", 201)
    steps.append(f"DRAFT {q['quote_id']}")
    q = _ok(http.post(f"/api/quotes/{q['quote_id']}/confirm", headers=h,
                      json={"revision": q["revision"], "quantity_by_size": LAKESIDE_SPLIT, "unit": "box",
                            "gloves_per_unit": 100}), "confirm")
    steps.append(q["state"])
    body = _ok(http.post(f"/api/shortlist/{q['quote_id']}", json={"revision": q["revision"]}, headers=h), "shortlist")
    if not body.get("moved"):
        raise SmokeFailed("shortlist", body.get("reason") or "no eligible glove")
    q, s = body["quote"], body["shortlist"]
    steps.append(q["state"])
    on_time = [e["product_id"] for e in s["eligible"] if (e.get("fulfilment") or {}).get("status") == "full"]
    if not on_time:
        raise SmokeFailed("shortlist", "no eligible glove ships in full")
    q = _ok(http.post(f"/api/price/{q['quote_id']}", json={"revision": q["revision"], "product_id": on_time[0]},
                      headers=h), "price")
    steps.append(q["state"])
    return SmokeResult(q["quote_id"], q["state"], on_time[0], violations(s, on_time[0]), steps)


def smoke(http, headers: dict | None = None, cancel: bool = True) -> SmokeResult:
    h = headers or {}
    if _ok(http.get("/api/health", headers=h), "health").get("status") != "ok":
        raise SmokeFailed("health", "status is not ok")
    r = run_quote_via_api(http, headers=h)
    if r.state != "PRICED" or r.violations:
        raise SmokeFailed("assert", f"state {r.state}, violations {r.violations}")
    if cancel:
        q = _ok(http.get(f"/api/quotes/{r.quote_id}", headers=h), "read back")
        _ok(http.post(f"/api/quotes/{r.quote_id}/state", json={"revision": q["revision"], "to": "CANCELLED"},
                      headers=h), "cancel the smoke quote")
        r.steps.append("CANCELLED")
    return r


class _Session:
    """requests on the app's URL with the job identity's OAuth headers."""

    def __init__(self, base: str, headers: dict) -> None:
        import requests
        self.base, self.s = base.rstrip("/"), requests.Session()
        self.s.headers.update(headers)

    def get(self, path, headers=None):
        return self.s.get(self.base + path, headers=headers, timeout=60)

    def post(self, path, json=None, headers=None):
        return self.s.post(self.base + path, json=json, headers=headers, timeout=120)


def workspace_session(app_name: str) -> _Session:
    """The deployed app, called as the identity running the job or the workflow (sp-dbx-lakehouse). Verify before
    recording: WorkspaceClient().apps.get(name).url, that Config.authenticate() headers are accepted by the Apps proxy
    for a service principal, that the proxy forwards it as X-Forwarded-Email, and that this identity may open the
    Lakeside account (the smoke identity needs CAN_USE on the app and a row in rep_account)."""
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    return _Session(w.apps.get(app_name).url, w.config.authenticate())


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--app", required=True, help="the app's name: quote-ai-<target>")
    p.add_argument("--keep", action="store_true", help="keep the smoke quote (default: cancelled)")
    a = p.parse_args(argv)
    try:
        r = smoke(workspace_session(a.app), cancel=not a.keep)
    except SmokeFailed as e:
        print(f"::error::{e}")
        return 1
    print(json.dumps({"app": a.app, "quote_id": r.quote_id, "state": r.state, "product": r.product_id,
                      "violations": r.violations, "steps": r.steps}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
