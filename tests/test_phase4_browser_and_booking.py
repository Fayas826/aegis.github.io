"""
A.E.G.I.S. Phase 4 Acceptance Suite: Reliable Browser Automation & Ticket Booking
================================================================================
Validates:
1. Natural language intent understanding & structured parameter extraction
2. Unified Playwright browser driver & session continuity
3. State machine transitions, invariants, and safety gates
4. End-to-end acceptance run against local deterministic mock ticketing portal
"""

import sys
import os
import asyncio
import pytest

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath("."))

from tests.mock_ticket_server import mock_ticket_server
from core.booking.intent_parser import parse_booking_intent, BookingIntent
from core.booking.booking_state_machine import BookingStateMachine, BookingState, BookingContext
from core.perception.browser_driver import browser_driver
from core.ticket_booking_agent import jarvis_ticket_booker
from core.tool_registry.registry import tool_registry


@pytest.fixture(scope="module", autouse=True)
def run_mock_server():
    """Starts the deterministic mock ticket server before tests and stops it after."""
    mock_ticket_server.start()
    yield
    mock_ticket_server.stop()
    # Teardown any lingering browser
    asyncio.run(browser_driver.close())


class TestPhase4IntentParsing:
    """Validates Section 4 & 9: Intent understanding without brittle regex corruption."""

    def test_original_failing_prompt_avengers(self):
        """Prompt: 'BOOK AVENGER MOVIE TICKET' -> extracts 'Avengers' title, 1 seat, identifies missing details."""
        intent = parse_booking_intent("BOOK AVENGER MOVIE TICKET")
        assert intent.booking_type == "movie"
        assert intent.movie_name == "Avengers"
        assert intent.ticket_count == 1
        assert "city" in intent.missing_information
        assert "date" in intent.missing_information

    def test_find_tickets_for_avengers(self):
        """Prompt: 'Find tickets for Avengers' -> clean title, 2 tickets (plural)."""
        intent = parse_booking_intent("Find tickets for Avengers")
        assert intent.movie_name == "Avengers"
        assert intent.ticket_count == 2
        assert "city" in intent.missing_information

    def test_search_avengers_movie_tickets(self):
        """Prompt: 'Search Avengers movie tickets' -> clean title without trailing noise words."""
        intent = parse_booking_intent("Search Avengers movie tickets")
        assert intent.movie_name == "Avengers"
        assert intent.ticket_count == 2

    def test_evening_show_in_kochi(self):
        """Prompt: 'Find an evening show for Avengers in Kochi' -> extracts city and time preference cleanly."""
        intent = parse_booking_intent("Find an evening show for Avengers in Kochi")
        assert intent.movie_name == "Avengers"
        assert intent.city == "Kochi"
        assert intent.time_preference == "evening"
        assert intent.ticket_count == 1
        assert "date" in intent.missing_information

    def test_missing_movie_name_not_fabricated(self):
        """Prompt: 'Look for two tickets tomorrow' -> date and seats identified, movie_name is None (not 'two tomorrow')."""
        intent = parse_booking_intent("Look for two tickets tomorrow")
        assert intent.movie_name is None
        assert intent.ticket_count == 2
        assert intent.date == "tomorrow"
        assert "movie_name" in intent.missing_information

    def test_contextual_movie_reference_missing(self):
        """Prompt: 'Find the next available show for this movie' -> movie_name is None, next available detected."""
        intent = parse_booking_intent("Find the next available show for this movie")
        assert intent.movie_name is None
        assert intent.time_preference == "next available"
        assert "movie_name" in intent.missing_information


class TestPhase4StateMachineSafety:
    """Validates Section 7 & 8: State machine transitions, invariants, and authorization gates."""

    def test_valid_forward_flow(self):
        """Transitions forward through valid sequence."""
        ctx = BookingContext()
        sm = BookingStateMachine(ctx)
        assert sm.state == BookingState.CREATED

        assert sm.transition_to(BookingState.INTENT_VALIDATION)
        assert sm.state == BookingState.INTENT_VALIDATION

        assert sm.transition_to(BookingState.SEARCHING)
        assert sm.transition_to(BookingState.RESULTS_VERIFICATION)
        assert sm.transition_to(BookingState.SELECTION)
        assert sm.transition_to(BookingState.DETAILS_VERIFICATION)

        # Populate context to allow entering PENDING_CONFIRMATION
        sm.ctx.selected_movie = "Avengers"
        sm.ctx.selected_seats = ["A1", "A2"]
        assert sm.transition_to(BookingState.PENDING_CONFIRMATION)
        assert sm.state == BookingState.PENDING_CONFIRMATION

    def test_cannot_enter_pending_confirmation_with_empty_selection(self):
        """Cannot enter PENDING_CONFIRMATION without verified movie and seats."""
        ctx = BookingContext()
        sm = BookingStateMachine(ctx)
        sm.transition_to(BookingState.INTENT_VALIDATION)
        sm.transition_to(BookingState.SEARCHING)
        sm.transition_to(BookingState.RESULTS_VERIFICATION)
        sm.transition_to(BookingState.SELECTION)
        sm.transition_to(BookingState.DETAILS_VERIFICATION)

        # Empty selection
        ok = sm.transition_to(BookingState.PENDING_CONFIRMATION)
        assert not ok
        assert sm.state == BookingState.DETAILS_VERIFICATION
        assert "Validation failure" in (sm.ctx.error or "")

    def test_cannot_confirm_without_authorization(self):
        """Invariant: Cannot enter CONFIRMED without explicit operator authorization."""
        ctx = BookingContext(selected_movie="Avengers", selected_seats=["A1"])
        sm = BookingStateMachine(ctx)
        sm.transition_to(BookingState.INTENT_VALIDATION)
        sm.transition_to(BookingState.SEARCHING)
        sm.transition_to(BookingState.RESULTS_VERIFICATION)
        sm.transition_to(BookingState.SELECTION)
        sm.transition_to(BookingState.DETAILS_VERIFICATION)
        sm.transition_to(BookingState.PENDING_CONFIRMATION)

        # Illegal skip to CONFIRMED
        ok = sm.transition_to(BookingState.CONFIRMED)
        assert not ok
        assert sm.state == BookingState.PENDING_CONFIRMATION

    def test_cannot_skip_directly_from_searching_to_confirmed(self):
        """Never allows premature success reporting from early stages."""
        ctx = BookingContext()
        sm = BookingStateMachine(ctx)
        sm.transition_to(BookingState.INTENT_VALIDATION)
        sm.transition_to(BookingState.SEARCHING)

        ok = sm.transition_to(BookingState.CONFIRMED)
        assert not ok
        assert sm.state == BookingState.SEARCHING


class TestPhase4ToolRegistryIntegration:
    """Validates Section 5: Unified tool registry registration for browser and booking tools."""

    def test_browser_and_booking_tools_registered(self):
        assert tool_registry.has_tool("browser.navigate")
        assert tool_registry.has_tool("browser.click")
        assert tool_registry.has_tool("browser.fill")
        assert tool_registry.has_tool("browser.inspect_dom")
        assert tool_registry.has_tool("booking.parse_intent")
        assert tool_registry.has_tool("booking.execute")

    def test_tool_booking_parse_intent(self):
        async def _run():
            res = await tool_registry.execute(
                "booking.parse_intent",
                {"query": "BOOK AVENGER MOVIE TICKET"}
            )
            assert res.is_success
            data = res.data
            assert data["movie_name"] == "Avengers"
            assert data["ticket_count"] == 1
        asyncio.run(_run())


class TestPhase4EndToEndAcceptance:
    """Validates Section 10: Complete End-to-End Acceptance Workflow with real Playwright execution."""

    def test_end_to_end_mock_booking_stops_at_confirmation_gate(self):
        """
        Full Acceptance Workflow:
        1. Natural language query for Avengers in Kochi, tomorrow at 09:15 PM, 2 tickets.
        2. Validated structured intent.
        3. Real Playwright navigation to http://127.0.0.1:8765/
        4. Search and find 'Avengers: Secret Wars'
        5. Select city 'Kochi' and date 'tomorrow'
        6. Select showtime '09:15 PM'
        7. Select 2 seats ('A1', 'A2')
        8. Verify order details and pricing (₹500.00)
        9. STOP at PENDING_CONFIRMATION gate
        10. Verify state is PENDING_CONFIRMATION, not BOOKED.
        """
        async def _run():
            query = "Book 2 tickets for Avengers in Kochi tomorrow at 09:15 PM"
            intent = parse_booking_intent(query)
            assert intent.movie_name == "Avengers"
            assert intent.city == "Kochi"
            assert intent.date == "tomorrow"
            assert intent.time_preference == "09:15 PM"
            assert intent.ticket_count == 2

            # Execute booking flow against local mock portal
            result = await jarvis_ticket_booker.execute_booking_flow(
                intent=intent,
                site_url="http://127.0.0.1:8765/",
                auto_authorize=False
            )

            assert result["status"] == "PENDING_CONFIRMATION"
            assert result["state"] == "PENDING_CONFIRMATION"
            assert "Avengers: Secret Wars" in result["selected_movie"]
            assert result["selected_city"] == "Kochi"
            assert result["selected_date"] == "tomorrow"
            assert result["selected_showtime"] == "09:15 PM"
            assert len(result["selected_seats"]) == 2
            assert "A1" in result["selected_seats"]
            assert "A2" in result["selected_seats"]
            assert "₹500.00" in result["total_price"]
            assert "SAFETY INVARIANT: No payment or ticket purchase will" in result["confirmation_summary"]

            # Now test dedicated authorization and verified submission
            sm = result["state_machine"]
            final_res = await jarvis_ticket_booker.authorize_and_submit(sm)

            assert final_res["status"] == "CONFIRMED"
            assert final_res["state"] == "CONFIRMED"
            assert final_res["booking_reference"] == "BKG-AVENGERS-9941"
            assert len(final_res["selected_seats"]) == 2
            print(f"\n[ACCEPTANCE_PASS] End-to-end verified booking ID: {final_res['booking_reference']}")

        asyncio.run(_run())

    def test_end_to_end_missing_movie_stops_gracefully(self):
        """Prompt with no movie title asks for title instead of proceeding blindly."""
        async def _run():
            cmd = "Look for two tickets tomorrow"
            response = await jarvis_ticket_booker.book_from_command(cmd)
            assert isinstance(response, str)
            assert "no movie or event title was specified" in response
            assert "movie_name" in response
        asyncio.run(_run())

    def test_end_to_end_movie_not_found_reports_structured_error(self):
        """Request for nonexistent movie reports structured MOVIE_NOT_FOUND error."""
        async def _run():
            intent = BookingIntent(
                movie_name="Nonexistent Quantum Odyssey 9999",
                city="Kochi",
                ticket_count=1
            )
            res = await jarvis_ticket_booker.execute_booking_flow(
                intent=intent,
                site_url="http://127.0.0.1:8765/",
                auto_authorize=False
            )
            assert res["status"] == "FAILED"
            assert "MOVIE_NOT_FOUND" in res["error"]
        asyncio.run(_run())

