import time

from core.orchestration.action_verifier import ActionObservation
from core.orchestration.verification_planner import (
    OutcomeStatus,
    VerificationPlanner,
    verify_goal,
)


NOW = 1_700_000_000.0


def test_verified_success_with_browser_observation():
    observation = {
        "title": "Settings",
        "url": "https://example.test/settings",
        "timestamp": NOW,
    }

    result = verify_goal("open the Settings page", observation, now=NOW)

    assert result.status is OutcomeStatus.VERIFIED
    assert result.goal_verified is True
    assert result.action_attempted is False


def test_execution_success_does_not_create_false_goal_success():
    observation = {
        "title": "Home",
        "url": "https://example.test/home",
        "timestamp": NOW,
    }

    result = verify_goal(
        "open the Settings page",
        observation,
        action_result={"status": "SUCCESS"},
        now=NOW,
    )

    assert result.status is OutcomeStatus.UNVERIFIED
    assert result.action_attempted is True
    assert result.goal_verified is False
    assert result.action_succeeded is True


def test_stale_evidence_is_rejected():
    observation = ActionObservation(
        "Settings", "settings.exe", 42, timestamp=NOW - 31
    )

    result = verify_goal("open the Settings page", observation, now=NOW)

    assert result.status is OutcomeStatus.UNVERIFIED
    assert result.goal_verified is False
    assert "stale" in result.reason.lower()


def test_missing_control_is_unverified():
    observation = {
        "title": "Settings",
        "controls": [{"label": "Cancel", "clicked": True}],
        "timestamp": NOW,
    }

    result = verify_goal("click the Save button on the Settings page", observation, now=NOW)

    assert result.status is OutcomeStatus.UNVERIFIED
    assert result.matched_predicates == (False, True)


def test_unexpected_page_is_unverified_even_when_control_is_present():
    observation = {
        "title": "Home",
        "controls": [{"label": "Save", "clicked": True}],
        "timestamp": NOW,
    }

    result = verify_goal("click the Save button on the Settings page", observation, now=NOW)

    assert result.status is OutcomeStatus.UNVERIFIED
    assert result.goal_verified is False
    assert result.matched_predicates == (True, False)


def test_blocked_action_is_distinct_from_unverified_goal():
    result = verify_goal(
        "open the Settings page",
        action_attempted=True,
        action_result={"status": "BLOCKED"},
    )

    assert result.status is OutcomeStatus.BLOCKED
    assert result.action_attempted is True
    assert result.goal_verified is False


def test_unavailable_evidence_is_explicit():
    result = verify_goal("open the Settings page")

    assert result.status is OutcomeStatus.UNAVAILABLE
    assert result.goal_verified is False


def test_ambiguous_goal_requests_clarification():
    result = VerificationPlanner.verify("do it")

    assert result.status is OutcomeStatus.CLARIFICATION_REQUIRED
    assert result.plan.is_ambiguous is True
    assert result.plan.clarification
