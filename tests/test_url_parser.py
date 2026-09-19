import pytest

from app.url_parser import InvalidMeetingUrl, parse_meeting_url


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
