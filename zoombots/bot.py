import asyncio
import logging
import os
import re
import time
import uuid
from collections import deque
from pathlib import Path

from playwright.async_api import Browser, Page, Playwright

from . import stats
from .audio import VirtualMic
from .log import DATA_DIR, log_event
from .meeting import Meeting

logger = logging.getLogger("zoombots.bot")

HEADED = os.environ.get("ZOOMBOTS_HEADED") == "1"  # à combiner avec xvfb-run
CHROMIUM_PATH = os.environ.get("ZOOMBOTS_CHROMIUM") or None
JOIN_TIMEOUT_MS = 60_000
ADMIT_TIMEOUT_S = int(os.environ.get("ZOOMBOTS_ADMIT_TIMEOUT", "600"))  # attente max en salle d'attente
WATCH_INTERVAL_S = float(os.environ.get("ZOOMBOTS_WATCH_INTERVAL", "3"))
MAX_RECONNECTS = int(os.environ.get("ZOOMBOTS_MAX_RECONNECTS", "3"))
SCREENSHOT_DIR = Path(DATA_DIR) / "screenshots"

# Réduit la consommation d'un Chromium par bot (pas de GPU, pas de sortie audio, petit viewport).
LITE_ARGS = [
    "--disable-gpu", "--disable-dev-shm-usage", "--mute-audio", "--disable-extensions",
    "--disable-background-networking", "--disable-sync", "--no-first-run",
]
MIC_BUTTON = re.compile(r"(un)?mute|micro", re.I)

# Textes de l'interface Zoom (anglais / français) servant à classer l'état de la page.
_WAITING = re.compile(r"(waiting for the host|host will let you in|please wait, the meeting host|"
                      r"waiting room|salle d.attente|attendez que l.h[ôo]te|l.h[ôo]te va vous (laisser|admettre))", re.I)
_DENIED = re.compile(r"(host has removed you|removed you from this meeting|denied (your )?(request|entry)|"
                     r"you have been removed|a retiré|vous a retiré|refus[ée]|admission (was )?denied)", re.I)
_ENDED = re.compile(r"(meeting has (been )?ended|meeting is over|this meeting has ended|"
                    r"la réunion (a|est) (été )?(termin|fini)|réunion terminée)", re.I)
_FAILED = re.compile(r"(invalid meeting|meeting id is not valid|n.existe pas|id de réunion (invalide|incorrect)|"
                     r"can.t join|unable to join)", re.I)

JOIN_AUDIO = re.compile(r"(join audio|audio by computer|computer audio|audio par ordinateur|rejoindre l.audio)", re.I)
JOIN_BUTTON = re.compile(r"^(join|rejoindre)", re.I)
LEAVE_BUTTON = re.compile(r"^(leave|quitter)", re.I)


class JoinDenied(RuntimeError):
    """Refus d'admission, expulsion ou réunion invalide : inutile de réessayer."""


class MeetingEnded(RuntimeError):
    pass


def classify_text(text: str, in_toolbar: bool = False) -> str:
    """Classe l'état d'une page Zoom à partir de son texte visible.

    Renvoie : in_meeting | waiting | denied | ended | failed | unknown.
    La barre d'outils (bouton Leave / micro présent) prime sur le texte résiduel."""
    if in_toolbar:
        return "in_meeting"
    for state, pattern in (("ended", _ENDED), ("denied", _DENIED), ("failed", _FAILED), ("waiting", _WAITING)):
        if pattern.search(text):
            return state
    return "unknown"


class Bot:
    def __init__(self, meeting: Meeting, name: str, bot_id: str | None = None,
                 auto_reconnect: bool = True, on_change=None):
        self.id = bot_id or uuid.uuid4().hex[:8]
        self.meeting = meeting
        self.name = name
        self.auto_reconnect = auto_reconnect
        # created | joining | waiting_room | in_meeting | reconnecting | ended | denied | error | left
        self.status = "created"
        self.error: str | None = None
        self.reconnects = 0
        self.events: deque[dict] = deque(maxlen=50)
        self.mic = VirtualMic(self.id)
        self._on_change = on_change
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._closing = False

    # -- état ---------------------------------------------------------------

    def _set_status(self, status: str, error: str | None = None) -> None:
        self.status, self.error = status, error
        self.events.append(log_event(logger, "status", bot=self.id, meeting=self.meeting.id, status=status, error=error))
        if self._on_change:
            self._on_change()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "meeting": self.meeting.id,
            "status": self.status,
            "error": self.error,
            "playing": self.mic.playing,
            "reconnects": self.reconnects,
            "auto_reconnect": self.auto_reconnect,
        }

    def to_state(self) -> dict:
        return {"id": self.id, "meeting": self.meeting.id, "passcode": self.meeting.passcode,
                "name": self.name, "auto_reconnect": self.auto_reconnect}

    def resources(self) -> dict:
        return stats.process_usage(stats.bot_processes(self.mic.source))

    # -- cycle de vie -------------------------------------------------------

    async def run(self, pw: Playwright, join_gate: asyncio.Semaphore | None = None) -> None:
        """Rejoint la réunion, la surveille, et se reconnecte si le bot est coupé ou expulsé."""
        attempt = 0
        while not self._closing:
            try:
                if join_gate:
                    async with join_gate:
                        await self._connect(pw)
                else:
                    await self._connect(pw)
                attempt = 0
                reason = await self._watch()
            except JoinDenied as exc:
                await self._cleanup()
                self._set_status("denied", str(exc))
                return
            except MeetingEnded as exc:
                await self._cleanup()
                self._set_status("ended", str(exc))
                return
            except Exception as exc:  # noqa: BLE001 - on remonte tout au dashboard
                if self._closing:
                    return
                await self._screenshot("error")
                await self._cleanup()
                reason = f"erreur: {exc}"
                logger.warning("join failed", extra={"fields": {"bot": self.id, "error": str(exc)}})
            if self._closing:
                return
            if reason in ("ended", "denied"):
                await self._cleanup()
                self._set_status(reason, "réunion terminée" if reason == "ended" else "expulsé ou refusé")
                return
            await self._cleanup()
            attempt += 1
            if not self.auto_reconnect or attempt > MAX_RECONNECTS:
                self._set_status("error", f"{reason} (abandon après {attempt - 1} reconnexion(s))")
                return
            self.reconnects += 1
            self._set_status("reconnecting", reason)
            await asyncio.sleep(min(2 ** attempt, 30))

    async def _connect(self, pw: Playwright) -> None:
        self._set_status("joining")
        await self.mic.create()
        self._browser = await pw.chromium.launch(
            headless=not HEADED,
            executable_path=CHROMIUM_PATH,
            env={**os.environ, "PULSE_SOURCE": self.mic.source},
            args=["--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required", *LITE_ARGS],
        )
        context = await self._browser.new_context(
            permissions=["microphone"], viewport={"width": 800, "height": 600}
        )
        self._page = await context.new_page()
        await self._join_flow(self._page)
        self._set_status("in_meeting")

    async def _page_state(self, page: Page) -> str:
        if page.is_closed():
            return "closed"
        try:
            text = await page.locator("body").inner_text(timeout=2000)
            in_toolbar = await page.get_by_role("button", name=LEAVE_BUTTON).count() > 0
        except Exception:  # noqa: BLE001 - navigation en cours, page fermée…
            return "unknown"
        return classify_text(text, in_toolbar)

    async def _join_flow(self, page: Page) -> None:
        await page.goto(self.meeting.join_url, wait_until="domcontentloaded")
        cookies = page.locator("#onetrust-accept-btn-handler")
        if await cookies.count():
            await cookies.click()

        await page.locator("#input-for-name").fill(self.name, timeout=JOIN_TIMEOUT_MS)
        if self.meeting.passcode:
            pwd = page.locator("#input-for-pwd")
            if await pwd.count():
                await pwd.fill(self.meeting.passcode)
        await page.get_by_role("button", name=JOIN_BUTTON).first.click()

        # Attente d'admission : on distingue salle d'attente, refus, fin de réunion et entrée réelle.
        deadline = time.monotonic() + ADMIT_TIMEOUT_S
        while time.monotonic() < deadline:
            audio = page.get_by_role("button", name=JOIN_AUDIO)
            if await audio.count():
                await audio.first.click()
                return
            state = await self._page_state(page)
            if state == "in_meeting":
                return
            if state == "waiting" and self.status != "waiting_room":
                self._set_status("waiting_room")
            elif state in ("denied", "failed"):
                raise JoinDenied("admission refusée ou réunion invalide")
            elif state == "ended":
                raise MeetingEnded("la réunion est terminée")
            elif state == "closed":
                raise RuntimeError("page fermée pendant la connexion")
            await asyncio.sleep(1)
        raise TimeoutError(f"pas admis après {ADMIT_TIMEOUT_S}s")

    async def _watch(self) -> str:
        """Surveille la réunion jusqu'à sa fin. Renvoie la raison : ended | denied | dropped | closed."""
        while not self._closing:
            await asyncio.sleep(WATCH_INTERVAL_S)
            if not self._browser or not self._browser.is_connected():
                return "dropped (navigateur arrêté)"
            state = await self._page_state(self._page)
            if state == "closed":
                return "dropped (page fermée)"
            if state in ("ended", "denied"):
                return state
            if state == "failed":
                return "dropped (erreur Zoom)"
        return "closed"

    async def leave(self) -> None:
        self._closing = True
        await self._cleanup()
        self._set_status("left")

    async def _cleanup(self) -> None:
        browser, self._browser, self._page = self._browser, None, None
        if browser:
            try:
                await browser.close()
            except Exception:  # noqa: BLE001 - navigateur déjà mort
                pass
        await self.mic.destroy()

    # -- observabilité ------------------------------------------------------

    async def _screenshot(self, tag: str) -> Path | None:
        page = self._page
        if not page or page.is_closed():
            return None
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOT_DIR / f"{self.id}-{tag}.png"
        try:
            await page.screenshot(path=str(path))
        except Exception:  # noqa: BLE001
            return None
        return path

    async def screenshot(self) -> bytes:
        """Capture en direct, ou la dernière capture d'erreur si le bot n'a plus de page."""
        if self._page and not self._page.is_closed():
            return await self._page.screenshot()
        last = SCREENSHOT_DIR / f"{self.id}-error.png"
        if last.exists():
            return last.read_bytes()
        raise RuntimeError("aucune capture disponible")

    # -- actions ------------------------------------------------------------

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

    def _require_page(self) -> Page:
        if not self._page or self.status != "in_meeting":
            raise RuntimeError("le bot n'est pas dans la réunion")
        return self._page
