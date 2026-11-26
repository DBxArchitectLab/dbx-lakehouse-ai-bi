"""The post-deploy smoke test (AI-25) against the app's own API, with no workspace: the Lakeside smoke request reaches
PRICED with zero violations, and a broken route fails the release with the step that broke."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import lakeside_fixtures as fx  # noqa: E402
from quote_ai.app.backend.main import create_app  # noqa: E402
from quote_ai.ops import smoke_test  # noqa: E402
from test_app_season4 import ALICE, make_ctx  # noqa: E402


def client(**change):
    ctx = make_ctx(inputs_as_of=lambda q: fx.fresh_inputs())
    for k, v in change.items():
        setattr(ctx, k, v)
    return TestClient(create_app(ctx, serve_frontend=False))


def test_the_lakeside_smoke_request_reaches_priced_with_zero_violations():
    c = client()
    r = smoke_test.smoke(c, headers=ALICE)
    assert (r.state, r.product_id, r.violations) == ("PRICED", "GLOVE-A", [])
    assert r.steps[1:] == ["REQUIREMENTS_CONFIRMED", "SHORTLISTED", "PRICED", "CANCELLED"]
    assert c.get(f"/api/quotes/{r.quote_id}", headers=ALICE).json()["state"] == "CANCELLED"   # never in a report


def test_the_smoke_request_is_the_lakeside_request():
    assert smoke_test.LAKESIDE_SMOKE_REQUEST == fx.LAKESIDE_REQUEST
    assert smoke_test.LAKESIDE_SPLIT == fx.LAKESIDE_SPLIT


def test_a_broken_route_fails_with_the_step():
    with pytest.raises(smoke_test.SmokeFailed) as e:
        smoke_test.smoke(client(sku_index=None), headers=ALICE)    # search not wired: /api/shortlist answers 503
    assert e.value.step == "shortlist" and "503" in e.value.reason
    assert "release-and-rollback.md" in str(e.value)


def test_signed_out_fails_at_the_first_quote_step():
    with pytest.raises(smoke_test.SmokeFailed) as e:
        smoke_test.smoke(client())
    assert e.value.step == "new request" and "401" in e.value.reason


def test_unknown_is_never_counted_as_eligible():
    s = {"eligible": [{"product_id": "GLOVE-B", "checks": [{"requirement": "chemo_tested", "result": "unknown"}]}]}
    assert smoke_test.violations(s, "GLOVE-B") == ["GLOVE-B eligible with chemo_tested = unknown"]
    assert smoke_test.violations({"eligible": []}, "GLOVE-E") == ["GLOVE-E priced but not eligible"]
