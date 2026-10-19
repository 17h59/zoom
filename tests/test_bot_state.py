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
    ("This meeting link is invalid (3,001)", "failed"),
    ("Automated bots aren't allowed to join this meeting.", "denied"),
    ("Les robots automatisés ne sont pas autorisés à rejoindre cette réunion.", "denied"),
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


def test_original_sound_init_script_disables_3a():
    from zoombots.bot import DISABLE_DENOISE, ORIGINAL_SOUND_INIT_SCRIPT
    assert DISABLE_DENOISE is True
    # Vérifie que les contraintes WebRTC 3A (AEC, AGC, ANS) sont bien forcées à false
    assert "noiseSuppression = false" in ORIGINAL_SOUND_INIT_SCRIPT
    assert "echoCancellation = false" in ORIGINAL_SOUND_INIT_SCRIPT
    assert "autoGainControl = false" in ORIGINAL_SOUND_INIT_SCRIPT
    # Vérifie que le profil Zoom originalSound et le SDK JS média sont interceptés
    assert "originalSound" in ORIGINAL_SOUND_INIT_SCRIPT
    assert "JsMediaSDK_Instance" in ORIGINAL_SOUND_INIT_SCRIPT
    assert "easyStore" in ORIGINAL_SOUND_INIT_SCRIPT
    assert "easy-local-storage" in ORIGINAL_SOUND_INIT_SCRIPT


@pytest.mark.parametrize("label,matches", [
    ("Turn on Original Sound", True),
    ("Original Sound: Off", True),
    ("Original Sound (off)", True),
    ("Activer le son d'origine", True),
    ("Son d'origine : Désactivé", True),
    ("Turn off Original Sound", False),
    ("Original Sound: On", False),
    ("Leave", False),
    ("Mute", False),
])
def test_original_sound_button_regex(label, matches):
    from zoombots.bot import ORIGINAL_SOUND_BUTTON
    assert bool(ORIGINAL_SOUND_BUTTON.search(label)) == matches


@pytest.mark.asyncio
async def test_ensure_original_sound_clicks_button():
    clicked = []

    class FakeLocator:
        def __init__(self, count):
            self._count = count

        async def count(self):
            return self._count

        async def click(self, timeout=None):
            clicked.append(1)

        @property
        def first(self):
            return self

    class FakePage:
        def __init__(self, has_button):
            self.has_button = has_button

        def is_closed(self):
            return False

        def get_by_role(self, role, name=None):
            return FakeLocator(1 if self.has_button else 0)

    bot = Bot(Meeting("123456789"), "t")
    await bot._ensure_original_sound(FakePage(has_button=True))
    assert clicked == [1]

    clicked.clear()
    await bot._ensure_original_sound(FakePage(has_button=False))
    assert clicked == []

