"""Booking portal selection must fail closed outside explicit test usage."""

import asyncio

from core.booking.intent_parser import BookingIntent
from core.ticket_booking_agent import JarvisTicketBooker


def test_booking_does_not_default_to_local_mock_portal(monkeypatch):
    monkeypatch.delenv("AEGIS_BOOKING_SITE_URL", raising=False)
    booker = JarvisTicketBooker(headless=True)
    result = asyncio.run(booker.execute_booking_flow(
        BookingIntent(movie_name="Example Movie", city="Kochi"),
        site_url=None,
        auto_authorize=False,
    ))
    assert result["status"] == "FAILED"
    assert "SITE_URL_REQUIRED" in result["error"]
    assert "127.0.0.1:8765" not in result["context"].get("portal_url", "")
