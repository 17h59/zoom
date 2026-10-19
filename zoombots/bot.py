import asyncio
import logging
import os
import re
import time
import uuid
from collections import deque
from pathlib import Path

from playwright.async_api import Browser, Error as PlaywrightError, Page, Playwright

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
STABLE_SESSION_S = int(os.environ.get("ZOOMBOTS_STABLE_SESSION", "60"))  # session assez longue pour remettre le compteur à 0
LOST_CYCLES = 3  # cycles consécutifs sans barre d'outils avant de conclure à une perte
SCREENSHOT_DIR = Path(DATA_DIR) / "screenshots"

# Réduit la consommation d'un Chromium par bot (pas de GPU, pas de sortie audio, petit viewport).
LITE_ARGS = [
    "--disable-gpu", "--disable-dev-shm-usage", "--mute-audio", "--disable-extensions",
    "--disable-background-networking", "--disable-sync", "--no-first-run",
]

# Le contexte est forcé en en-US : les libellés ci-dessous sont ceux de l'interface anglaise (FR en secours).
MUTED_LABEL = re.compile(r"^(unmute|activer|réactiver)", re.I)
MIC_BUTTON = re.compile(r"^((un)?mute( my)?( audio| microphone| mic)?|(couper|activer|réactiver) (le |mon )?(micro|son))$", re.I)
JOIN_AUDIO = re.compile(r"(join audio|audio by computer|computer audio|audio par ordinateur|rejoindre l.audio)", re.I)
JOIN_BUTTON = re.compile(r"^(join|rejoindre)", re.I)
LEAVE_BUTTON = re.compile(r"^(leave|quitter)", re.I)

# Textes de l'interface Zoom classant l'état de la page. Ils ne sont appliqués qu'aux titres, dialogues et
# alertes (jamais au chat, aux sous-titres ou aux noms des participants), sauf la salle d'attente.
_WAITING = re.compile(r"(waiting for the host|host will let you in|please wait, the meeting host|"
                      r"waiting room|salle d.attente|attendez que l.h[ôo]te|l.h[ôo]te va vous (laisser|admettre))", re.I)
_DENIED = re.compile(r"(host has removed you|removed you from (this|the) meeting|your (request|entry) (was |has been )?(denied|declined)|"
                     r"l.h[ôo]te vous a retiré|vous avez été retiré|admission (was )?(denied|refus)|demande .{0,20}refus)", re.I)
_ENDED = re.compile(r"(meeting has (been )?ended|this meeting is over|"
                    r"(la|cette) réunion (a|est) (été )?(termin|fini)|réunion terminée)", re.I)
_FAILED = re.compile(r"(invalid meeting|meeting id is not valid|meeting (does not|doesn.t) exist|n.existe pas|"
                     r"id de réunion (invalide|incorrect)|unable to join (the )?meeting)", re.I)

# Zones susceptibles de porter un message système Zoom.
HEADLINE_SELECTOR = "h1, h2, h3, [role=dialog], [role=alertdialog], [role=alert]"
# Mots de passe dans les messages d'erreur (URL ?pwd=…) : jamais stockés ni renvoyés par l'API.
_PWD = re.compile(r"(pwd=)[^&\s\"')]+", re.I)


class JoinDenied(RuntimeError):
    """Refus d'admission, expulsion ou réunion invalide : inutile de réessayer."""


class MeetingEnded(RuntimeError):
    pass


def classify_text(text: str, in_toolbar: bool = False) -> str:
    """Classe l'état d'une page Zoom à partir de son texte système (titres, dialogues, alertes).

    Renvoie : in_meeting | waiting | denied | ended | failed | unknown.
    La barre d'outils (bouton Leave) prime sur le texte résiduel."""
    if in_toolbar:
        return "in_meeting"
    for state, pattern in (("ended", _ENDED), ("denied", _DENIED), ("failed", _FAILED), ("waiting", _WAITING)):
        if pattern.search(text):
            return state
    return "unknown"


class Bot:
    def __init__(self, meeting: Meeting, name: str, bot_id: str | None = None,
                 auto_reconnect: bool = True, on_change=None, owner: str | None = None):
        self.id = bot_id or uuid.uuid4().hex[:8]
        self.owner = owner  # identifiant de session GUI ; None = bot d'administration (persisté)
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
        self._task: asyncio.Task | None = None
        self._closing = False

    # -- état ---------------------------------------------------------------

    def _scrub(self, text: str | None) -> str | None:
        if not text:
            return text
        text = _PWD.sub(r"\1***", text)
        if self.meeting.passcode:
            text = text.replace(self.meeting.passcode, "***")
        return text

    def _set_status(self, status: str, error: str | None = None) -> None:
        self.status, self.error = status, self._scrub(error)
        self.events.append(log_event(logger, "status", bot=self.id, meeting=self.meeting.id,
                                     status=status, error=self.error))
        if self._on_change:
            self._on_change()

    @property
    def finished(self) -> bool:
        return self.status in ("ended", "denied", "error", "left")

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
            "owner": self.owner[:6] if self.owner else None,
        }

    def to_state(self) -> dict:
        return {"id": self.id, "meeting": self.meeting.id, "passcode": self.meeting.passcode,
                "host": self.meeting.host, "name": self.name, "auto_reconnect": self.auto_reconnect}

    # -- cycle de vie -------------------------------------------------------

    def start(self, pw: Playwright, join_gate: asyncio.Semaphore | None = None) -> None:
        self._task = asyncio.create_task(self.run(pw, join_gate))

    async def run(self, pw: Playwright, join_gate: asyncio.Semaphore | None = None) -> None:
        """Rejoint la réunion, la surveille, et se reconnecte si le bot est coupé ou expulsé."""
        try:
            await self._run(pw, join_gate)
        except asyncio.CancelledError:
            await asyncio.shield(self._cleanup())  # pas de Chromium ni de module PulseAudio orphelin
            raise

    async def _run(self, pw: Playwright, join_gate: asyncio.Semaphore | None) -> None:
        attempt = 0
        while not self._closing:
            started: float | None = None
            try:
                await self._connect(pw, join_gate)
                started = time.monotonic()
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
                reason = f"erreur: {(str(exc).splitlines() or [type(exc).__name__])[0]}"
                logger.warning("join failed", extra={"fields": {"bot": self.id, "error": self._scrub(str(exc))}})
            await self._cleanup()
            if self._closing:
                return
            if reason in ("ended", "denied"):
                self._set_status(reason, "réunion terminée" if reason == "ended" else "expulsé ou refusé")
                return
            # Le compteur ne repart de zéro qu'après une session stable : sinon une boucle
            # connexion/coupure immédiate ne s'arrêterait jamais.
            if started is not None and time.monotonic() - started >= STABLE_SESSION_S:
                attempt = 0
            attempt += 1
            if not self.auto_reconnect or attempt > MAX_RECONNECTS:
                self._set_status("error", f"{reason} (abandon après {attempt - 1} reconnexion(s))")
                return
            self.reconnects += 1
            self._set_status("reconnecting", reason)
            await asyncio.sleep(min(2 ** attempt, 30))

    async def _connect(self, pw: Playwright, join_gate: asyncio.Semaphore | None = None) -> None:
        self._set_status("joining")
        # Le sémaphore ne couvre que la phase coûteuse (Chromium, chargement, clic sur Join) :
        # l'attente en salle d'attente ne bloque pas les autres bots.
        if join_gate:
            async with join_gate:
                page = await self._open_and_submit(pw)
        else:
            page = await self._open_and_submit(pw)
        await self._await_admission(page)
        self._set_status("in_meeting")

    async def _open_and_submit(self, pw: Playwright) -> Page:
        page = await self._launch(pw)
        await self._submit_join(page)
        return page

    async def _launch(self, pw: Playwright) -> Page:
        await self.mic.create()
        self._browser = await pw.chromium.launch(
            headless=not HEADED,
            executable_path=CHROMIUM_PATH,
            env={**os.environ, "PULSE_SOURCE": self.mic.source},
            args=["--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required", *LITE_ARGS],
        )
        context = await self._browser.new_context(
            permissions=["microphone"], viewport={"width": 800, "height": 600}, locale="en-US"
        )
        self._page = await context.new_page()
        return self._page

    async def _headline_text(self, page: Page) -> str:
        """Texte des seuls éléments système (titres, dialogues, alertes)."""
        try:
            return "\n".join(await page.locator(HEADLINE_SELECTOR).all_inner_texts())
        except PlaywrightError:
            return ""

    async def _in_toolbar(self, page: Page) -> bool:
        try:
            return await page.get_by_role("button", name=LEAVE_BUTTON).count() > 0
        except PlaywrightError:
            return False

    async def _page_state(self, page: Page, include_body: bool = False) -> str:
        if page.is_closed():
            return "closed"
        try:
            text = await self._headline_text(page)
            if include_body:  # salle d'attente : message de page ordinaire, sans risque pour l'état de la session
                text += "\n" + await page.locator("body").inner_text(timeout=2000)
            in_toolbar = await self._in_toolbar(page)
        except PlaywrightError:  # navigation en cours, page fermée…
            return "unknown"
        state = classify_text(text, in_toolbar)
        if not include_body and state == "waiting":
            return "unknown"
        return state

    async def _submit_join(self, page: Page) -> None:
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

    async def _await_admission(self, page: Page) -> None:
        """Distingue salle d'attente, refus, fin de réunion et entrée réelle."""
        deadline = time.monotonic() + ADMIT_TIMEOUT_S
        while time.monotonic() < deadline:
            audio = page.get_by_role("button", name=JOIN_AUDIO)
            if await audio.count():
                await audio.first.click()
                return
            state = await self._page_state(page, include_body=True)
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
        """Surveille la réunion jusqu'à sa fin. Renvoie la raison : ended | denied | dropped… | closed.

        Une fin n'est décidée que si la barre d'outils reste absente plusieurs cycles de suite
        ET qu'un message système la confirme : le chat ou un nom de participant n'y suffisent pas."""
        lost = 0
        while not self._closing:
            await asyncio.sleep(WATCH_INTERVAL_S)
            if not self._browser or not self._browser.is_connected():
                return "dropped (navigateur arrêté)"
            state = await self._page_state(self._page)
            if state == "closed":
                return "dropped (page fermée)"
            if state == "in_meeting":
                lost = 0
                continue
            lost += 1
            if lost < LOST_CYCLES:
                continue
            if state in ("ended", "denied"):
                return state
            if state == "failed":
                return "dropped (erreur Zoom)"
        return "closed"

    async def leave(self) -> None:
        """Quitte vraiment la réunion (clic sur Leave), arrête la tâche et libère toutes les ressources."""
        self._closing = True
        await self._click_leave()
        task, self._task = self._task, None
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._cleanup()
        self._set_status("left")

    async def _click_leave(self) -> None:
        page = self._page
        if not page or page.is_closed():
            return
        try:
            await page.get_by_role("button", name=LEAVE_BUTTON).first.click(timeout=2000)
            confirm = page.get_by_role("button", name=re.compile(r"^(leave meeting|quitter la réunion)", re.I))
            if await confirm.count():
                await confirm.first.click(timeout=2000)
        except PlaywrightError:
            pass  # on ferme le navigateur quoi qu'il arrive

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
        except PlaywrightError:
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

    async def set_mic(self, on: bool) -> bool:
        """Dé-mute (on=True) ou mute le bot via le bouton micro de la barre Zoom. Renvoie True si l'état a changé."""
        page = self._require_page()
        try:
            button = page.get_by_role("button", name=MIC_BUTTON).first
            label = await button.get_attribute("aria-label", timeout=5000) or (await button.inner_text(timeout=2000))
            is_muted = bool(MUTED_LABEL.match(label.strip()))
            if is_muted != on:
                return False  # déjà dans l'état demandé
            await button.click(timeout=5000)
        except PlaywrightError as exc:  # inclut TimeoutError : bouton absent ou interface changée
            raise RuntimeError(f"bouton micro introuvable ou inutilisable ({type(exc).__name__})") from exc
        await asyncio.sleep(0.4)  # laisse Zoom ouvrir le micro avant d'envoyer le son
        return True

    async def play(self, path: Path, volume: float = 1.0, auto_unmute: bool = True) -> "asyncio.Task[bool]":
        if self.status != "in_meeting":
            raise RuntimeError("le bot n'est pas dans la réunion")
        if auto_unmute:
            await self.set_mic(True)
        return await self.mic.play(path, volume)

    async def play_wait(self, path: Path, volume: float = 1.0) -> None:
        """Joue un son et attend la fin de la lecture. Lève RuntimeError si la lecture a échoué."""
        task = await self.play(path, volume)
        await asyncio.wait({task})  # une annulation de l'appelant n'annule pas la lecture : on l'arrête explicitement
        if not task.cancelled() and task.result() is False:
            raise RuntimeError("lecture en échec (fichier illisible ?)")

    def stop_sounds(self) -> None:
        self.mic.stop()

    def _require_page(self) -> Page:
        if not self._page or self.status != "in_meeting":
            raise RuntimeError("le bot n'est pas dans la réunion")
        return self._page
