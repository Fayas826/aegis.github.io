import asyncio
"""
A.E.G.I.S. Structured Booking Intent Parser
===========================================
Phase 4: Frontier-grade Natural Language Intent Interpretation for Ticketing.
Replaces brittle regex routing with normalized, schema-validated intent extraction.
"""

import re
import os
import json
import logging
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger("AEGIS.BookingIntent")


class BookingIntent(BaseModel):
    booking_type: str = Field(default="movie", description="Type of booking: movie, flight, train, bus, event")
    movie_name: Optional[str] = Field(default=None, description="Title of the movie or event")
    city: Optional[str] = Field(default=None, description="Target city or location")
    venue: Optional[str] = Field(default=None, description="Target cinema, theatre, or venue")
    date: Optional[str] = Field(default=None, description="Requested date (today, tomorrow, or explicit date)")
    time_preference: Optional[str] = Field(default=None, description="Showtime preference (e.g. evening, 09:15 PM)")
    ticket_count: int = Field(default=1, description="Number of tickets requested")
    language: Optional[str] = Field(default=None, description="Preferred language (English, Hindi, etc.)")
    seat_preference: Optional[str] = Field(default=None, description="Seat tier or preference (e.g. middle, recliner)")
    missing_information: List[str] = Field(default_factory=list, description="Essential fields missing from prompt")
    requires_confirmation: bool = Field(default=True, description="Strict safety gate required before final commit")
    raw_query: str = Field(default="", description="Original user prompt")
    normalized_query: str = Field(default="", description="Normalized query text")
    confidence: float = Field(default=1.0, description="Confidence score of intent extraction")

    @property
    def is_complete(self) -> bool:
        """Returns True if all required fields are present."""
        return len(self.missing_information) == 0

    def to_summary(self) -> str:
        """Formatted human-readable summary for operator confirmation."""
        lines = [
            f"Type: {self.booking_type.capitalize()}",
            f"Title: {self.movie_name or '[Not Specified]'}",
            f"City: {self.city or '[Not Specified]'}",
            f"Venue: {self.venue or 'Any Available'}",
            f"Date: {self.date or 'Today / Next Available'}",
            f"Time: {self.time_preference or 'Any Available'}",
            f"Tickets: {self.ticket_count}",
        ]
        if self.missing_information:
            lines.append(f"Missing Details: {', '.join(self.missing_information)}")
        return "\n".join(lines)


# Known Indian & Global Cities for disambiguation
KNOWN_CITIES = {
    "kochi": "Kochi",
    "cochin": "Kochi",
    "ernakulam": "Kochi",
    "kollam": "Kollam",
    "quilon": "Kollam",
    "trivandrum": "Trivandrum",
    "thiruvananthapuram": "Trivandrum",
    "bengaluru": "Bengaluru",
    "bangalore": "Bengaluru",
    "delhi": "Delhi",
    "new delhi": "Delhi",
    "ncr": "Delhi",
    "mumbai": "Mumbai",
    "bombay": "Mumbai",
    "chennai": "Chennai",
    "madras": "Chennai",
    "hyderabad": "Hyderabad",
    "pune": "Pune",
    "kolkata": "Kolkata",
    "calcutta": "Kolkata",
    "calicut": "Kozhikode",
    "kozhikode": "Kozhikode",
}

# Known Theatre Chains
KNOWN_VENUES = [
    "pvr", "inox", "cinepolis", "jb cinemas", "miraj", "carnival", "wave", "movietime"
]


class IntentExtractor:
    """Robust, deterministic and LLM-augmented intent extractor."""

    @classmethod
    def clean_text(cls, text: str) -> str:
        """Normalizes punctuation, capitalization, and whitespace."""
        t = (text or "").strip()
        # Remove trailing and leading quotes/punctuation
        t = re.sub(r"^[\s\"']+|[\s\"'\.\?!]+$", "", t)
        # Collapse multiple spaces
        t = re.sub(r"\s+", " ", t)
        return t

    @classmethod
    def parse_deterministic(cls, raw_query: str, default_city: Optional[str] = None) -> BookingIntent:
        """
        High-precision deterministic parser that dismantles sentence structure
        without corrupting title entities.
        """
        cleaned = cls.clean_text(raw_query)
        lower = cleaned.lower()

        # 1. Determine booking type
        booking_type = "movie"
        if any(w in lower for w in ("flight", "fly", "plane", "airline")):
            booking_type = "flight"
        elif any(w in lower for w in ("train", "irctc", "railway")):
            booking_type = "train"
        elif any(w in lower for w in ("bus", "redbus")):
            booking_type = "bus"
        elif any(w in lower for w in ("concert", "event", "play", "standup", "comedy show")):
            booking_type = "event"

        # 2. Extract ticket count
        ticket_count = 1
        m_count = re.search(r"\b(\d+)\s*(?:tickets?|seats?|passes?)\b", lower)
        if m_count:
            ticket_count = int(m_count.group(1))
        else:
            word_counts = {
                "one": 1, "a": 1, "an": 1, "single": 1,
                "two": 2, "pair": 2, "couple": 2,
                "three": 3, "four": 4, "five": 5, "six": 6
            }
            m_word = re.search(r"\b(one|a|an|single|two|pair|couple|three|four|five|six)\s+(?:tickets?|seats?|passes?)\b", lower)
            if m_word:
                ticket_count = word_counts.get(m_word.group(1), 1)
            elif re.search(r"\btickets\b|\bseats\b", lower):
                # Plural without explicit number defaults to 2
                ticket_count = 2
            elif re.search(r"\bticket\b|\bseat\b", lower):
                ticket_count = 1

        # 3. Extract City
        city = None
        # Check explicit "in <city>" or "at <city>"
        m_city_prep = re.search(r"\b(?:in|at|city)\s+([a-zA-Z\s]+?)(?:\s+(?:tomorrow|today|tonight|this|on|at|for|show|showtime|theatre|cinema|the)\b|$)", lower)
        if m_city_prep:
            candidate_city = m_city_prep.group(1).strip()
            for k, canonical in KNOWN_CITIES.items():
                if k in candidate_city.lower():
                    city = canonical
                    break
            if not city and candidate_city and candidate_city not in ("a", "an", "the", "movie", "theatre"):
                city = candidate_city.capitalize()

        # Check known cities anywhere in string if not yet found
        if not city:
            for k, canonical in KNOWN_CITIES.items():
                if re.search(rf"\b{k}\b", lower):
                    city = canonical
                    break

        # 4. Extract Venue / Theatre
        venue = None
        m_venue = re.search(r"\b(?:at|theatre|cinema|hall)\s+([a-zA-Z0-9\s]+?)(?:\s+(?:in|on|at|for|tomorrow|today)\b|$)", lower)
        for kv in KNOWN_VENUES:
            if kv in lower:
                venue = kv.title()
                break

        # 5. Extract Date
        date = None
        if "tomorrow" in lower:
            date = "tomorrow"
        elif "today" in lower or "tonight" in lower:
            date = "today"
        elif "weekend" in lower or "this weekend" in lower:
            date = "weekend"
        else:
            m_date = re.search(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", lower)
            if m_date:
                date = m_date.group(1)

        # 6. Extract Time Preference
        time_pref = None
        if "evening" in lower:
            time_pref = "evening"
        elif "morning" in lower:
            time_pref = "morning"
        elif "afternoon" in lower or "matinee" in lower:
            time_pref = "afternoon"
        elif "night" in lower or "late night" in lower:
            time_pref = "night"
        elif "next available" in lower or "next show" in lower:
            time_pref = "next available"
        else:
            m_time = re.search(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b", lower)
            if m_time:
                time_pref = m_time.group(1).upper()

        # 7. Extract Movie / Event Title
        movie_name = None
        # Check if user said "this movie" or generic reference without title
        if re.search(r"\b(this movie|this film|a movie|the movie)\b", lower) and not any(m in lower for m in ("avenger", "avengers", "inception", "interstellar", "batman", "oppenheimer")):
            movie_name = None
        else:
            # Strip command prefixes
            # e.g. "book an avenger movie ticket" -> "avenger"
            # e.g. "find tickets for avengers" -> "avengers"
            # e.g. "find an evening show for avengers in kochi" -> "avengers"
            work_text = cleaned

            # Strip leading action verbs
            work_text = re.sub(r"^(?:please\s+)?(?:book|buy|find|search(?:\s+for)?|look(?:\s+for)?|show(?:\s+me)?|get|reserve)\s+", "", work_text, flags=re.I)
            
            # Strip ticket counts & tokens at start
            work_text = re.sub(r"^(?:an?|\d+|one|two|three|four|pair\s+of)?\s*(?:movie|film)?\s*(?:tickets?|seats?|passes?)\s*(?:for)?\s*", "", work_text, flags=re.I)
            work_text = re.sub(r"^(?:an?|the)\s+(?:evening|morning|afternoon|night|next|available)?\s*(?:show(?:\s+for)?|showtime(?:\s+for)?)\s*", "", work_text, flags=re.I)
            work_text = re.sub(r"^(?:for)\s+", "", work_text, flags=re.I)

            # Strip trailing metadata from the end
            if city:
                work_text = re.sub(rf"\s+\b(?:in|at)\s+{re.escape(city)}\b.*$", "", work_text, flags=re.I)
            for k in KNOWN_CITIES.keys():
                work_text = re.sub(rf"\s+\b(?:in|at)\s+{re.escape(k)}\b.*$", "", work_text, flags=re.I)

            if venue:
                work_text = re.sub(rf"\s+\b(?:at|in)\s+{re.escape(venue)}\b.*$", "", work_text, flags=re.I)

            if date:
                work_text = re.sub(rf"(?:\s+|^)\b{re.escape(date)}\b.*$", "", work_text, flags=re.I)

            if time_pref and time_pref != "next available":
                work_text = re.sub(rf"(?:\s+|^)\b(?:at\s+)?{re.escape(time_pref.lower())}\b.*$", "", work_text, flags=re.I)

            # Strip trailing "movie tickets", "tickets", "movie", "film"
            work_text = re.sub(r"\s+\b(?:movie|film)?\s*(?:tickets?|seats?|passes?)\b.*$", "", work_text, flags=re.I)
            work_text = re.sub(r"\s+\b(?:movie|film)\b.*$", "", work_text, flags=re.I)

            work_text = cls.clean_text(work_text)
            
            # Normalize title
            if work_text:
                # Canonicalize singular/plural common titles if needed
                if work_text.lower() == "avenger":
                    work_text = "Avengers"
                elif work_text.lower() in ("avengers", "the avengers"):
                    work_text = "Avengers"
                elif work_text.lower() in ("tickets", "two tickets", "the movie", "this movie"):
                    work_text = None
                
                movie_name = work_text

        # 8. Identify Missing Information
        missing = []
        if not movie_name:
            missing.append("movie_name")
        if not city:
            if default_city:
                city = default_city
            else:
                missing.append("city")
        if not date:
            missing.append("date")
        if not time_pref:
            missing.append("time_preference")

        return BookingIntent(
            booking_type=booking_type,
            movie_name=movie_name,
            city=city,
            venue=venue,
            date=date,
            time_preference=time_pref,
            ticket_count=ticket_count,
            missing_information=missing,
            requires_confirmation=True,
            raw_query=raw_query,
            normalized_query=cleaned,
            confidence=0.95 if movie_name else 0.5,
        )

    @classmethod
    async def parse_with_llm(cls, raw_query: str, default_city: Optional[str] = None) -> BookingIntent:
        """
        Attempts structured JSON extraction using Gemini if available,
        otherwise gracefully falls back to deterministic parsing.
        """
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return cls.parse_deterministic(raw_query, default_city=default_city)

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            prompt = f"""Extract structured ticket-booking intent from this request:
"{raw_query}"

Respond STRICTLY with a JSON object matching this schema:
{{
  "booking_type": "movie" | "flight" | "train" | "bus" | "event",
  "movie_name": string or null (extract ONLY the clean title, strip words like "movie ticket", "show", etc.),
  "city": string or null (e.g. "Kochi", "Kollam", "Mumbai"),
  "venue": string or null,
  "date": string or null (e.g. "tomorrow", "today", "2026-10-15"),
  "time_preference": string or null (e.g. "evening", "09:15 PM"),
  "ticket_count": integer (default 1 if singular like 'a ticket', 2 if plural 'tickets'),
  "language": string or null,
  "missing_information": array of strings (list any of: "movie_name", "city", "date", "time_preference" that were NOT specified)
}}
"""
            response = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-flash-latest",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0
                )
            )

            text = response.text.strip()
            data = json.loads(text)
            
            missing = data.get("missing_information", [])
            city = data.get("city") or default_city
            if not city and "city" not in missing:
                missing.append("city")

            return BookingIntent(
                booking_type=data.get("booking_type", "movie"),
                movie_name=data.get("movie_name"),
                city=city,
                venue=data.get("venue"),
                date=data.get("date"),
                time_preference=data.get("time_preference"),
                ticket_count=int(data.get("ticket_count", 1)),
                language=data.get("language"),
                missing_information=missing,
                requires_confirmation=True,
                raw_query=raw_query,
                normalized_query=cls.clean_text(raw_query),
                confidence=0.98,
            )
        except Exception as e:
            logger.warning(f"LLM intent parsing fallback note: {e}. Using deterministic parser.")
            return cls.parse_deterministic(raw_query, default_city=default_city)


def parse_booking_intent(query: str, default_city: Optional[str] = None, use_llm: bool = False) -> BookingIntent:
    """Convenience synchronous entrypoint for intent extraction."""
    return IntentExtractor.parse_deterministic(query, default_city=default_city)
