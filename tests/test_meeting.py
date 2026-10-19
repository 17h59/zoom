import pytest

from zoombots.meeting import parse_meeting


def test_raw_id_with_spaces():
    assert parse_meeting("123 4567 8901").id == "12345678901"


def test_link_with_pwd():
    m = parse_meeting("https://us02web.zoom.us/j/81234567890?pwd=abc")
    assert (m.id, m.passcode, m.host) == ("81234567890", "abc", "us02web.zoom.us")
    assert m.join_url == "https://us02web.zoom.us/wc/join/81234567890?pwd=abc"


def test_explicit_passcode_wins():
    assert parse_meeting("https://zoom.us/j/81234567890?pwd=abc", "xyz").passcode == "xyz"


@pytest.mark.parametrize("link,host", [
    ("https://zoom.us/j/81234567890", "app.zoom.us"),
    ("https://www.zoom.us/j/81234567890", "app.zoom.us"),
    ("https://acme.zoom.us/j/81234567890", "acme.zoom.us"),
    ("https://www.zoomgov.com/j/81234567890", "www.zoomgov.com"),
    ("https://zoomgov.com/j/81234567890", "www.zoomgov.com"),
    ("https://acme.zoomgov.com/wc/join/81234567890", "acme.zoomgov.com"),
    ("https://zoom.com.cn/j/81234567890", "zoom.com.cn"),
    ("zoom.us/s/81234567890", "app.zoom.us"),
])
def test_zoom_domains(link, host):
    m = parse_meeting(link)
    assert (m.id, m.host) == ("81234567890", host)


def test_extra_domain_via_env(monkeypatch):
    with pytest.raises(ValueError):
        parse_meeting("https://meet.example.com/j/81234567890")
    monkeypatch.setenv("ZOOMBOTS_EXTRA_DOMAINS", "meet.example.com")
    assert parse_meeting("https://meet.example.com/j/81234567890").host == "meet.example.com"


@pytest.mark.parametrize("evil", [
    "https://evil.com/j/81234567890",
    "https://zoom.us.evil.com/j/81234567890",
    "https://evilzoom.us/j/81234567890",
    "https://zoom.us@evil.com/j/81234567890",
    "ftp://zoom.us/j/81234567890",
])
def test_foreign_hosts_rejected(evil):
    with pytest.raises(ValueError):
        parse_meeting(evil)


def test_long_id_is_not_truncated():
    with pytest.raises(ValueError):
        parse_meeting("https://zoom.us/j/812345678901234")


def test_passcode_is_url_encoded():
    assert parse_meeting("123456789", "a&b=1#x").join_url.endswith("?pwd=a%26b%3D1%23x")


@pytest.mark.parametrize("bad", ["abc", "123", "https://zoom.us/foo"])
def test_invalid(bad):
    with pytest.raises(ValueError):
        parse_meeting(bad)
