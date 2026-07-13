from __future__ import annotations

from datetime import UTC, datetime
import pytest

from core import scorecard
from core.ledger import JsonObject, JsonValue


def _transition(
    event_id: str,
    *,
    action: str = "block",
    reason_code: str = "stop.verification_missing",
    turn_id: str = "turn-1",
    resolves: list[str] | None = None,
    resolution: str = "none",
    occurred_at: str = "2026-07-13T12:00:00+00:00",
) -> JsonObject:
    return {
        "scorecard_schema_version": 1,
        "event": "gate_transition",
        "event_id": event_id,
        "host": "codex_cli",
        "session_id": "session-1",
        "agent": "codex",
        "turn_id": turn_id,
        "reason_code": reason_code,
        "action": action,
        "resolves": resolves or [],
        "resolution": resolution,
        "attribution": "exact",
        "occurred_at": occurred_at,
    }


def test_parse_transition_when_schema_is_valid_returns_typed_transition() -> None:
    # Given: a complete independent scorecard v1 event.
    raw = _transition("block-1")

    # When: the journal boundary parses it.
    transition = scorecard.parse_transition(raw)

    # Then: identity and closed variants are preserved.
    assert transition.identity.agent_key == "codex_cli:session-1:codex"
    assert transition.reason_code.value == "stop.verification_missing"
    assert transition.action.value == "block"
    assert transition.occurred_at.tzinfo is UTC


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scorecard_schema_version", 2),
        ("event", "verification"),
        ("reason_code", "stop.unknown"),
        ("action", "allow"),
        ("resolution", "unknown"),
        ("attribution", "guessed"),
        ("occurred_at", "2026-07-13T12:00:00"),
    ],
)
def test_parse_transition_when_closed_field_is_invalid_rejects_event(
    field: str, value: JsonValue
) -> None:
    # Given: one invalid boundary field.
    raw = _transition("invalid-1")
    raw[field] = value

    # When/Then: the independent scorecard schema rejects it.
    with pytest.raises(scorecard.ScorecardSchemaError, match=field):
        _ = scorecard.parse_transition(raw)


def test_parse_transition_when_required_field_is_missing_rejects_event() -> None:
    # Given: a journal event without canonical session identity.
    raw = _transition("missing-1")
    _ = raw.pop("session_id")

    # When/Then: missing data is not default-attributed silently.
    with pytest.raises(scorecard.ScorecardSchemaError, match="session_id"):
        _ = scorecard.parse_transition(raw)


def test_aggregate_when_two_blocks_share_one_recovery_counts_distinct_units() -> None:
    # Given: two blocked attempts in one recovery scope and one explicit recovery.
    transitions = tuple(
        scorecard.parse_transition(raw)
        for raw in (
            _transition("block-1"),
            _transition("block-2"),
            _transition(
                "recover-1",
                action="recover",
                resolves=["block-1", "block-2"],
                resolution="verification",
            ),
        )
    )

    # When: the current canonical session is aggregated.
    result = scorecard.aggregate_transitions(transitions)

    # Then: attempts, scopes, and resolved attempts remain separate units.
    assert result.blocked_attempts == 2
    assert result.recovered_scopes == 1
    assert result.resolved_attempts == 2
    assert result.cap_allows == 0
    assert result.unresolved_block_ids == ()


def test_aggregate_when_cap_allows_never_counts_it_as_recovery() -> None:
    # Given: one unresolved block followed by a cap allow.
    transitions = tuple(
        scorecard.parse_transition(raw)
        for raw in (
            _transition("block-1"),
            _transition(
                "cap-1",
                action="cap_allow",
                resolves=["block-1"],
                resolution="none",
            ),
        )
    )

    # When: the scorecard aggregates it.
    result = scorecard.aggregate_transitions(transitions)

    # Then: cap passage remains unresolved and outside recovery totals.
    assert result.blocked_attempts == 1
    assert result.recovered_scopes == 0
    assert result.resolved_attempts == 0
    assert result.cap_allows == 1
    assert result.unresolved_block_ids == ("block-1",)


def test_aggregate_when_event_id_repeats_is_idempotent() -> None:
    # Given: the same append is observed twice during replay.
    transition = scorecard.parse_transition(_transition("block-1"))

    # When: both copies are aggregated.
    result = scorecard.aggregate_transitions((transition, transition))

    # Then: the logical event is counted once.
    assert result.blocked_attempts == 1
    assert result.unresolved_block_ids == ("block-1",)


def test_aggregate_when_recovery_precedes_block_keeps_block_unresolved() -> None:
    # Given: a malformed causal order that points at a future block.
    transitions = tuple(
        scorecard.parse_transition(raw)
        for raw in (
            _transition(
                "recover-1",
                action="recover",
                resolves=["block-1"],
                resolution="verification",
                occurred_at="2026-07-13T12:00:00+00:00",
            ),
            _transition(
                "block-1",
                occurred_at="2026-07-13T12:00:01+00:00",
            ),
        )
    )

    # When: the journal is aggregated in causal order.
    result = scorecard.aggregate_transitions(transitions)

    # Then: only a previously unresolved block can be recovered.
    assert result.recovered_scopes == 0
    assert result.resolved_attempts == 0
    assert result.unresolved_block_ids == ("block-1",)


def test_aggregate_when_recovery_reason_differs_keeps_block_unresolved() -> None:
    # Given: a recovery transition for a different gate reason.
    transitions = tuple(
        scorecard.parse_transition(raw)
        for raw in (
            _transition("block-1", reason_code="pretool.goals_missing"),
            _transition(
                "recover-1",
                action="recover",
                reason_code="stop.verification_missing",
                resolves=["block-1"],
                resolution="verification",
                occurred_at="2026-07-13T12:00:01+00:00",
            ),
        )
    )

    # When: the transitions are aggregated.
    result = scorecard.aggregate_transitions(transitions)

    # Then: a different reason cannot close the block scope.
    assert result.recovered_scopes == 0
    assert result.resolved_attempts == 0
    assert result.unresolved_block_ids == ("block-1",)


def test_parse_transition_when_cap_has_no_block_reference_rejects_event() -> None:
    # Given: a cap allow that is not tied to a blocked attempt.
    raw = _transition("cap-1", action="cap_allow")

    # When/Then: the schema refuses a dangling cap event.
    with pytest.raises(scorecard.ScorecardSchemaError, match="resolves"):
        _ = scorecard.parse_transition(raw)


def test_empty_aggregate_distinguishes_pre_activation_from_observed_zero() -> None:
    # Given: an activation instant and sessions on either side.
    activated_at = datetime(2026, 7, 13, 12, tzinfo=UTC)

    # When: empty session summaries are created.
    before = scorecard.empty_aggregate(
        scorecard.SessionIdentity("codex_cli", "old", "codex"),
        activated_at=activated_at,
        session_started_at=datetime(2026, 7, 13, 11, tzinfo=UTC),
    )
    after = scorecard.empty_aggregate(
        scorecard.SessionIdentity("codex_cli", "new", "codex"),
        activated_at=activated_at,
        session_started_at=datetime(2026, 7, 13, 13, tzinfo=UTC),
    )

    # Then: historical absence is N/A while activated exact attribution is zero.
    assert before.observed is False
    assert after.observed is True
    assert after.blocked_attempts == 0


def test_parse_transition_accepts_all_six_reason_codes() -> None:
    # Given: the complete fixed internal decision-code set.
    reason_codes = {
        "stop.provenance_incomplete",
        "stop.investigation_markers",
        "stop.verification_missing",
        "pretool.goals_missing",
        "pretool.intent_missing",
        "pretool.contract_missing",
    }

    # When: each code crosses the journal boundary.
    parsed = {
        scorecard.parse_transition(
            _transition(f"event-{index}", reason_code=reason_code)
        ).reason_code.value
        for index, reason_code in enumerate(sorted(reason_codes))
    }

    # Then: no extra or missing code exists.
    assert parsed == reason_codes
