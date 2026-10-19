"""Sessions utilisateur du GUI web : chaque onglet a ses bots, ses sons et ses actions, isolés des autres.

Une session vit tant que son onglet est connecté (WebSocket). Quand la connexion disparaît plus de
`GRACE_S` secondes, la session est détruite : bots retirés, actions annulées, fichiers supprimés.
Un rechargement de page (reconnexion dans le délai de grâce) reprend la même session."""

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import signal
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from . import net
from .bot import Bot
from .log import DATA_DIR
from . import manager as manager_module
from .manager import BotManager, list_sounds, sound_path
from .meeting import parse_meeting

logger = logging.getLogger("zoombots.sessions")

ROOT = Path(DATA_DIR) / "sessions"
GRACE_S = float(os.environ.get("ZOOMBOTS_SESSION_GRACE", "15"))
NEVER_CONNECTED_S = 30.0  # session créée mais jamais attachée à un WebSocket
LOCAL = os.environ.get("ZOOMBOTS_MODE") == "local"  # GUI lancé chez soi : pas de plafonds par adresse IP
MAX_SESSIONS = int(os.environ.get("ZOOMBOTS_MAX_SESSIONS", "20"))
# Plafonds par adresse IP cliente (0 = illimité). Contre un utilisateur qui ouvrirait beaucoup d'onglets.
IP_MAX_SESSIONS = int(os.environ.get("ZOOMBOTS_IP_MAX_SESSIONS", "0" if LOCAL else "3"))
IP_MAX_BOTS = int(os.environ.get("ZOOMBOTS_IP_MAX_BOTS", "0" if LOCAL else "10"))
IP_SESSIONS_PER_MINUTE = int(os.environ.get("ZOOMBOTS_IP_SESSIONS_PER_MINUTE", "0" if LOCAL else "10"))
SESSION_MAX_BOTS = int(os.environ.get("ZOOMBOTS_SESSION_MAX_BOTS", "10"))
MAX_UPLOAD_BYTES = int(float(os.environ.get("ZOOMBOTS_MAX_UPLOAD_MB", "25")) * 1024 * 1024)
MAX_SOUNDS = int(os.environ.get("ZOOMBOTS_SESSION_MAX_SOUNDS", "20"))
MAX_SOUND_SECONDS = int(os.environ.get("ZOOMBOTS_MAX_SOUND_SECONDS", "900"))
YT_TIMEOUT_S = int(os.environ.get("ZOOMBOTS_YT_TIMEOUT", "180"))
JOB_ERROR_TTL_S = 20
DESTROY_TIMEOUT_S = 60
MAX_LINKS = 20
MAX_DEPLOY = 50
MAX_ACTION_COUNT = 1000
MAX_PARALLEL_UPLOADS = 2
UPLOAD_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus", ".aac", ".mp4", ".webm", ".mkv"}
YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}


def mgr_max() -> int:
    return manager_module.MAX_BOTS


class SessionError(Exception):
    """Erreur attribuable à l'utilisateur : le code HTTP est porté par `status`."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass
class Sound:
    id: str
    name: str
    path: Path
    duration: float
    size: int
    source: str  # upload | youtube

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "duration": round(self.duration, 1),
                "size": self.size, "source": self.source}


@dataclass
class Action:
    bot_id: str
    sound: str
    total: int
    sound_id: str = ""
    done: int = 0
    status: str = "pending"  # pending | running | done | stopped | error
    error: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {"sound": self.sound, "total": self.total, "done": self.done, "status": self.status, "error": self.error}


def distribute(links: int, count: int, mode: str) -> list[int]:
    """Nombre de bots par lien. `split` : `count` bots au total répartis équitablement ; `each` : `count` par lien."""
    if mode == "each":
        return [count] * links
    base, rest = divmod(count, links)
    return [base + (1 if i < rest else 0) for i in range(links)]


def clean_name(raw: str) -> str:
    name = re.sub(r"[\x00-\x1f\x7f/\\]", "", Path(raw or "son").name).strip()
    return name[:80] or "son"


def _kill_group(proc) -> None:
    """Tue le processus et ses enfants (yt-dlp lance ffmpeg) : `proc.kill()` n'atteint que le premier."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


async def _run(*args: str, timeout: float = 30, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True,
        env=net.clean_env(env))
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        _kill_group(proc)
        await proc.wait()
        raise SessionError("délai dépassé") from None
    except asyncio.CancelledError:
        _kill_group(proc)
        await asyncio.shield(proc.wait())
        raise
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


async def probe_audio(path: Path) -> float:
    """Durée en secondes d'un fichier audio ou vidéo décodable ; SessionError sinon."""
    code, out, _ = await _run("ffprobe", "-v", "error", "-protocol_whitelist", "file", "-show_entries", "format=duration:stream=codec_type",
                              "-of", "json", str(path))
    try:
        info = json.loads(out)
        duration = float(info["format"]["duration"])
        has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    except (ValueError, KeyError, TypeError):
        raise SessionError("fichier audio illisible ou sans piste audio") from None
    if code != 0 or not has_audio or duration <= 0:
        raise SessionError("fichier audio illisible ou sans piste audio")
    if duration > MAX_SOUND_SECONDS:
        raise SessionError(f"son trop long (max {MAX_SOUND_SECONDS} s)")
    return duration


def youtube_url(raw: str) -> str:
    """URL YouTube normalisée ; toute autre destination est refusée (pas de SSRF via yt-dlp)."""
    parsed = urlparse(raw.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or host not in YT_HOSTS:
        raise SessionError("seuls les liens YouTube sont acceptés")
    path = parsed.path
    # une vidéo, jamais une playlist, une chaîne ou une page d'information (qui redirige vers un autre hôte)
    video = (host == "youtu.be" and len(path.strip("/")) > 0 and "/" not in path.strip("/")) \
        or path == "/watch" or path.startswith(("/shorts/", "/live/", "/embed/"))
    if not video:
        raise SessionError("lien de vidéo YouTube attendu (watch?v=…, youtu.be/…, shorts/…), pas une playlist ni une chaîne")
    return parsed.geturl()


def ytdlp_command() -> list[str]:
    return [sys.executable, "-m", "yt_dlp"]


class Session:
    def __init__(self, sid: str, manager: BotManager, ip: str = "", registry: "SessionManager | None" = None):
        self.id = sid
        self.manager = manager
        self.ip = ip
        self.registry = registry
        self.created = time.monotonic()
        self.connections = 0
        self.disconnected_at: float | None = None
        self.sounds: dict[str, Sound] = {}
        self.actions: dict[str, Action] = {}
        self.jobs: dict[str, dict] = {}  # téléchargements YouTube
        self.dir = ROOT / sid[:16]
        self._closed = False
        self._uploads = 0  # envois en cours (comptés dans le plafond de sons dès leur début)
        self._probe = asyncio.Semaphore(2)  # ffprobe : au plus 2 à la fois par session
        self._destroyed = asyncio.Event()

    # -- présence -----------------------------------------------------------

    def attach(self) -> None:
        self.connections += 1
        self.disconnected_at = None

    def detach(self) -> None:
        self.connections = max(0, self.connections - 1)
        if self.connections == 0:
            self.disconnected_at = time.monotonic()

    def expired(self, now: float) -> bool:
        if self.connections:
            return False
        if self.disconnected_at is not None:
            return now - self.disconnected_at > GRACE_S
        return now - self.created > max(GRACE_S, NEVER_CONNECTED_S)

    # -- bots ---------------------------------------------------------------

    @property
    def bots(self) -> list[Bot]:
        return self.manager.bots_of(self.id)

    def bot(self, bot_id: str) -> Bot:
        for b in self.bots:
            if b.id == bot_id:
                return b
        raise SessionError(f"bot {bot_id} inconnu", 404)

    def deploy(self, links: list[dict], count: int, mode: str, name: str, auto_reconnect: bool,
               respawn_on_ban: bool = True) -> list[Bot]:
        if self._closed:
            raise SessionError("session terminée", 410)
        if not 1 <= len(links) <= MAX_LINKS:
            raise SessionError(f"entre 1 et {MAX_LINKS} liens")
        if mode not in ("split", "each"):
            raise SessionError("mode inconnu (split | each)")
        meetings = []
        for i, entry in enumerate(links, 1):
            try:
                meetings.append(parse_meeting(entry["meeting"], entry.get("passcode") or None))
            except ValueError as exc:
                raise SessionError(f"lien {i} : {exc}") from exc
        per_link = distribute(len(meetings), count, mode)
        total = sum(per_link)
        if not 1 <= total <= MAX_DEPLOY:
            raise SessionError(f"entre 1 et {MAX_DEPLOY} bots par déploiement (demandé : {total})")
        active = [b for b in self.bots if not b.finished]
        if len(active) + total > SESSION_MAX_BOTS:
            raise SessionError(f"limite de {SESSION_MAX_BOTS} bots par session ({len(active)} actifs)", 429)
        if IP_MAX_BOTS and self.registry:
            used = self.registry.active_bots(self.ip)
            if used + total > IP_MAX_BOTS:
                raise SessionError(f"limite de {IP_MAX_BOTS} bots par adresse IP ({used} actifs)", 429)
        if total > self.manager.capacity_left():
            raise SessionError(f"le serveur est plein : {self.manager.capacity_left()} place(s) libre(s) "
                                f"sur {mgr_max()} (ZOOMBOTS_MAX_BOTS)", 429)
        created: list[Bot] = []
        index = 0
        try:
            for meeting, n in zip(meetings, per_link):
                if not n:
                    continue
                names = [name if total == 1 else f"{name} {index + k + 1}" for k in range(n)]
                index += n
                created += self.manager.spawn(meeting, names, auto_reconnect, owner=self.id, respawn_on_ban=respawn_on_ban)
        except OverflowError as exc:
            raise SessionError(str(exc), 429) from exc
        return created

    async def remove_bot(self, bot_id: str) -> None:
        self.bot(bot_id)
        self._cancel_action(bot_id)
        await self.manager.remove(bot_id)
        self.actions.pop(bot_id, None)

    async def remove_bots(self, finished_only: bool = False) -> int:
        targets = [b.id for b in self.bots if b.finished or not finished_only]
        results = await asyncio.gather(*(self.remove_bot(i) for i in targets), return_exceptions=True)
        return sum(1 for r in results if not isinstance(r, Exception))

    # -- sons ---------------------------------------------------------------

    def _check_sound_slot(self) -> None:
        pending = sum(1 for j in self.jobs.values() if j["status"] == "downloading")
        if len(self.sounds) + pending + self._uploads >= MAX_SOUNDS:
            raise SessionError(f"limite de {MAX_SOUNDS} sons par session", 429)

    async def add_upload(self, filename: str, chunks) -> Sound:
        """`chunks` : itérateur asynchrone de bytes (corps de la requête), limité à MAX_UPLOAD_BYTES."""
        if self._closed:
            raise SessionError("session terminée", 410)
        self._check_sound_slot()
        if self._uploads >= MAX_PARALLEL_UPLOADS:
            raise SessionError(f"{MAX_PARALLEL_UPLOADS} envois simultanés maximum", 429)
        name = clean_name(filename)
        ext = Path(name).suffix.lower()
        if ext not in UPLOAD_EXTS:
            raise SessionError(f"format non géré ({ext or 'sans extension'})")
        self._uploads += 1  # place réservée tout de suite (aucun await entre la vérification et ici)
        self.dir.mkdir(parents=True, exist_ok=True)
        sid = "s" + uuid.uuid4().hex[:8]
        path = self.dir / f"{sid}{ext}"
        size = 0
        try:
            with open(path, "wb") as fh:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise SessionError(f"fichier trop gros (max {MAX_UPLOAD_BYTES // 2**20} Mo)", 413)
                    await asyncio.to_thread(fh.write, chunk)  # un disque lent ne fige pas la boucle
            async with self._probe:
                duration = await probe_audio(path)
            if self._closed:
                raise SessionError("session terminée", 410)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            self._uploads -= 1
        sound = Sound(sid, name, path, duration, size, "upload")
        self.sounds[sid] = sound
        return sound

    def start_youtube(self, raw_url: str) -> str:
        if self._closed:
            raise SessionError("session terminée", 410)
        url = youtube_url(raw_url)
        self._check_sound_slot()
        if sum(1 for j in self.jobs.values() if j["status"] == "downloading") >= 2:
            raise SessionError("2 téléchargements simultanés maximum", 429)
        job_id = "y" + uuid.uuid4().hex[:8]
        self.jobs[job_id] = {"id": job_id, "url": url, "status": "downloading", "error": None, "title": None}
        self.jobs[job_id]["task"] = asyncio.create_task(self._download(job_id, url))
        return job_id

    async def _download(self, job_id: str, url: str) -> None:
        job = self.jobs[job_id]
        self.dir.mkdir(parents=True, exist_ok=True)
        template = self.dir / f"{job_id}.%(ext)s"
        try:
            try:
                proxy_env = net.download_proxy_env()
            except RuntimeError as exc:
                raise SessionError(str(exc)) from exc
            code, out, err = await _run(
                *ytdlp_command(), "--ignore-config", "--no-playlist", "--playlist-items", "1", "--use-extractors", "youtube",
                "--no-simulate", "-x", "--audio-format", "mp3",
                "--max-filesize", f"{MAX_UPLOAD_BYTES}", "--match-filter", f"duration<={MAX_SOUND_SECONDS}",
                "--no-progress", "--no-warnings", "--print", "after_move:%(title)s\x1f%(filepath)s",
                "-o", str(template), "--", url, timeout=YT_TIMEOUT_S, env=proxy_env)
            lines = [line for line in out.splitlines() if "\x1f" in line]
            if code != 0 or not lines:
                reason = net.scrub((err.strip().splitlines() or ["échec du téléchargement"])[-1][:200])
                if "does not pass filter" in err or "longer than" in err:
                    reason = f"vidéo trop longue (max {MAX_SOUND_SECONDS} s)"
                raise SessionError(reason)
            title, filepath = lines[-1].split("\x1f", 1)
            path = Path(filepath)
            if path.parent.resolve() != self.dir.resolve() or not path.is_file():
                raise SessionError("fichier téléchargé introuvable")
            if path.stat().st_size > MAX_UPLOAD_BYTES:
                path.unlink(missing_ok=True)
                raise SessionError(f"fichier trop gros (max {MAX_UPLOAD_BYTES // 2**20} Mo)")
            try:
                async with self._probe:
                    duration = await probe_audio(path)
            except SessionError:
                path.unlink(missing_ok=True)
                raise
            if self._closed:
                path.unlink(missing_ok=True)
                return
            sound = Sound(job_id, clean_name(title) + ".mp3", path, duration, path.stat().st_size, "youtube")
            self.sounds[job_id] = sound
            job.update(status="ready", title=sound.name, done_at=time.monotonic())
        except asyncio.CancelledError:
            job.update(status="error", done_at=time.monotonic())
            self._remove_partials(job_id)
            raise
        except SessionError as exc:
            job.update(status="error", error=str(exc), done_at=time.monotonic())
            self._remove_partials(job_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("youtube failed", extra={"fields": {"error": str(exc)}})
            job.update(status="error", error="échec du téléchargement", done_at=time.monotonic())

    def _remove_partials(self, job_id: str) -> None:
        for leftover in self.dir.glob(f"{job_id}.*"):
            leftover.unlink(missing_ok=True)

    def delete_sound(self, sound_id: str) -> None:
        sound = self.sounds.pop(sound_id, None)
        if not sound:
            raise SessionError("son inconnu", 404)
        for action in self.actions.values():  # une action en cours utilise le fichier : on l'arrête
            if action.sound_id == sound_id and action.status in ("pending", "running"):
                self._cancel_action(action.bot_id)
        sound.path.unlink(missing_ok=True)

    def resolve_sound(self, ref: str) -> tuple[str, Path]:
        """`ref` : id d'un son de la session, ou `lib:<nom>` pour la bibliothèque du serveur."""
        if ref.startswith("lib:"):
            try:
                return ref[4:], sound_path(ref[4:])
            except FileNotFoundError:
                raise SessionError("son de la bibliothèque introuvable", 404) from None
        sound = self.sounds.get(ref)
        if not sound:
            raise SessionError("son inconnu", 404)
        return sound.name, sound.path

    # -- actions ------------------------------------------------------------

    def run_action(self, bot_ids: list[str] | None, sound_ref: str, count: int, gap: float,
                   volume: float, stagger: float) -> list[str]:
        """Joue `count` fois un son sur chaque bot, chaque lecture attendant la fin de la précédente."""
        if self._closed:
            raise SessionError("session terminée", 410)
        if not 1 <= count <= MAX_ACTION_COUNT:
            raise SessionError(f"répétitions : entre 1 et {MAX_ACTION_COUNT}")
        name, path = self.resolve_sound(sound_ref)
        bots = [self.bot(i) for i in bot_ids] if bot_ids is not None else [b for b in self.bots if not b.finished]
        bots = [b for b in bots if not b.finished]
        if not bots:
            raise SessionError("aucun bot actif ciblé")
        for index, bot in enumerate(bots):
            self._cancel_action(bot.id)
            action = Action(bot.id, name, count, sound_ref)
            self.actions[bot.id] = action
            action.task = asyncio.create_task(self._run_action(bot, action, path, volume, gap, index * stagger))
        return [b.id for b in bots]

    async def _wait_in_meeting(self, bot: Bot, timeout: float = 900) -> None:
        deadline = time.monotonic() + timeout
        while bot.status != "in_meeting":
            if bot.finished:
                raise RuntimeError("le bot a quitté la réunion")
            if time.monotonic() > deadline:
                raise RuntimeError("le bot n'a pas rejoint la réunion à temps")
            await asyncio.sleep(0.5)

    async def _run_action(self, bot: Bot, action: Action, path: Path, volume: float, gap: float, delay: float) -> None:
        action.status = "running"
        failures = 0
        try:
            if delay:
                await asyncio.sleep(delay)
            while action.done < action.total:
                await self._wait_in_meeting(bot)
                try:
                    await bot.play_wait(path, volume)
                except RuntimeError as exc:
                    if bot.status != "in_meeting":
                        # reconnexion en cours : on attend que le bot revienne (ou qu'il soit terminé), sans
                        # consommer le budget d'échecs — c'est la même lecture qui reprend
                        await asyncio.sleep(1)
                        continue
                    failures += 1  # micro indisponible alors que le bot est dans la réunion
                    if failures >= 3:
                        raise RuntimeError(str(exc)) from exc
                    await asyncio.sleep(1)
                    continue
                failures = 0
                action.done += 1
                if gap and action.done < action.total:
                    await asyncio.sleep(gap)
            action.status = "done"
        except asyncio.CancelledError:
            action.status = "stopped"
            raise
        except Exception as exc:  # noqa: BLE001
            action.status, action.error = "error", net.scrub(str(exc))

    def _cancel_action(self, bot_id: str) -> None:
        action = self.actions.get(bot_id)
        if action and action.task and not action.task.done():
            action.task.cancel()
            action.status = "stopped"
        for b in self.manager.bots_of(self.id):
            if b.id == bot_id:
                b.stop_sounds()

    def stop_actions(self, bot_ids: list[str] | None = None) -> int:
        targets = bot_ids if bot_ids is not None else [b.id for b in self.bots]
        for bot_id in targets:
            self.bot(bot_id)
        for bot_id in targets:
            self._cancel_action(bot_id)
        return len(targets)

    # -- cycle de vie -------------------------------------------------------

    async def destroy(self) -> None:
        if self._closed:
            await self._destroyed.wait()  # un second appel (DELETE pendant l'expiration) attend la fin réelle du nettoyage
            return
        self._closed = True
        tasks = [a.task for a in self.actions.values() if a.task] + [j["task"] for j in self.jobs.values() if j.get("task")]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*(self.manager.remove(b.id) for b in self.bots), return_exceptions=True)
        shutil.rmtree(self.dir, ignore_errors=True)
        self._destroyed.set()
        logger.info("session détruite", extra={"fields": {"session": self.id[:6]}})

    def _visible_jobs(self) -> list[dict]:
        now = time.monotonic()
        for job_id in [i for i, j in self.jobs.items() if j["status"] == "ready"
                       or (j["status"] == "error" and now - j.get("done_at", now) > JOB_ERROR_TTL_S)]:
            del self.jobs[job_id]
        return list(self.jobs.values())

    def snapshot(self) -> dict:
        bots = []
        for b in self.bots:
            d = b.to_dict()
            d["link"] = f"{b.meeting.host}/{b.meeting.id}"
            action = self.actions.get(b.id)
            d["action"] = action.to_dict() if action else None
            bots.append(d)
        return {
            "bots": bots,
            "sounds": [s.to_dict() for s in self.sounds.values()],
            "library": list_sounds(),
            # téléchargements en cours, et erreurs récentes (une erreur reste affichée JOB_ERROR_TTL_S secondes)
            "jobs": [{k: v for k, v in j.items() if k not in ("task", "done_at")} for j in self._visible_jobs()],
        }


class SessionManager:
    def __init__(self, manager: BotManager):
        self.manager = manager
        self.sessions: dict[str, Session] = {}
        self._reaper: asyncio.Task | None = None
        self._creations: dict[str, deque] = {}  # horodatages de création de sessions, par IP
        self._destroying: set[asyncio.Task] = set()

    async def start(self) -> None:
        shutil.rmtree(ROOT, ignore_errors=True)  # fichiers d'une exécution précédente
        self._reaper = asyncio.create_task(self._reap_loop())

    async def stop(self) -> None:
        if self._reaper:
            self._reaper.cancel()
            await asyncio.gather(self._reaper, return_exceptions=True)
        await asyncio.gather(*self._destroying, return_exceptions=True)
        await asyncio.gather(*(s.destroy() for s in list(self.sessions.values())), return_exceptions=True)
        self.sessions.clear()

    def active_bots(self, ip: str) -> int:
        return sum(1 for s in self.sessions.values() if s.ip == ip for b in s.bots if not b.finished)

    def _check_ip(self, ip: str) -> None:
        if IP_MAX_SESSIONS and sum(1 for s in self.sessions.values() if s.ip == ip) >= IP_MAX_SESSIONS:
            raise SessionError(f"limite de {IP_MAX_SESSIONS} sessions par adresse IP", 429)
        if IP_SESSIONS_PER_MINUTE:
            now = time.monotonic()
            recent = self._creations.setdefault(ip, deque())
            while recent and now - recent[0] > 60:
                recent.popleft()
            if len(recent) >= IP_SESSIONS_PER_MINUTE:
                raise SessionError("trop de sessions créées, réessaie dans une minute", 429)
            recent.append(now)
        stale = time.monotonic() - 60
        for key in [k for k, q in self._creations.items() if not q or q[-1] < stale]:  # la table ne grossit pas
            del self._creations[key]

    def create(self, ip: str = "") -> Session:
        if len(self.sessions) >= MAX_SESSIONS:
            raise SessionError(f"serveur complet ({MAX_SESSIONS} sessions)", 503)
        self._check_ip(ip)
        sid = secrets.token_urlsafe(24)
        self.sessions[sid] = Session(sid, self.manager, ip, self)
        return self.sessions[sid]

    def get(self, sid: str | None) -> Session:
        session = self.sessions.get(sid or "")
        if not session:
            raise SessionError("session inconnue ou expirée", 401)
        return session

    async def close(self, sid: str) -> None:
        session = self.sessions.pop(sid, None)
        if session:
            await session.destroy()

    async def _reap_loop(self) -> None:
        """Détruit les sessions expirées. Chaque destruction est une tâche à part, bornée dans le temps : une
        session dont le nettoyage se fige, ou lève, ne retarde ni ne tue le nettoyage des autres."""
        while True:
            await asyncio.sleep(min(2.0, max(0.05, GRACE_S / 4)))
            now = time.monotonic()
            for sid, session in list(self.sessions.items()):
                if session.expired(now):
                    del self.sessions[sid]
                    task = asyncio.create_task(self._destroy_safely(session))
                    self._destroying.add(task)
                    task.add_done_callback(self._destroying.discard)

    async def _destroy_safely(self, session: Session) -> None:
        try:
            await asyncio.wait_for(session.destroy(), DESTROY_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.error("destruction de session trop longue", extra={"fields": {"session": session.id[:6]}})
        except Exception:  # noqa: BLE001
            logger.exception("échec de destruction de session")
