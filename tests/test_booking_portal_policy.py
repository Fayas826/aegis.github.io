"""Booking portal selection must fail closed outside explicit test usage."""

import asyncio

from core.booking.intent_parser import BookingIntent
from core.ticket_booking_agent import (
    MOCK_BOOKING_URL,
    JarvisTicketBooker,
    booking_origin,
    validate_booking_site_url,
)


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


def test_booking_rejects_non_http_site_urls():
    booker = JarvisTicketBooker(headless=True)
    result = asyncio.run(booker.execute_booking_flow(
        BookingIntent(movie_name="Example Movie", city="Kochi"),
        site_url="file:///tmp/fake-booking.html",
        auto_authorize=False,
    ))
    assert result["status"] == "FAILED"
    assert "INVALID_SITE_URL" in result["error"]


def test_named_real_provider_does_not_select_a_hidden_default(monkeypatch):
    monkeypatch.delenv("AEGIS_BOOKING_SITE_URL", raising=False)
    response = asyncio.run(JarvisTicketBooker(headless=True).book_from_command(
        "Book Avengers tickets on BookMyShow"
    ))
    assert "SITE_URL_REQUIRED" in response
    assert "in.bookmyshow.com" not in response


def test_mock_portal_requires_an_explicit_test_argument(monkeypatch):
    monkeypatch.setenv("AEGIS_BOOKING_SITE_URL", MOCK_BOOKING_URL)
    booker = JarvisTicketBooker(headless=True)
    result = asyncio.run(booker.execute_booking_flow(
        BookingIntent(movie_name="Example Movie", city="Kochi"),
        site_url=None,
        auto_authorize=False,
    ))
    assert result["status"] == "FAILED"
    assert "MOCK_SITE_REQUIRES_EXPLICIT_TEST" in result["error"]


def test_booking_origin_rejects_credentials_fragments_and_misleading_hosts():
    assert booking_origin("https://user:pass@example.test/path#fragment") is None
    assert booking_origin("https://example.test.evil.invalid") != booking_origin("https://example.test")
    assert validate_booking_site_url("https://example.test/checkout") == "https://example.test/checkout"


def test_redirect_outside_authorized_origin_is_blocked():
    booker = JarvisTicketBooker(headless=True)
    booker._authorized_origin = booking_origin("https://authorized.example")
    try:
        booker._assert_authorized_navigation("https://evil.example/checkout")
    except ValueError as exc:
        assert "UNAUTHORIZED_REDIRECT" in str(exc)
    else:
        raise AssertionError("unauthorized redirect was accepted")


def test_task_engine_ticket_entrypoint_fails_closed(monkeypatch):
    monkeypatch.delenv("AEGIS_BOOKING_SITE_URL", raising=False)
    from core.orchestration.task_engine import default_action_controller

    result = asyncio.run(default_action_controller("WEB_EXECUTION", {
        "app_name": "ticket_portal",
        "query": "Avengers tickets",
    }))
    assert result["status"] == "FAILED"
    assert "SITE_URL_REQUIRED" in result["error"]
