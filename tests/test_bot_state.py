import pytest

from zoombots.bot import Bot, classify_text
from zoombots.meeting import Meeting


@pytest.mark.parametrize("text,expected", [
    ("Please wait, the meeting host will let you in soon.", "waiting"),
    ("Veuillez patienter, l'hôte va vous admettre", "waiting"),
    ("The host has removed you from this meeting", "denied"),
    ("L'hôte vous a retiré de la réunion", "denied"),
    ("This meeting has been ended by host", "ended"),
    ("Cette réunion est terminée", "ended"),
    ("Invalid meeting ID", "failed"),
    ("Enter your name", "unknown"),
])
def test_classify(text, expected):
    assert classify_text(text) == expected


@pytest.mark.parametrize("text", [
    "Refusé joined the meeting",
    "I refuse",
    "a retiré le fichier",
    "Unable to join audio",
    "Alice: the meeting is over there",
    "waiting for the ball",
])
def test_ordinary_text_is_not_a_state(text):
    assert classify_text(text) == "unknown"


def test_toolbar_wins_over_stale_text():
    assert classify_text("waiting room", in_toolbar=True) == "in_meeting"


def test_passcode_is_scrubbed_from_errors():
    bot = Bot(Meeting("123456789", "s3cret"), "t")
    bot._set_status("error", "goto https://app.zoom.us/wc/join/1?pwd=s3cret failed; also s3cret")
    assert "s3cret" not in bot.error and "s3cret" not in str(list(bot.events))
