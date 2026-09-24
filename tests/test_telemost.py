"""Unit tests for Yandex Telemost URL and navigation helpers."""

import pytest

from app.capture.telemost import (
    build_private_join_url,
    telemost_meeting_id,
    validate_telemost_meeting_url,
)
from app.url_parser import InvalidMeetingUrl, parse_capture_url


def test_validate_telemost_guest_link() -> None:
    url = validate_telemost_meeting_url("https://telemost.yandex.ru/j/16448943383326")
    assert url.startswith("https://telemost.yandex.ru/j/")


def test_build_private_join_url_mic_camera_off() -> None:
    guest = "https://telemost.yandex.ru/j/12345"
    nav = build_private_join_url(guest)
    assert nav.startswith("https://telemost.yandex.ru/private-join/12345?")
    assert "mic=off" in nav
    assert "camera=off" in nav


def test_telemost_meeting_id_from_private_join() -> None:
    assert telemost_meeting_id("https://telemost.yandex.ru/private-join/abc") == "abc"


@pytest.mark.parametrize(
    "url",
    [
        "https://meet.example.com/Room",
        "https://telemost.evil.ru/j/1",
        "https://telemost.yandex.ru/",
    ],
)
def test_validate_telemost_rejects(url: str) -> None:
    with pytest.raises(InvalidMeetingUrl):
        validate_telemost_meeting_url(url)


def test_parse_capture_jitsi_unchanged() -> None:
    host, room = parse_capture_url("jitsi", "https://meet.example.com/MyRoom")
    assert host == "meet.example.com"
    assert room == "MyRoom"
