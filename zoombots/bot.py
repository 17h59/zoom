import asyncio
import logging
import os
import re
import time
import uuid
from collections import deque
from pathlib import Path

from playwright.async_api import Browser, Error as PlaywrightError, Page, Playwright

try:
    from playwright_stealth import Stealth
    _STEALTH = True
except ImportError:
    _STEALTH = False

from . import net
from .audio import VirtualMic
from .log import DATA_DIR, log_event
from .meeting import Meeting

logger = logging.getLogger("zoombots.bot")

HEADED = os.environ.get("ZOOMBOTS_HEADED") == "1"  # à combiner avec xvfb-run
CHROMIUM_PATH = os.environ.get("ZOOMBOTS_CHROMIUM") or None
# Playwright désactive le sandbox de Chromium par défaut. On l'active sauf sous root (Chromium le refuse) ou si
# ZOOMBOTS_SANDBOX=0 (conteneur sans espaces de noms utilisateur : l'isolation est alors celle du conteneur).
SANDBOX = os.environ.get("ZOOMBOTS_SANDBOX", "1" if os.geteuid() != 0 else "0") == "1"
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

DISABLE_DENOISE = os.environ.get("ZOOMBOTS_DISABLE_DENOISE", "1") == "1"

# Le contexte est forcé en en-US : les libellés ci-dessous sont ceux de l'interface anglaise (FR en secours).
MUTED_LABEL = re.compile(r"^(unmute|activer|réactiver)", re.I)
MIC_BUTTON = re.compile(r"^((un)?mute( my)?( audio| microphone| mic)?|(couper|activer|réactiver) (le |mon )?(micro|son))$", re.I)
JOIN_AUDIO = re.compile(r"(join audio|audio by computer|computer audio|audio par ordinateur|rejoindre l.audio)", re.I)
JOIN_BUTTON = re.compile(r"^(join|rejoindre)", re.I)
LEAVE_BUTTON = re.compile(r"^(leave|quitter)", re.I)
ORIGINAL_SOUND_BUTTON = re.compile(
    r"(turn on original sound|original sound: off|original sound\b.*off|activer le son d.origine|son d.origine\s*:\s*d[ée]sactiv[ée])",
    re.I,
)

# Neutralise la réduction de bruit Zoom et force le son d'origine (Original Sound) / stéréo / haute fidélité
# sans altération WebRTC (ni suppression de bruit ANS, ni annulation d'écho AEC, ni contrôle automatique de gain AGC).
ORIGINAL_SOUND_INIT_SCRIPT = r"""
(() => {
  const disable3A = (audio) => {
    if (!audio || typeof audio !== 'object') return;
    audio.echoCancellation = false;
    audio.noiseSuppression = false;
    audio.autoGainControl = false;
    audio.googEchoCancellation = false;
    audio.googAutoGainControl = false;
    audio.googNoiseSuppression = false;
    audio.googHighpassFilter = false;
    audio.googTypingNoiseDetection = false;
    audio.googAudioMirroring = false;
    if (audio.mandatory) {
      audio.mandatory.echoCancellation = false;
      audio.mandatory.noiseSuppression = false;
      audio.mandatory.autoGainControl = false;
      audio.mandatory.googEchoCancellation = false;
      audio.mandatory.googAutoGainControl = false;
      audio.mandatory.googNoiseSuppression = false;
      audio.mandatory.googHighpassFilter = false;
    }
    if (Array.isArray(audio.optional)) {
      audio.optional.forEach(opt => {
        for (const k of ['echoCancellation', 'googEchoCancellation', 'googAutoGainControl', 'googNoiseSuppression', 'googHighpassFilter', 'noiseSuppression', 'autoGainControl']) {
          if (k in opt) opt[k] = false;
        }
      });
    }
    if (Array.isArray(audio.advanced)) {
      audio.advanced.forEach(adv => {
        if ('echoCancellation' in adv) adv.echoCancellation = false;
        if ('noiseSuppression' in adv) adv.noiseSuppression = false;
        if ('autoGainControl' in adv) adv.autoGainControl = false;
      });
    }
  };

  const hookMediaDevices = (target) => {
    if (!target || !target.getUserMedia || target.__hooked_gum) return;
    const origGUM = target.getUserMedia.bind(target);
    target.getUserMedia = async function(constraints) {
      if (constraints) {
        if (constraints.audio === true) constraints.audio = {};
        if (typeof constraints.audio === 'object' && constraints.audio !== null) {
          disable3A(constraints.audio);
        }
      }
      return origGUM(constraints);
    };
    target.__hooked_gum = true;
  };

  if (typeof navigator !== 'undefined') {
    if (navigator.mediaDevices) hookMediaDevices(navigator.mediaDevices);
    const hookLegacy = (obj, name) => {
      if (obj && typeof obj[name] === 'function' && !obj[name].__hooked) {
        const orig = obj[name].bind(obj);
        obj[name] = function(constraints, success, error) {
          if (constraints) {
            if (constraints.audio === true) constraints.audio = {};
            if (typeof constraints.audio === 'object' && constraints.audio !== null) disable3A(constraints.audio);
          }
          return orig(constraints, success, error);
        };
        obj[name].__hooked = true;
      }
    };
    hookLegacy(navigator, 'getUserMedia');
    hookLegacy(navigator, 'webkitGetUserMedia');
    if (typeof Navigator !== 'undefined' && Navigator.prototype) {
      hookLegacy(Navigator.prototype, 'getUserMedia');
      hookLegacy(Navigator.prototype, 'webkitGetUserMedia');
    }
  }

  if (typeof window !== 'undefined' && window.MediaDevices && window.MediaDevices.prototype) {
    hookMediaDevices(window.MediaDevices.prototype);
  }

  if (typeof window !== 'undefined' && window.MediaStreamTrack && window.MediaStreamTrack.prototype) {
    const origApply = window.MediaStreamTrack.prototype.applyConstraints;
    window.MediaStreamTrack.prototype.applyConstraints = function(constraints) {
      if (this.kind === 'audio' && constraints && typeof constraints === 'object') {
        disable3A(constraints);
      }
      return origApply.call(this, constraints);
    };
  }

  try {
    const storeData = {
      audio_profile_type_new: 'originalSound',
      audio_profile_type: 'originalSound',
      denoise_type: 'Browser',
      enable_high_fidelity: true,
      enable_stereo: true,
      isStereoEnabled: true,
      enableHightFidelity: true,
    };
    const b64Data = btoa(JSON.stringify(storeData)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    localStorage.setItem('ZWFzeS1sb2NhbC1zdG9yYWdl', b64Data);
    localStorage.setItem('easy-local-storage', b64Data);
    for (const [k, v] of Object.entries(storeData)) {
      localStorage.setItem(k, typeof v === 'boolean' ? String(v) : v);
    }
  } catch (e) {}

  let _easyStore = typeof window !== 'undefined' ? window.easyStore : undefined;
  const hookEasyStore = (store) => {
    if (!store || typeof store.easyGet !== 'function' || store.__hooked) return store;
    const origEasyGet = store.easyGet.bind(store);
    store.easyGet = function(key) {
      if (key === 'audio_profile_type' || key === 'audio_profile_type_new') return 'originalSound';
      if (key === 'denoise_type') return 'Browser';
      if (key === 'enable_high_fidelity' || key === 'enableHightFidelity' || key === 'enable_stereo' || key === 'isStereoEnabled') return true;
      return origEasyGet(key);
    };
    store.__hooked = true;
    return store;
  };
  if (_easyStore) hookEasyStore(_easyStore);
  try {
    Object.defineProperty(window, 'easyStore', {
      get() { return _easyStore; },
      set(val) { _easyStore = hookEasyStore(val); },
      configurable: true,
      enumerable: true,
    });
  } catch (e) {}

  let _JsMediaSDK_Instance = typeof window !== 'undefined' ? window.JsMediaSDK_Instance : undefined;
  const hookSDK = (sdk) => {
    if (!sdk || sdk.__hooked) return sdk;
    let _util = sdk.util;
    if (_util) _util.isSupportOriginalSound = () => true;
    try {
      Object.defineProperty(sdk, 'util', {
        get() { return _util; },
        set(u) {
          _util = u;
          if (u) u.isSupportOriginalSound = () => true;
        },
        configurable: true,
        enumerable: true,
      });
    } catch (e) {}
    if (sdk.prototype && typeof sdk.prototype.Notify_MeidaSDK === 'function') {
      const origNotify = sdk.prototype.Notify_MeidaSDK;
      sdk.prototype.Notify_MeidaSDK = function(code, data) {
        if (data && data.CaptureAudioInfo) {
          data.CaptureAudioInfo.audioProfile = {
            currentSelect: 'originalSound',
            originalSound: { highfidelity: true, stereo: true },
            backgroundNoiseSuppression: 'Browser',
          };
          data.CaptureAudioInfo.disableNoiseSuppression = true;
          data.CaptureAudioInfo.disableAudioAGC = true;
          data.CaptureAudioInfo.enableOriginalSound = true;
        }
        return origNotify.call(this, code, data);
      };
    }
    sdk.__hooked = true;
    return sdk;
  };
  if (_JsMediaSDK_Instance) hookSDK(_JsMediaSDK_Instance);
  try {
    Object.defineProperty(window, 'JsMediaSDK_Instance', {
      get() { return _JsMediaSDK_Instance; },
      set(val) { _JsMediaSDK_Instance = hookSDK(val); },
      configurable: true,
      enumerable: true,
    });
  } catch (e) {}
})();
"""

# Textes de l'interface Zoom classant l'état de la page. Ils ne sont appliqués qu'aux titres, dialogues et
# alertes (jamais au chat, aux sous-titres ou aux noms des participants), sauf la salle d'attente.
_WAITING = re.compile(r"(waiting for the host|host will let you in|please wait, the meeting host|"
                      r"waiting room|salle d.attente|attendez que l.h[ôo]te|l.h[ôo]te va vous (laisser|admettre))", re.I)
_DENIED = re.compile(
    r"((the )?host has removed you|removed you from (this|the) meeting|"
    r"(you have been|you were|previously) removed|"
    r"your (request|entry) (was |has been )?(denied|declined)|"
    r"l.h[ôo]te vous a retiré|vous avez été retiré|"
    r"précédemment retiré|"
    r"admission (was )?(denied|refus)|demande .{0,20}refus|"
    r"automated bots aren.t allowed|les robots automatisés ne sont pas autorisés|"
    r"sign in to join the meeting|connectez-vous pour rejoindre)",
    re.I,
)
_ENDED = re.compile(r"(meeting has (been )?ended|this meeting is over|"
                    r"(la|cette) réunion (a|est) (été )?(termin|fini)|réunion terminée)", re.I)
_FAILED = re.compile(r"(invalid meeting|meeting id is not valid|meeting (does not|doesn.t) exist|n.existe pas|"
                     r"id de réunion (invalide|incorrect)|unable to join (the )?meeting|meeting link is invalid|"
                     r"lien de réunion est non valide)", re.I)

# Zones susceptibles de porter un message système Zoom.
HEADLINE_SELECTOR = "h1, h2, h3, [role=dialog], [role=alertdialog], [role=alert], .preview-captcha-banner, .zm-message"
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
                 auto_reconnect: bool = True, on_change=None, owner: str | None = None,
                 respawn_on_ban: bool = True, on_banned=None, respawns: int = 0):
        self.id = bot_id or uuid.uuid4().hex[:8]
        self.owner = owner  # identifiant de session GUI ; None = bot d'administration (persisté)
        self.meeting = meeting
        self.name = name
        self.auto_reconnect = auto_reconnect
        self.respawn_on_ban = respawn_on_ban
        self.respawns = respawns
        # created | joining | waiting_room | in_meeting | reconnecting | ended | denied | error | left
        self.status = "created"
        self.error: str | None = None
        self.reconnects = 0
        self.events: deque[dict] = deque(maxlen=50)
        self.mic = VirtualMic(self.id)
        self._on_change = on_change
        self._on_banned = on_banned
        self._banned = False
        self._ban_handled = False
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
        return net.scrub(text)  # ni IP du serveur, ni hôte ou identifiants de proxy dans ce que voit l'utilisateur

    @property
    def is_banned(self) -> bool:
        """Vrai si le bot a été expulsé ou banni par l'hôte de la réunion."""
        if self._banned:
            return True
        if self.status == "denied" and self.error:
            if re.search(r"invalide|introuvable", self.error, re.I):
                return False
            if _DENIED.search(self.error) or re.search(r"expulsé|retiré|banni|refus", self.error, re.I):
                return True
        return False

    def _set_status(self, status: str, error: str | None = None) -> None:
        self.status, self.error = status, self._scrub(error)
        self.events.append(log_event(logger, "status", bot=self.id, meeting=self.meeting.id,
                                     status=status, error=self.error))
        if self._on_change:
            self._on_change()
        if self.is_banned and self._on_banned and not self._closing and not self._ban_handled:
            self._ban_handled = True
            try:
                self._on_banned(self)
            except Exception:
                logger.exception("erreur dans on_banned", extra={"fields": {"bot": self.id}})

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
            "respawn_on_ban": self.respawn_on_ban,
            "respawns": self.respawns,
            "owner": self.owner[:6] if self.owner else None,
        }

    def to_state(self) -> dict:
        return {"id": self.id, "meeting": self.meeting.id, "passcode": self.meeting.passcode,
                "host": self.meeting.host, "name": self.name, "auto_reconnect": self.auto_reconnect,
                "respawn_on_ban": self.respawn_on_ban}

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
                if not re.search(r"invalide|introuvable", str(exc), re.I):
                    self._banned = True
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
                if reason == "denied":
                    self._banned = True
                self._set_status(reason, "réunion terminée" if reason == "ended" else "expulsé ou refusé")
                return
            # Le compteur ne repart de zéro qu'après une session stable : sinon une boucle
            # connexion/coupure immédiate ne s'arrêterait jamais.
            if started is not None and time.monotonic() - started >= STABLE_SESSION_S:
                attempt = 0
                self.respawns = 0
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
        proxy = net.next_proxy()  # lève avant toute ressource si un proxy est exigé et absent
        await self.mic.create()
        lite_args = list(LITE_ARGS)
        if HEADED and "--disable-gpu" in lite_args:
            lite_args.remove("--disable-gpu")
        self._browser = await pw.chromium.launch(
            headless=not HEADED,
            executable_path=CHROMIUM_PATH,
            env=net.clean_env({"PULSE_SOURCE": self.mic.source}),  # sans jeton, code d'accès ni identifiants de proxy
            proxy=proxy,
            chromium_sandbox=SANDBOX,
            args=["--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required",
                  "--disable-blink-features=AutomationControlled",
                  *lite_args, *net.privacy_args(proxy is not None)],
        )
        context = await self._browser.new_context(
            permissions=["microphone"],
            viewport={"width": 1280, "height": 720},
            screen={"width": 1920, "height": 1080},
            locale="en-US",
            color_scheme="light",
            has_touch=False,
            is_mobile=False,
            device_scale_factor=1,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        if _STEALTH:
            await Stealth().apply_stealth_async(context)
        else:
            # Masque navigator.webdriver avant tout script de la page
            await context.add_init_script(
                "Object.defineProperty(Navigator.prototype, 'webdriver', {get: () => undefined, configurable: true});"
            )
        if DISABLE_DENOISE:
            await context.add_init_script(ORIGINAL_SOUND_INIT_SCRIPT)
        try:
            await context.grant_permissions(["microphone"], origin=f"https://{self.meeting.host}")
        except Exception:
            pass
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

        # Vérifie si la page affiche déjà une erreur, un refus ou un lien invalide
        state = await self._page_state(page, include_body=True)
        if state == "denied":
            raise JoinDenied("bloqué par Zoom (détection de bot ou refus d'admission)")
        if state == "failed":
            raise JoinDenied("réunion invalide ou introuvable")
        if state == "ended":
            raise MeetingEnded("la réunion est terminée")

        name_input = page.locator("#input-for-name")
        await name_input.wait_for(timeout=JOIN_TIMEOUT_MS)
        try:
            await name_input.hover()
            await asyncio.sleep(0.1)
            await name_input.click()
            await name_input.fill("")
            await name_input.press_sequentially(self.name, delay=40)
        except Exception:
            await name_input.fill(self.name, timeout=JOIN_TIMEOUT_MS)

        if self.meeting.passcode:
            pwd = page.locator("#input-for-pwd")
            if await pwd.count():
                try:
                    await pwd.hover()
                    await asyncio.sleep(0.1)
                    await pwd.click()
                    await pwd.fill("")
                    await pwd.press_sequentially(self.meeting.passcode, delay=40)
                except Exception:
                    await pwd.fill(self.meeting.passcode)

        await asyncio.sleep(0.4)

        join_btn = page.get_by_role("button", name=JOIN_BUTTON).first
        if await join_btn.count():
            try:
                await join_btn.hover()
                await asyncio.sleep(0.2)
                await join_btn.click(timeout=5000)
            except PlaywrightError as exc:
                current_state = await self._page_state(page, include_body=True)
                if current_state == "denied":
                    raise JoinDenied("bloqué par Zoom (détection de bot ou refus d'admission)") from exc
                if current_state == "failed":
                    raise JoinDenied("réunion invalide ou introuvable") from exc
                raise

    async def _ensure_original_sound(self, page: Page) -> None:
        """Si un bouton 'Original Sound' apparaît dans l'interface, s'assure qu'il est activé."""
        if not DISABLE_DENOISE or page.is_closed():
            return
        try:
            btn = page.get_by_role("button", name=ORIGINAL_SOUND_BUTTON).first
            if await btn.count():
                await btn.click(timeout=1000)
        except Exception:
            pass

    async def _await_admission(self, page: Page) -> None:
        """Distingue salle d'attente, refus, fin de réunion et entrée réelle."""
        deadline = time.monotonic() + ADMIT_TIMEOUT_S
        while time.monotonic() < deadline:
            audio = page.get_by_role("button", name=JOIN_AUDIO)
            if await audio.count():
                await audio.first.click()
                await self._ensure_original_sound(page)
                return
            state = await self._page_state(page, include_body=True)
            if state == "in_meeting":
                await self._ensure_original_sound(page)
                return
            if state == "waiting" and self.status != "waiting_room":
                self._set_status("waiting_room")
            elif state == "failed":
                raise JoinDenied("réunion invalide ou introuvable")
            elif state == "denied":
                text = await self._headline_text(page)
                if re.search(r"automated bots|robots automatisés|sign in to join", text, re.I):
                    raise JoinDenied("bloqué par Zoom (détection de bot)")
                raise JoinDenied("admission refusée ou expulsé par l'hôte")
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
        (SCREENSHOT_DIR / f"{self.id}-error.png").unlink(missing_ok=True)  # contenu de réunion : pas conservé
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
                await asyncio.wait_for(browser.close(), 15)  # un navigateur figé ne bloque pas le nettoyage
            except Exception:  # noqa: BLE001 - navigateur déjà mort ou figé
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
            try:
                return await self._page.screenshot()
            except PlaywrightError as exc:  # page fermée pendant la capture
                raise RuntimeError("capture impossible : la page vient de se fermer") from exc
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
            raise RuntimeError("lecture interrompue ou en échec (fichier illisible ?)")

    def stop_sounds(self) -> None:
        self.mic.stop()

    def _require_page(self) -> Page:
        if not self._page or self.status != "in_meeting":
            raise RuntimeError("le bot n'est pas dans la réunion")
        return self._page
