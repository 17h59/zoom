import pytest

from zoombots.bot import classify_text


@pytest.mark.parametrize("text,expected", [
    ("Please wait, the meeting host will let you in soon.", "waiting"),
    ("Veuillez patienter, l'hôte va vous admettre", "waiting"),
    ("The host has removed you from this meeting", "denied"),
    ("This meeting has been ended by host", "ended"),
    ("Invalid meeting ID", "failed"),
    ("Enter your name", "unknown"),
])
def test_classify(text, expected):
    assert classify_text(text) == expected


def test_toolbar_wins_over_stale_text():
    assert classify_text("waiting room", in_toolbar=True) == "in_meeting"
