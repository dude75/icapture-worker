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
