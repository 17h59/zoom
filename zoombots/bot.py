import asyncio
import os
import re
import uuid
from pathlib import Path

from playwright.async_api import Browser, Page, Playwright

from .audio import VirtualMic
from .meeting import Meeting

HEADED = os.environ.get("ZOOMBOTS_HEADED") == "1"  # à combiner avec xvfb-run
JOIN_TIMEOUT_MS = 60_000

MIC_BUTTON = re.compile(r"(un)?mute|micro", re.I)


class Bot:
    def __init__(self, meeting: Meeting, name: str):
        self.id = uuid.uuid4().hex[:8]
        self.meeting = meeting
        self.name = name
        self.status = "created"  # created | joining | in_meeting | error | left
        self.error: str | None = None
        self.mic = VirtualMic(self.id)
        self._browser: Browser | None = None
        self._page: Page | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "meeting": self.meeting.id,
            "status": self.status,
            "error": self.error,
            "playing": self.mic.playing,
        }

    async def join(self, pw: Playwright) -> None:
        self.status = "joining"
        try:
            await self.mic.create()
            self._browser = await pw.chromium.launch(
                headless=not HEADED,
                env={**os.environ, "PULSE_SOURCE": self.mic.source},
                args=["--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
            )
            context = await self._browser.new_context(permissions=["microphone"])
            self._page = await context.new_page()
            await self._join_flow(self._page)
            self.status = "in_meeting"
        except Exception as exc:  # noqa: BLE001 - on remonte tout au dashboard
            self.status, self.error = "error", str(exc)
            await self._cleanup()

    async def _join_flow(self, page: Page) -> None:
        await page.goto(self.meeting.join_url, wait_until="domcontentloaded")
        # bandeau cookies, s'il y en a un
        cookies = page.locator("#onetrust-accept-btn-handler")
        if await cookies.count():
            await cookies.click()

        await page.locator("#input-for-name").fill(self.name, timeout=JOIN_TIMEOUT_MS)
        if self.meeting.passcode:
            pwd = page.locator("#input-for-pwd")
            if await pwd.count():
                await pwd.fill(self.meeting.passcode)
        await page.get_by_role("button", name=re.compile(r"^(join|rejoindre)", re.I)).first.click()

        # salle d'attente éventuelle : on attend d'être admis, puis audio par ordinateur
        audio = page.get_by_role("button", name=re.compile(r"(join audio|audio by computer|audio par ordinateur)", re.I))
        await audio.first.click(timeout=JOIN_TIMEOUT_MS * 5)

    async def set_mic(self, on: bool) -> None:
        """Dé-mute (on=True) ou mute le bot via le bouton micro de la barre Zoom."""
        page = self._require_page()
        button = page.get_by_role("button", name=MIC_BUTTON).first
        label = (await button.get_attribute("aria-label") or "").lower()
        is_muted = label.startswith(("unmute", "activer", "réactiver"))
        if is_muted == on:
            await button.click()

    async def play(self, path: Path, volume: float = 1.0, auto_unmute: bool = True) -> None:
        if self.status != "in_meeting":
            raise RuntimeError("le bot n'est pas dans la réunion")
        if auto_unmute:
            await self.set_mic(True)
        await self.mic.play(path, volume)

    def stop_sounds(self) -> None:
        self.mic.stop()

    async def leave(self) -> None:
        await self._cleanup()
        self.status = "left"

    async def _cleanup(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = self._page = None
        await self.mic.destroy()

    def _require_page(self) -> Page:
        if not self._page or self.status != "in_meeting":
            raise RuntimeError("le bot n'est pas dans la réunion")
        return self._page
