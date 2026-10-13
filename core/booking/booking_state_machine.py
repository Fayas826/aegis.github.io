"""
A.E.G.I.S. Autonomous Ticket Booking State Machine
==================================================
Phase 4: Verified Lifecycle and Confirmation Gate for Ticketing Workflows.
Enforces strict transitions:
  CREATED -> INTENT_VALIDATION -> SEARCHING -> RESULTS_VERIFICATION
  -> SELECTION -> DETAILS_VERIFICATION -> PENDING_CONFIRMATION
  [User Authorization Gate] -> AUTHORIZED -> SUBMITTING -> CONFIRMED
"""

import time
import uuid
import enum
import logging
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

from core.booking.intent_parser import BookingIntent

logger = logging.getLogger("AEGIS.BookingStateMachine")


class BookingState(str, enum.Enum):
    CREATED = "CREATED"
    INTENT_VALIDATION = "INTENT_VALIDATION"
    SEARCHING = "SEARCHING"
    RESULTS_VERIFICATION = "RESULTS_VERIFICATION"
    SELECTION = "SELECTION"
    DETAILS_VERIFICATION = "DETAILS_VERIFICATION"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    AUTHORIZED = "AUTHORIZED"
    SUBMITTING = "SUBMITTING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    RECOVERY = "RECOVERY"


# Valid state transitions graph
VALID_TRANSITIONS = {
    BookingState.CREATED: [BookingState.INTENT_VALIDATION, BookingState.FAILED, BookingState.CANCELLED],
    BookingState.INTENT_VALIDATION: [BookingState.SEARCHING, BookingState.BLOCKED, BookingState.FAILED, BookingState.CANCELLED],
    BookingState.SEARCHING: [BookingState.RESULTS_VERIFICATION, BookingState.RECOVERY, BookingState.FAILED, BookingState.CANCELLED],
    BookingState.RESULTS_VERIFICATION: [BookingState.SELECTION, BookingState.SEARCHING, BookingState.FAILED, BookingState.CANCELLED],
    BookingState.SELECTION: [BookingState.DETAILS_VERIFICATION, BookingState.RECOVERY, BookingState.FAILED, BookingState.CANCELLED],
    BookingState.DETAILS_VERIFICATION: [BookingState.PENDING_CONFIRMATION, BookingState.RECOVERY, BookingState.FAILED, BookingState.CANCELLED],
    BookingState.PENDING_CONFIRMATION: [BookingState.AUTHORIZED, BookingState.CANCELLED, BookingState.FAILED],
    BookingState.AUTHORIZED: [BookingState.SUBMITTING, BookingState.CANCELLED, BookingState.FAILED],
    BookingState.SUBMITTING: [BookingState.CONFIRMED, BookingState.FAILED, BookingState.RECOVERY],
    BookingState.RECOVERY: [BookingState.SEARCHING, BookingState.SELECTION, BookingState.DETAILS_VERIFICATION, BookingState.FAILED],
    BookingState.CONFIRMED: [],
    BookingState.FAILED: [],
    BookingState.BLOCKED: [BookingState.INTENT_VALIDATION, BookingState.CANCELLED],
    BookingState.CANCELLED: [],
}


class BookingContext(BaseModel):
    task_id: str = Field(default_factory=lambda: f"BKG-{uuid.uuid4().hex[:8].upper()}")
    current_state: BookingState = Field(default=BookingState.CREATED)
    intent: Optional[BookingIntent] = None
    portal_url: str = Field(default="")
    found_items: List[Dict[str, Any]] = Field(default_factory=list)
    selected_movie: Optional[str] = None
    selected_city: Optional[str] = None
    selected_venue: Optional[str] = None
    selected_date: Optional[str] = None
    selected_showtime: Optional[str] = None
    selected_seats: List[str] = Field(default_factory=list)
    total_price: Optional[str] = None
    confirmation_summary: Optional[str] = None
    is_authorized: bool = Field(default=False)
    booking_reference: Optional[str] = None
    history: List[Dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None

    def record_step(self, from_state: BookingState, to_state: BookingState, reason: str = ""):
        self.history.append({
            "from_state": from_state.value,
            "to_state": to_state.value,
            "reason": reason,
            "timestamp": time.time()
        })


class BookingStateMachine:
    """State machine controller ensuring verified progression and human authorization gates."""

    def __init__(self, context: Optional[BookingContext] = None):
        self.ctx = context or BookingContext()

    @property
    def state(self) -> BookingState:
        return self.ctx.current_state

    def transition_to(self, target_state: BookingState, reason: str = "") -> bool:
        """Transitions state while enforcing valid transition DAG rules and invariants."""
        current = self.ctx.current_state

        allowed = VALID_TRANSITIONS.get(current, [])
        if target_state not in allowed:
            err_msg = f"Illegal state transition attempted: {current.value} -> {target_state.value}."
            logger.error(err_msg)
            self.ctx.error = err_msg
            return False

        # Invariant 1: Cannot reach CONFIRMED without explicit operator authorization
        if target_state == BookingState.CONFIRMED:
            if not self.ctx.is_authorized:
                err_msg = "Safety violation: Cannot enter CONFIRMED without explicit authorization."
                logger.error(err_msg)
                self.ctx.error = err_msg
                return False
            if not self.ctx.booking_reference:
                err_msg = "Verification failure: Cannot enter CONFIRMED without independently verified booking reference."
                logger.error(err_msg)
                self.ctx.error = err_msg
                return False

        # Invariant 2: Cannot enter PENDING_CONFIRMATION without verified details
        if target_state == BookingState.PENDING_CONFIRMATION:
            if not self.ctx.selected_movie or not self.ctx.selected_seats:
                err_msg = "Validation failure: Cannot enter PENDING_CONFIRMATION with empty selection or seats."
                logger.error(err_msg)
                self.ctx.error = err_msg
                return False

        prev = self.ctx.current_state
        self.ctx.current_state = target_state
        self.ctx.record_step(prev, target_state, reason)
        logger.info(f"[STATE_MACHINE] {prev.value} -> {target_state.value} ({reason or 'OK'})")
        return True

    def build_confirmation_summary(self) -> str:
        """Builds a verified, structured human confirmation summary."""
        lines = [
            "==================================================",
            "   A.E.G.I.S. BOOKING CONFIRMATION GATE (STOP)    ",
            "==================================================",
            f"Event/Movie : {self.ctx.selected_movie or 'Unknown'}",
            f"City        : {self.ctx.selected_city or 'Unknown'}",
            f"Venue       : {self.ctx.selected_venue or 'Any Available'}",
            f"Date        : {self.ctx.selected_date or 'Today / Next Available'}",
            f"Showtime    : {self.ctx.selected_showtime or 'Standard'}",
            f"Seats ({len(self.ctx.selected_seats)}): {', '.join(self.ctx.selected_seats) if self.ctx.selected_seats else 'None'}",
            f"Total Price : {self.ctx.total_price or '₹0.00 / Free'}",
            "--------------------------------------------------",
            "STATUS: PENDING OPERATOR CONFIRMATION",
            "SAFETY INVARIANT: No payment or ticket purchase will",
            "be committed without your explicit confirmation.",
            "=================================================="
        ]
        summary = "\n".join(lines)
        self.ctx.confirmation_summary = summary
        return summary

    def authorize(self) -> bool:
        """Operator explicitly grants authorization at the confirmation gate."""
        if self.ctx.current_state != BookingState.PENDING_CONFIRMATION:
            logger.warning(f"Authorization ignored: current state is {self.ctx.current_state.value}, expected PENDING_CONFIRMATION.")
            return False

        self.ctx.is_authorized = True
        return self.transition_to(BookingState.AUTHORIZED, "Operator authorization confirmed.")
