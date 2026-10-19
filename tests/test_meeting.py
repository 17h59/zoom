import pytest

from zoombots.meeting import parse_meeting


def test_raw_id_with_spaces():
    assert parse_meeting("123 4567 8901").id == "12345678901"


def test_link_with_pwd():
    m = parse_meeting("https://us02web.zoom.us/j/81234567890?pwd=abc")
    assert (m.id, m.passcode) == ("81234567890", "abc")
    assert m.join_url == "https://app.zoom.us/wc/join/81234567890?pwd=abc"


def test_explicit_passcode_wins():
    assert parse_meeting("https://zoom.us/j/81234567890?pwd=abc", "xyz").passcode == "xyz"


@pytest.mark.parametrize("bad", ["abc", "123", "https://zoom.us/foo"])
def test_invalid(bad):
    with pytest.raises(ValueError):
        parse_meeting(bad)
