import pytest

from app.url_parser import InvalidMeetingUrl, parse_capture_url, parse_meeting_url


def test_parse_meeting_url_ok() -> None:
    host, room = parse_meeting_url("https://meet.realweb.ru/TeamStandup")
    assert host == "meet.realweb.ru"
    assert room == "TeamStandup"


def test_parse_meeting_url_nested_path() -> None:
    host, room = parse_meeting_url("https://meet.example.com/a/b/RoomName")
    assert host == "meet.example.com"
    assert room == "RoomName"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "ftp://meet.example.com/Room",
        "https://meet.example.com/",
        "not-a-url",
    ],
)
def test_parse_meeting_url_invalid(url: str) -> None:
    with pytest.raises(InvalidMeetingUrl):
        parse_meeting_url(url)


def test_parse_capture_url_telemost() -> None:
    host, room = parse_capture_url(
        "telemost",
        "https://telemost.yandex.ru/j/16448943383326",
    )
    assert host == "telemost.yandex.ru"
    assert room == "16448943383326"


def test_parse_capture_url_telemost_private_join() -> None:
    host, room = parse_capture_url(
        "telemost",
        "https://telemost.yandex.ru/private-join/999",
    )
    assert host == "telemost.yandex.ru"
    assert room == "999"


@pytest.mark.parametrize(
    "url",
    [
        "https://meet.example.com/Room",
        "https://evil.example/j/1",
    ],
)
def test_parse_capture_url_telemost_rejects_non_telemost(url: str) -> None:
    with pytest.raises(InvalidMeetingUrl):
        parse_capture_url("telemost", url)
