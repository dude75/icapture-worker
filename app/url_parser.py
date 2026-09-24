"""Parse meeting URLs into host and room name."""

from __future__ import annotations

from urllib.parse import urlparse


class InvalidMeetingUrl(ValueError):
    pass


def parse_meeting_url(meeting_url: str) -> tuple[str, str]:
    raw = meeting_url.strip()
    if not raw:
        raise InvalidMeetingUrl("empty url")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise InvalidMeetingUrl("url must use http or https")
    host = parsed.hostname
    if not host:
        raise InvalidMeetingUrl("missing host")
    path = parsed.path.strip("/")
    if not path:
        raise InvalidMeetingUrl("missing room in path")
    room = path.split("/")[-1]
    if not room:
        raise InvalidMeetingUrl("missing room name")
    return host, room


def parse_capture_url(connector: str, meeting_url: str) -> tuple[str, str]:
    """Parse meeting URL for a connector into (host, room_or_meeting_id)."""
    name = connector.strip().lower()
    if name == "telemost":
        from app.capture.telemost import telemost_meeting_id, validate_telemost_meeting_url

        normalized = validate_telemost_meeting_url(meeting_url)
        parsed = urlparse(normalized)
        host = (parsed.hostname or "").lower()
        if not host:
            raise InvalidMeetingUrl("missing host")
        return host, telemost_meeting_id(normalized)
    return parse_meeting_url(meeting_url)
