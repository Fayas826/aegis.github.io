"""Small, conservative expected-outcome planning and verification API.

This module deliberately does not execute actions or retain state.  It turns a
plain-language goal into a small set of observable predicates and evaluates
those predicates against either a browser-shaped mapping or the existing
desktop :class:`ActionObservation` snapshot.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
import re
import time
from typing import Any, Mapping, Optional, Sequence, Tuple, Union

from core.orchestration.action_verifier import ActionObservation


class OutcomeStatus(str, Enum):
    """The deliberately small set of outcomes exposed to an orchestrator."""

    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"


@dataclass(frozen=True)
class ExpectedPredicate:
    """An observable condition inferred from a natural-language goal."""

    kind: str
    value: str
    description: str


@dataclass(frozen=True)
class VerificationPlan:
    """A goal and the evidence required to verify it."""

    goal: str
    predicates: Tuple[ExpectedPredicate, ...]
    clarification: Optional[str] = None

    @property
    def is_ambiguous(self) -> bool:
        return not self.predicates or self.clarification is not None


@dataclass
class VerificationOutcome:
    """Structured evidence result suitable for later orchestration."""

    status: OutcomeStatus
    goal: str
    action_attempted: bool
    goal_verified: bool
    reason: str
    plan: VerificationPlan
    matched_predicates: Tuple[bool, ...] = ()
    evidence_timestamp: Optional[float] = None
    evidence_age_seconds: Optional[float] = None
    action_status: Optional[str] = None

    @property
    def action_succeeded(self) -> bool:
        """Whether an action reported success (never used as goal evidence)."""

        return self.action_attempted and self.action_status in {
            "SUCCESS", "SUCCEEDED", "OK", "COMPLETED"
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation for a future integration."""

        result = asdict(self)
        result["status"] = self.status.value
        return result


Observation = Union[ActionObservation, Mapping[str, Any]]

_CONTROL_VERBS = re.compile(
    r"\b(?:click|press|tap|select|choose|activate)\s+(?:the\s+)?"
    r"(?:button|link|menu|control)?\s*[\"']?([^\"']+?)[\"']?"
    r"(?=\s+(?:on|in|at|from)\b|\s*$)",
    re.IGNORECASE,
)
_NAVIGATION = re.compile(
    r"\b(?:open|visit|navigate\s+to|go\s+to)\s+(?:the\s+)?"
    r"[\"']?([^\"']+?)[\"']?(?=\s+(?:page|screen|site|website)\b|\s*$)",
    re.IGNORECASE,
)
_PAGE_NOUN = re.compile(
    r"\b(?:on|in|at)\s+(?:the\s+)?([\w.-]+)\s+"
    r"(?:page|screen|site|website)\b",
    re.IGNORECASE,
)
_URL = re.compile(r"\b(?:https?://)?(?:www\.)?[\w-]+\.(?:com|org|net|io|dev|ai|co)\b", re.IGNORECASE)
_QUOTED = re.compile(r"[\"']([^\"']+)[\"']")
_CONFIRMATION = re.compile(
    r"\b(?:saved|successful|success|completed|done|sent|submitted|created|"
    r"updated|deleted|connected|logged\s+in|signed\s+in)\b",
    re.IGNORECASE,
)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def derive_predicates(
    goal: str, observation: Optional[Observation] = None
) -> VerificationPlan:
    """Infer only predicates that can be checked from a visible snapshot.

    ``observation`` is accepted for API symmetry and future disambiguation; it
    is intentionally not used to manufacture evidence or make an ambiguous
    goal appear verified.
    """

    del observation
    normalized = _clean(goal)
    if len(normalized.split()) < 2 or normalized.lower() in {
        "do it",
        "handle this",
        "finish it",
        "make it happen",
    }:
        return VerificationPlan(
            normalized,
            (),
            "Please specify the expected page, text, or control state.",
        )

    predicates: list[ExpectedPredicate] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, value: str, description: str) -> None:
        value = _clean(value).strip(".,:;!?()[]{}")
        if not value:
            return
        key = (kind, value.casefold())
        if key not in seen:
            seen.add(key)
            predicates.append(ExpectedPredicate(kind, value, description))

    action_match = _CONTROL_VERBS.search(normalized)
    if action_match:
        target = re.sub(
            r"\s+(?:button|link|menu|control)$", "", action_match.group(1), flags=re.I
        )
        add("control_activated", target, f"The control '{target}' was activated.")

    for url in _URL.findall(normalized):
        add("url_contains", url, f"The current URL contains '{url}'.")

    navigation_match = _NAVIGATION.search(normalized)
    if navigation_match:
        target = navigation_match.group(1)
        target = re.sub(r"\s+(?:page|screen|site|website)$", "", target, flags=re.I)
        if not _CONTROL_VERBS.search(normalized):
            add("page_contains", target, f"The current page identifies '{target}'.")

    page_match = _PAGE_NOUN.search(normalized)
    if page_match:
        add("page_contains", page_match.group(1), f"The current page identifies '{page_match.group(1)}'.")

    # Quoted text is strong evidence for display/confirmation goals, but a
    # quoted control target is already represented by control_activated.
    if not action_match:
        for quoted in _QUOTED.findall(normalized):
            add("text_contains", quoted, f"Visible text contains '{quoted}'.")

    for confirmation in _CONFIRMATION.findall(normalized):
        add("text_contains", confirmation, f"Visible text contains '{confirmation}'.")

    if not predicates:
        return VerificationPlan(
            normalized,
            (),
            "Please specify observable evidence, such as a page, URL, text, or control.",
        )
    return VerificationPlan(normalized, tuple(predicates))


def _timestamp(observation: Observation) -> Optional[float]:
    if isinstance(observation, ActionObservation):
        return float(observation.timestamp)
    raw = observation.get("timestamp", observation.get("observed_at", observation.get("captured_at")))
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _text_values(observation: Observation) -> tuple[str, ...]:
    if isinstance(observation, ActionObservation):
        return (
            observation.active_window_title,
            observation.ocr_text,
            " ".join(str(item.get("text", "")) for item in observation.dom_context.get("elements", [])),
        )
    values: list[str] = []
    for key in ("title", "page_title", "text", "body_text", "ocr_text", "content"):
        value = observation.get(key)
        if isinstance(value, str):
            values.append(value)
    return tuple(values)


def _page_values(observation: Observation) -> tuple[str, ...]:
    if isinstance(observation, ActionObservation):
        return (observation.active_window_title, observation.active_process_name)
    return tuple(
        str(observation.get(key, ""))
        for key in ("title", "page_title", "url", "current_url")
        if observation.get(key) is not None
    )


def _controls(observation: Observation) -> Sequence[Mapping[str, Any]]:
    if isinstance(observation, ActionObservation):
        return tuple(observation.dom_context.get("elements", ()))
    raw = observation.get("controls", observation.get("elements", ()))
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    normalized: list[Mapping[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping):
            normalized.append(item)
        else:
            normalized.append({"text": str(item)})
    return tuple(normalized)


def _control_label(control: Mapping[str, Any]) -> str:
    return _clean(
        control.get(
            "label",
            control.get("text", control.get("name", control.get("aria_label", control.get("value", "")))),
        )
    )


def _matches(predicate: ExpectedPredicate, observation: Observation) -> bool:
    target = predicate.value.casefold()
    if predicate.kind == "text_contains":
        return any(target in value.casefold() for value in _text_values(observation))
    if predicate.kind == "page_contains":
        return any(target in value.casefold() for value in _page_values(observation))
    if predicate.kind == "url_contains":
        return any(target in value.casefold() for value in _page_values(observation) if "." in value or "://" in value)
    if predicate.kind == "control_activated":
        for control in _controls(observation):
            if target not in _control_label(control).casefold():
                continue
            if control.get("visible", True) is False or control.get("enabled", True) is False:
                continue
            return any(bool(control.get(key)) for key in ("activated", "clicked", "pressed", "selected"))
        return False
    return False


def verify_goal(
    goal: str,
    observation: Optional[Observation] = None,
    *,
    action_attempted: bool = False,
    action_result: Optional[Mapping[str, Any]] = None,
    now: Optional[float] = None,
    max_age_seconds: float = 30.0,
) -> VerificationOutcome:
    """Verify a goal without treating execution status as proof of success."""

    plan = derive_predicates(goal, observation)
    result_status = _clean(action_result.get("status", "")).upper() if action_result else ""
    attempted = action_attempted or action_result is not None
    clock = time.time() if now is None else float(now)

    if result_status in {"BLOCKED", "SAFETY_BLOCK", "SAFETY_BLOCKED"}:
        return VerificationOutcome(
            OutcomeStatus.BLOCKED, plan.goal, attempted, False,
            "The action was blocked before its goal could be verified.", plan,
            action_status=result_status or None,
        )
    if result_status in {"UNAVAILABLE", "NOT_AVAILABLE"}:
        return VerificationOutcome(
            OutcomeStatus.UNAVAILABLE, plan.goal, attempted, False,
            "The action or evidence source is unavailable.", plan,
            action_status=result_status or None,
        )
    if plan.is_ambiguous:
        return VerificationOutcome(
            OutcomeStatus.CLARIFICATION_REQUIRED, plan.goal, attempted, False,
            plan.clarification or "The goal is ambiguous.", plan,
            action_status=result_status or None,
        )
    if observation is None:
        return VerificationOutcome(
            OutcomeStatus.UNAVAILABLE, plan.goal, attempted, False,
            "No observation was supplied.", plan,
            action_status=result_status or None,
        )

    evidence_timestamp = _timestamp(observation)
    if evidence_timestamp is None:
        return VerificationOutcome(
            OutcomeStatus.UNAVAILABLE, plan.goal, attempted, False,
            "Observation has no usable timestamp.", plan,
            action_status=result_status or None,
        )
    age = max(0.0, clock - evidence_timestamp)
    if age > max_age_seconds:
        return VerificationOutcome(
            OutcomeStatus.UNVERIFIED, plan.goal, attempted, False,
            f"Observation is stale ({age:.3f}s old; limit {max_age_seconds:.3f}s).", plan,
            evidence_timestamp=evidence_timestamp, evidence_age_seconds=age,
            action_status=result_status or None,
        )

    matched = tuple(_matches(predicate, observation) for predicate in plan.predicates)
    verified = bool(matched) and all(matched)
    if verified:
        reason = "All expected predicates matched current evidence."
        status = OutcomeStatus.VERIFIED
    else:
        missing = [
            predicate.description
            for predicate, is_match in zip(plan.predicates, matched)
            if not is_match
        ]
        reason = "Expected evidence was not found: " + "; ".join(missing)
        status = OutcomeStatus.UNVERIFIED
    return VerificationOutcome(
        status, plan.goal, attempted, verified, reason, plan, matched,
        evidence_timestamp, age,
        result_status or None,
    )


def verify_expected_outcome(*args: Any, **kwargs: Any) -> VerificationOutcome:
    """Compatibility spelling for callers that use the feature name."""

    return verify_goal(*args, **kwargs)


class VerificationPlanner:
    """Stateless facade for integrations that prefer an object API."""

    @staticmethod
    def plan(goal: str, observation: Optional[Observation] = None) -> VerificationPlan:
        return derive_predicates(goal, observation)

    @staticmethod
    def verify(goal: str, observation: Optional[Observation] = None, **kwargs: Any) -> VerificationOutcome:
        return verify_goal(goal, observation, **kwargs)


# Familiar alias for integrations that already use this naming convention.
VerificationStatus = OutcomeStatus


__all__ = [
    "ActionObservation",
    "ExpectedPredicate",
    "Observation",
    "OutcomeStatus",
    "VerificationStatus",
    "VerificationOutcome",
    "VerificationPlan",
    "VerificationPlanner",
    "derive_predicates",
    "verify_expected_outcome",
    "verify_goal",
]
