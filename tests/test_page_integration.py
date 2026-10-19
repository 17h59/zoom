"""Intégration avec un vrai Chromium sur de fausses pages Zoom (aucun accès réseau)."""

import asyncio
import glob

import pytest
from playwright.async_api import async_playwright

from zoombots import bot as botmod
from zoombots.bot import Bot
from zoombots.meeting import Meeting

TOOLBAR = '<button aria-label="mute my microphone">M</button><button>Leave</button>'


@pytest.fixture
async def page():
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch()
        except Exception:  # noqa: BLE001 - version de Playwright ≠ Chromium installé : on tente un binaire existant
            found = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
            if not found:
                pytest.skip("Chromium indisponible")
            browser = await pw.chromium.launch(executable_path=found[-1], args=["--no-sandbox"])
        page = await browser.new_page()
        yield page
        await browser.close()


@pytest.fixture
def bot(page):
    b = Bot(Meeting("123456789"), "t")
    b._page = page
    b.status = "in_meeting"
    return b


async def test_chat_text_does_not_end_the_session(page, bot):
    await page.set_content(f"{TOOLBAR}<div class=chat>Alice: I refuse. The host has removed you lol. This meeting has ended</div>")
    assert await bot._page_state(page) == "in_meeting"
    await page.set_content("<div class=chat>Refusé joined. host has removed you</div>")  # barre masquée, texte de chat seul
    assert await bot._page_state(page) == "unknown"


async def test_system_dialog_is_recognised(page, bot):
    await page.set_content('<div role="dialog"><h2>The host has removed you from this meeting</h2></div>')
    assert await bot._page_state(page) == "denied"
    await page.set_content("<h1>This meeting has been ended by host</h1>")
    assert await bot._page_state(page) == "ended"


async def test_waiting_room_only_counts_during_admission(page, bot):
    await page.set_content("<p>Please wait, the meeting host will let you in soon.</p>")
    assert await bot._page_state(page, include_body=True) == "waiting"
    assert await bot._page_state(page) == "unknown"


async def test_watch_needs_consecutive_cycles(page, bot, monkeypatch):
    monkeypatch.setattr(botmod, "WATCH_INTERVAL_S", 0.01)
    bot._browser = page.context.browser
    await page.set_content('<h1>This meeting has been ended by host</h1>')
    assert await asyncio.wait_for(bot._watch(), 5) == "ended"
    # un message système passager (barre de retour avant LOST_CYCLES) n'arrête pas la surveillance
    await page.set_content(TOOLBAR)
    task = asyncio.create_task(bot._watch())
    await asyncio.sleep(0.1)
    assert not task.done()
    bot._closing = True
    assert await asyncio.wait_for(task, 5) == "closed"


async def test_set_mic_toggles_only_when_needed(page, bot, monkeypatch):
    await page.set_content("""<button id=m aria-label="unmute my microphone"
        onclick="this.ariaLabel = this.ariaLabel.startsWith('un') ? 'mute my microphone' : 'unmute my microphone'">M</button>
        <button aria-label="Select a microphone">v</button>""")
    assert await bot.set_mic(True) is True
    assert await bot.set_mic(True) is False  # déjà actif : pas de second clic
    assert await bot.set_mic(False) is True


async def test_missing_mic_button_is_a_clean_error(page, bot):
    await page.set_content("<p>rien</p>")
    with pytest.raises(RuntimeError):
        await bot.set_mic(True)
