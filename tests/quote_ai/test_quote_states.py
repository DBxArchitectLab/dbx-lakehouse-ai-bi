"""The quote state machine (AI-07): the moves the ALLOWED table permits, and the ones it refuses."""
import pytest

from quote_ai.rules.quote_states import ALLOWED, TERMINAL, InvalidTransition, S, transition


def test_cannot_issue_from_priced():
    with pytest.raises(InvalidTransition):
        transition(S.PRICED, S.ISSUED)


def test_outside_policy_needs_approval():
    with pytest.raises(InvalidTransition):
        transition(S.PRICED, S.APPROVED, within_policy=False)
    assert transition(S.PRICED, S.APPROVED, within_policy=True) == S.APPROVED


def test_happy_path_first_step():
    assert transition(S.DRAFT, S.REQUIREMENTS_CONFIRMED) == S.REQUIREMENTS_CONFIRMED


def test_the_v1_path_and_the_full_path_are_allowed():
    path = [S.DRAFT, S.REQUIREMENTS_CONFIRMED, S.SHORTLISTED, S.PRICED, S.PENDING_APPROVAL, S.APPROVED, S.ISSUED]
    for a, b in zip(path, path[1:]):
        assert transition(a, b) == b


def test_no_jump_skips_a_step():
    for target in (S.SHORTLISTED, S.PRICED, S.APPROVED, S.ISSUED):
        with pytest.raises(InvalidTransition, match="not allowed"):
            transition(S.DRAFT, target)
    with pytest.raises(InvalidTransition, match="REQUIREMENTS_CONFIRMED -> ISSUED"):
        transition(S.REQUIREMENTS_CONFIRMED, S.ISSUED)


def test_every_state_is_in_the_table_and_the_ends_are_final():
    assert set(ALLOWED) == set(S)
    assert TERMINAL == {S.EXPIRED, S.CANCELLED}
    for end in TERMINAL:
        for target in S:
            with pytest.raises(InvalidTransition):
                transition(end, target)


def test_an_issued_quote_can_only_expire():
    assert ALLOWED[S.ISSUED] == {S.EXPIRED}


# ------------------------------------------------------------------ AI-20, AI-21: the same table on new storage

def test_every_allowed_move_passes_and_every_other_is_refused():
    for a in S:
        for b in S:
            if b in ALLOWED[a]:
                assert transition(a, b, within_policy=True) == b
            else:
                with pytest.raises(InvalidTransition):
                    transition(a, b, within_policy=True)


def test_a_changed_approved_version_goes_back_to_pending_approval():
    """AI-21: an edit after approval is a new version that needs its own approval."""
    assert transition(S.APPROVED, S.PENDING_APPROVAL) == S.PENDING_APPROVAL


def test_a_stale_version_is_a_conflict_on_lakebase():
    import lakeside_fixtures as fx
    from quote_ai.services import QuoteService
    from quote_ai.storage.lakebase import sqlite_db
    from quote_ai.storage.lakebase_repository import LakebaseQuoteRepository
    from quote_ai.storage.repository import StaleRevision
    svc = QuoteService(LakebaseQuoteRepository(sqlite_db()), clock=lambda: fx.NOW)
    q = svc.create("Lakeside", "rep.alice", fx.LAKESIDE_REQUEST, fx.lakeside_r1(), quote_id="Q-S")
    v = svc.confirm(q.quote_id, q.revision, "rep.alice", fx.LAKESIDE_SPLIT, "box", 100)
    assert (v.version, v.revision) == (2, 2)
    with pytest.raises(StaleRevision, match="quote changed; reload"):
        svc.confirm(q.quote_id, q.revision, "rep.bob", fx.LAKESIDE_SPLIT, "box", 100)
