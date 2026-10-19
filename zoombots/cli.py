"""Interface en ligne de commande : pilote le serveur zoombots via son API HTTP."""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import deploy, net
from .log import DATA_DIR

HOST = os.environ.get("ZOOMBOTS_HOST", "127.0.0.1")
PORT = int(os.environ.get("ZOOMBOTS_PORT", "8000"))
BASE = os.environ.get("ZOOMBOTS_URL") or f"http://{'127.0.0.1' if HOST == '0.0.0.0' else ('[%s]' % HOST if ':' in HOST else HOST)}:{PORT}"
PID_FILE = Path(DATA_DIR) / "server.pid"
LOG_FILE = Path(DATA_DIR) / "server.log"
RUN_FILE = Path(DATA_DIR) / "run.json"  # mode, hôte et port du serveur lancé par la CLI
CADDY_PID = Path(DATA_DIR) / "caddy.pid"
CADDY_LOG = Path(DATA_DIR) / "caddy.log"
CADDYFILE = Path(DATA_DIR) / "Caddyfile"


TOKEN = os.environ.get("ZOOMBOTS_TOKEN")
LOG_ROTATE_BYTES = 5 * 1024 * 1024
# Pas de proxy pour l'API : http_proxy ne doit pas intercepter les appels vers localhost.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class CliError(Exception):
    pass


def _detail(raw: bytes, fallback: str) -> str:
    try:
        detail = json.loads(raw).get("detail", fallback)
    except (ValueError, AttributeError):
        return fallback
    if isinstance(detail, list):
        return "; ".join(str(e.get("msg", e)) if isinstance(e, dict) else str(e) for e in detail)
    return str(detail)


def call(method: str, path: str, body: dict | None = None, raw: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"X-Zoombots": "1"}
    if data is not None:
        headers["content-type"] = "application/json"
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with _OPENER.open(req, timeout=30) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        raise CliError(f"{exc.code}: {_detail(exc.read(), str(exc.reason))}") from None
    except urllib.error.URLError:
        raise CliError(f"serveur injoignable sur {BASE} (lancer `zoombots start`)") from None
    return payload if raw else json.loads(payload)


def resolve(prefix: str) -> str:
    """Accepte un préfixe d'identifiant de bot."""
    if not prefix:
        raise CliError("identifiant de bot vide")
    ids = [b["id"] for b in call("GET", "/bots")]
    matches = [i for i in ids if i.startswith(prefix)]
    if len(matches) != 1:
        raise CliError(f"bot {prefix!r} " + ("introuvable" if not matches else "ambigu"))
    return matches[0]


def table(rows: list[dict], cols: list[str]) -> None:
    if not rows:
        print("(aucun)")
        return
    rows = [{k: (str(v).splitlines()[0][:100] if isinstance(v, str) and v else v) for k, v in r.items()} for r in rows]
    widths = [max(len(c), *(len(str(r.get(c) or "")) for r in rows)) for c in cols]
    print("  ".join(c.upper().ljust(w) for c, w in zip(cols, widths)))
    for r in rows:
        print("  ".join(str(r.get(c) or "").ljust(w) for c, w in zip(cols, widths)))


# -- gestion du serveur -------------------------------------------------------

def _private_dir() -> None:
    """Dossier de données en 0700 (journaux, captures, jeton local)."""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(PID_FILE.parent, 0o700)
    except OSError:
        pass


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(path, 0o600)


def _is_ours(pid: int) -> bool:
    """Vrai si le PID est bien un serveur zoombots (un PID recyclé ne doit jamais être signalé)."""
    try:
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    # `python -m zoombots`, ou l'exécutable `…/zoombots` : pas un éditeur ouvert sur zoombots/cli.py
    return b"zoombots" in args or any(a.endswith(b"/zoombots") for a in args)


def _pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return None
    return pid if _is_ours(pid) else None


def _require_safe_bind() -> None:
    if HOST not in ("127.0.0.1", "localhost", "::1") and not TOKEN:
        raise CliError("ZOOMBOTS_HOST expose l'API : définir ZOOMBOTS_TOKEN")


def ensure_pulseaudio() -> None:
    if not shutil.which("pactl"):
        raise CliError("PulseAudio est requis (relancer install.sh)")
    if subprocess.run(["pactl", "info"], capture_output=True).returncode == 0:
        return
    if not shutil.which("pulseaudio"):
        raise CliError("aucun serveur PulseAudio actif et `pulseaudio` introuvable")
    subprocess.run(["pulseaudio", "--start", "--exit-idle-time=-1"], capture_output=True)
    for _ in range(20):
        if subprocess.run(["pactl", "info"], capture_output=True).returncode == 0:
            return
        time.sleep(0.25)
    raise CliError("impossible de démarrer PulseAudio")


def _rotate_log() -> None:
    try:
        if LOG_FILE.stat().st_size > LOG_ROTATE_BYTES:
            LOG_FILE.replace(LOG_FILE.with_suffix(".log.1"))
    except FileNotFoundError:
        pass


def cmd_serve(args) -> int:
    _require_safe_bind()
    ensure_pulseaudio()
    if _pid():
        raise CliError(f"déjà lancé (pid {_pid()})")
    _private_dir()
    RUN_FILE.unlink(missing_ok=True)  # `serve` ne fournit pas run.json : ne pas garder celui d'un lancement précédent
    _write_private(PID_FILE, str(os.getpid()))
    try:
        from .__main__ import main as serve

        serve()
    finally:
        PID_FILE.unlink(missing_ok=True)
    return 0


def _start_server(host: str, port: int, extra_env: dict[str, str], headed: bool = False) -> int:
    """Lance le serveur en arrière-plan et attend qu'il réponde. Renvoie son PID."""
    global BASE, TOKEN
    if _pid():
        raise CliError(f"déjà lancé (pid {_pid()}) : `zoombots shutdown` pour l'arrêter")
    ensure_pulseaudio()
    _private_dir()
    _rotate_log()
    cmd = [sys.executable, "-m", "zoombots"]
    if headed:
        if not shutil.which("xvfb-run"):
            raise CliError("--headed nécessite xvfb-run")
        cmd = ["xvfb-run", "-a", *cmd]
    env = {**os.environ, "ZOOMBOTS_HEADED": "1" if headed else os.environ.get("ZOOMBOTS_HEADED", ""), **extra_env,
           "ZOOMBOTS_HOST": host, "ZOOMBOTS_PORT": str(port)}
    log_fd = os.open(LOG_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        proc = subprocess.Popen(cmd, stdout=log_fd, stderr=log_fd, start_new_session=True, env=env)
    finally:
        os.close(log_fd)
    _write_private(PID_FILE, str(proc.pid))
    base = f"http://{_url_host(host)}:{port}"
    for _ in range(60):
        if proc.poll() is not None:
            PID_FILE.unlink(missing_ok=True)
            raise CliError(f"le serveur s'est arrêté au démarrage (port {port} déjà pris ?), voir {LOG_FILE}")
        try:
            _probe(base)
            break
        except CliError as exc:
            if "serveur injoignable" not in str(exc):
                raise
            time.sleep(0.5)
    else:
        raise CliError(f"le serveur ne répond pas, voir {LOG_FILE}")
    if proc.poll() is not None:  # un autre service occupait le port et a répondu à notre place
        PID_FILE.unlink(missing_ok=True)
        raise CliError(f"le port {port} est déjà utilisé par un autre service, voir {LOG_FILE}")
    token = extra_env.get("ZOOMBOTS_TOKEN") or None
    info = {"mode": extra_env.get("ZOOMBOTS_MODE", "admin"), "host": host, "port": port}
    if token and info["mode"] == "local":
        info["token"] = token  # relu par la CLI (fichier 0600) ; en mode serveur, le jeton est dans server.json
    _write_private(RUN_FILE, json.dumps(info))
    BASE, TOKEN = base, token or TOKEN
    return proc.pid


def _url_host(host: str) -> str:
    if host == "0.0.0.0":
        return "127.0.0.1"
    return f"[{host}]" if ":" in host else host


def _probe(base: str) -> None:
    """Le serveur répond-il ? (sonde publique, sans jeton)"""
    try:
        with _OPENER.open(urllib.request.Request(base + "/health"), timeout=5):
            return
    except urllib.error.HTTPError:
        return  # il répond, même si c'est par un refus
    except urllib.error.URLError:
        raise CliError(f"serveur injoignable sur {base}") from None


def cmd_start(args) -> int:
    _require_safe_bind()
    pid = _start_server(HOST, PORT, {}, args.headed)
    print(f"zoombots lancé (pid {pid}) : {BASE}")
    return 0


def _open_browser(url: str) -> None:
    import webbrowser

    try:
        if not webbrowser.open(url):
            print(f"Ouvre {url} dans ton navigateur.")
    except webbrowser.Error:
        print(f"Ouvre {url} dans ton navigateur.")


def cmd_gui(args) -> int:
    """Interface web sur cette machine : rien n'est exposé sur Internet, aucun nom de domaine requis."""
    info = _run_info()
    if _pid() and info.get("mode") == "local":
        port = info.get("port", PORT)
        if (args.port and args.port != port) or args.proxy:
            print("L'interface tourne déjà : `zoombots shutdown` d'abord pour changer de port ou de proxy.", file=sys.stderr)
        print(f"L'interface tourne déjà : http://127.0.0.1:{port}/")
        url = f"http://127.0.0.1:{port}/"
    else:
        port = args.port or PORT
        url = f"http://127.0.0.1:{port}/"
        cfg_proxy = args.proxy or os.environ.get("ZOOMBOTS_PROXY")
        if cfg_proxy:
            try:
                for part in cfg_proxy.split(","):
                    net.parse_proxy(part)
            except ValueError as exc:
                raise CliError(str(exc)) from exc
        pid = _start_server("127.0.0.1", port, deploy.local_env(port, cfg_proxy), args.headed)
        print(f"Interface locale lancée (pid {pid}) : {url}")
        print("Fermer l'onglet retire ses bots. `zoombots shutdown` arrête tout.")
        if not cfg_proxy:
            print("Astuce : sans proxy, Zoom voit l'adresse IP de cette machine (voir `zoombots check-ip`).")
    if not args.no_open:
        _open_browser(url)
    return 0


def _run_info() -> dict:
    """Mode, hôte et port du serveur lancé par la CLI ; vide si le fichier manque, est invalide ou périmé."""
    try:
        info = json.loads(RUN_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}
    if not isinstance(info, dict) or not {"host", "port"} <= info.keys() or not _pid():
        return {}  # serveur arrêté ou planté : ne pas viser un ancien port
    return info


# -- mode serveur public ----------------------------------------------------

def _caddy_pid() -> int | None:
    try:
        pid = int(CADDY_PID.read_text())
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except (FileNotFoundError, ValueError, OSError):
        return None
    return pid if any(a == b"caddy" or a.endswith(b"/caddy") for a in args) else None


def _start_caddy(cfg: dict) -> None:
    caddy = deploy.find_caddy()
    if not caddy:
        raise CliError("Caddy est introuvable (il fournit le HTTPS automatique) : relancer `./install.sh --server`")
    if _caddy_pid():
        raise CliError(f"Caddy tourne déjà (pid {_caddy_pid()}) : `zoombots shutdown` d'abord")
    _private_dir()
    _write_private(CADDYFILE, deploy.caddyfile(cfg))
    log_fd = os.open(CADDY_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        proc = subprocess.Popen([caddy, "run", "--config", str(CADDYFILE), "--adapter", "caddyfile"],
                                stdout=log_fd, stderr=log_fd, start_new_session=True)
    finally:
        os.close(log_fd)
    _write_private(CADDY_PID, str(proc.pid))
    time.sleep(2)
    if proc.poll() is not None:
        CADDY_PID.unlink(missing_ok=True)
        tail = CADDY_LOG.read_text(errors="replace").strip().splitlines()[-3:]
        raise CliError("Caddy s'est arrêté : " + " | ".join(tail)
                       + "\n(ports 80/443 occupés ou non autorisés ? `./install.sh --server` règle l'autorisation)")


def _stop_caddy() -> None:
    pid = _caddy_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(20):  # attend la libération des ports 80/443 avant un éventuel redémarrage
                if not _caddy_pid():
                    break
                time.sleep(0.25)
            else:
                os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    CADDY_PID.unlink(missing_ok=True)


def cmd_server_start(args) -> int:
    """Serveur partagé (plusieurs utilisateurs). Avec --domain : HTTPS automatique via Caddy ; sans : HTTP sur le port."""
    if _pid():
        raise CliError(f"déjà lancé (pid {_pid()}) : `zoombots shutdown` d'abord (la configuration n'a pas été modifiée)")
    try:
        cfg = deploy.update_config(deploy.load_config(), domain=args.domain, no_domain=args.no_domain,
                                   code=args.code, proxy=args.proxy, port=args.port, email=args.email,
                                   no_proxy=args.no_proxy, reset_secrets=args.reset_secrets)
    except deploy.DeployError as exc:
        raise CliError(str(exc)) from exc
    _stop_caddy()  # un Caddy résiduel (par exemple d'un lancement avec domaine) ne doit pas survivre à --no-domain
    pid = _start_server("127.0.0.1" if cfg.get("domain") else "0.0.0.0", cfg["port"], deploy.server_env(cfg))
    if cfg.get("domain"):
        try:
            _start_caddy(cfg)
        except CliError:
            cmd_stop_server(None)
            raise
    deploy.save_config(cfg)  # écrite seulement si tout a démarré : `server info` ne ment pas
    print(f"Serveur lancé (pid {pid}).")
    _print_server_info(cfg)
    return 0


def _print_server_info(cfg: dict) -> None:
    domain = cfg.get("domain")
    if domain:
        print(f"Adresse        : https://{domain}/")
    else:
        ip = deploy.public_ip() or "<IP-du-serveur>"
        print(f"Adresse        : http://{ip}:{cfg['port']}/   (sans HTTPS : ajoute un nom de domaine, voir ci-dessous)")
    print(f"Code d'accès   : {cfg['code']}   (à donner aux utilisateurs)")
    print(f"Jeton admin    : {cfg['token']}   (CLI et /admin, depuis le serveur uniquement)")
    if not cfg.get("proxy"):
        print("⚠ Aucun proxy : Zoom verra l'adresse IP de ce serveur. `zoombots server start --proxy http://hôte:port`")
    else:
        print("Sortie des bots : via proxy (jamais directe). Vérifier : `zoombots check-ip`")
    if not domain:
        print("Pour un nom de domaine : `zoombots server start --domain bots.monsite.fr` puis `zoombots server dns`.")


def cmd_server_dns(args) -> int:
    cfg = deploy.load_config()
    domain = args.domain or cfg.get("domain")
    if not domain:
        raise CliError("indique le domaine : `zoombots server dns --domain bots.monsite.fr`")
    try:
        domain = deploy.validate_domain(domain)
        report = deploy.dns_report(domain)
    except deploy.DeployError as exc:
        raise CliError(str(exc)) from exc
    print(deploy.dns_help(report))
    return 0 if report["ok"] else 1


def cmd_server_info(args) -> int:
    cfg = deploy.load_config()
    if not cfg:
        raise CliError("serveur non configuré : `zoombots server start`")
    _print_server_info(cfg)
    return 0


def cmd_check_ip(args) -> int:
    """Vérifie que les bots ne sortent pas avec l'adresse IP de cette machine."""
    urls = args.proxy or [p for p in (os.environ.get("ZOOMBOTS_PROXY") or deploy.load_config().get("proxy") or "").split(",") if p]
    try:
        report = deploy.check_egress(urls)
    except deploy.DeployError as exc:
        raise CliError(str(exc)) from exc
    print(f"IP directe de cette machine : {report['direct'] or 'inconnue (pas de réseau ?)'}")
    if not urls:
        print("⚠ Aucun proxy configuré : les bots sortent avec cette adresse IP.")
        return 1
    bad = False
    for row in report["proxies"]:
        status = "✔ masquée" if row["masked"] else "✘ identique ou injoignable"
        bad |= not row["masked"]
        print(f"via {row['proxy']} : {row['ip'] or 'échec'}   {status}")
    return 1 if bad else 0


def cmd_stop_server(args) -> int:
    pid = _pid()
    if not pid:
        _stop_caddy()
        PID_FILE.unlink(missing_ok=True)
        RUN_FILE.unlink(missing_ok=True)
        print("serveur non lancé")
        return 0
    try:
        group = os.getpgid(pid)
        os.killpg(group, signal.SIGTERM) if group == pid else os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass  # il s'est arrêté entre-temps
    for _ in range(60):
        if not _pid():
            break
        time.sleep(0.5)
    else:
        print("arrêt forcé", file=sys.stderr)
        os.kill(pid, signal.SIGKILL)
        import asyncio

        from .audio import cleanup_orphans
        from .stats import scan_bot_processes

        for pids in scan_bot_processes().values():  # navigateurs orphelins
            for orphan in pids:
                try:
                    os.kill(orphan, signal.SIGKILL)
                except OSError:
                    pass

        asyncio.run(cleanup_orphans())
    _stop_caddy()  # après l'application : pas de fenêtre où Caddy répondrait 502 pendant l'arrêt des bots
    PID_FILE.unlink(missing_ok=True)
    RUN_FILE.unlink(missing_ok=True)
    print("serveur arrêté")
    return 0


def cmd_status(args) -> int:
    pid = _pid()
    try:
        bots = call("GET", "/bots")
        running = True
    except CliError as exc:
        if "serveur injoignable" not in str(exc):
            raise
        bots, running = [], False
    print(f"serveur : {'lancé' + (' (pid %d)' % pid if pid else '') if running else 'arrêté'}  {BASE}")
    if running:
        table(bots, ["id", "name", "meeting", "status", "reconnects", "owner", "error"])
    return 0


def cmd_logs(args) -> int:
    if not LOG_FILE.exists():
        raise CliError("pas de fichier de log")
    cmd = ["tail", "-n", str(args.lines)] + (["-f"] if args.follow else []) + [str(LOG_FILE)]
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        pass
    return 0


# -- commandes bots -----------------------------------------------------------

def cmd_spawn(args) -> int:
    bots = call("POST", "/bots", {"meeting": args.meeting, "passcode": args.passcode, "name": args.name,
                                  "count": args.count, "auto_reconnect": not args.no_reconnect})
    table(bots, ["id", "name", "meeting", "status"])
    return wait_for(args.wait, [b["id"] for b in bots]) if args.wait else 0


def wait_for(timeout: int, ids: list[str]) -> int:
    """Attend la connexion des bots. Code 1 si l'un d'eux n'est pas dans la réunion à la fin."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        bots = [b for b in call("GET", "/bots") if b["id"] in ids]
        if len(bots) < len(ids):
            raise CliError("des bots ont disparu pendant l'attente")
        if all(b["status"] in ("in_meeting", "denied", "ended", "error") for b in bots):
            table(bots, ["id", "name", "status", "error"])
            return 0 if all(b["status"] == "in_meeting" for b in bots) else 1
        time.sleep(2)
    raise CliError("délai dépassé en attendant la connexion des bots")


def cmd_list(args) -> int:
    bots = call("GET", "/bots")
    if args.json:
        print(json.dumps(bots, indent=2))
    else:
        table(bots, ["id", "name", "meeting", "status", "reconnects", "owner", "error"])
    return 0


def cmd_rm(args) -> int:
    if args.finished:
        print(f"{call('DELETE', '/bots?finished=true')['removed']} bot(s) terminé(s) retiré(s)")
        return 0
    ids = [b["id"] for b in call("GET", "/bots")] if args.all else [resolve(p) for p in args.bots]
    failures = 0
    for bot_id in ids:
        try:
            call("DELETE", f"/bots/{bot_id}")
            print(f"supprimé : {bot_id}")
        except CliError as exc:
            print(f"erreur: {bot_id}: {exc}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


def cmd_mic(args) -> int:
    changed = call("POST", f"/bots/{resolve(args.bot)}/mic", {"on": args.state == "on"})["changed"]
    print("micro " + args.state if changed else f"micro déjà {args.state}")
    return 0


def cmd_sounds(args) -> int:
    for name in call("GET", "/sounds"):
        print(name)
    return 0


def cmd_play(args) -> int:
    sound = urllib.parse.quote(args.sound, safe="")
    if args.bot:
        call("POST", f"/bots/{resolve(args.bot)}/sounds/{sound}", {"volume": args.volume})
        print("lecture lancée")
        return 0
    result = call("POST", f"/broadcast/sounds/{sound}", {"volume": args.volume})
    print(f"joué sur {result['played']} bot(s)")
    for failure in result["failed"]:
        print(f"erreur: {failure['bot']}: {failure['error']}", file=sys.stderr)
    return 1 if result["failed"] else 0


def cmd_silence(args) -> int:
    ids = [resolve(args.bot)] if args.bot else [b["id"] for b in call("GET", "/bots")]
    for bot_id in ids:
        call("POST", f"/bots/{bot_id}/stop")
    return 0


def cmd_stats(args) -> int:
    s = call("GET", "/stats")
    if args.json:
        print(json.dumps(s, indent=2))
        return 0
    print(f"RAM bots : {s['total_rss_mb']} Mo  (moyenne {s['avg_rss_mb_per_bot']} Mo/bot)")
    print(f"RAM dispo : {s['system']['mem_available_mb']} / {s['system']['mem_total_mb']} Mo")
    print(f"charge : {s['system']['load_avg']}  CPU : {s['system']['cpu_count']}")
    print(f"capacité estimée : {s['estimated_capacity']}  limite configurée : {s['max_bots']}")
    return 0


def cmd_events(args) -> int:
    for e in call("GET", f"/bots/{resolve(args.bot)}/events"):
        print(time.strftime("%H:%M:%S", time.localtime(e["ts"])), e.get("status"), e.get("error") or "")
    return 0


def cmd_screenshot(args) -> int:
    Path(args.output).write_bytes(call("GET", f"/bots/{resolve(args.bot)}/screenshot", raw=True))
    print(args.output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="zoombots", description="Gère des bots Zoom (serveur local + API).")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("gui", cmd_gui, "lance l'interface web chez toi (local, sans domaine) et ouvre le navigateur")
    sp.add_argument("--port", type=int, help="port (défaut 8000)")
    sp.add_argument("--proxy", help="proxy de sortie des bots (masque l'IP de cette machine)")
    sp.add_argument("--no-open", action="store_true", help="n'ouvre pas le navigateur")
    sp.add_argument("--headed", action="store_true")
    srv = sub.add_parser("server", help="serveur partagé pour plusieurs utilisateurs (avec ou sans nom de domaine)")
    ssub = srv.add_subparsers(dest="server_cmd", required=True)
    sp = ssub.add_parser("start", help="configure et lance le serveur (HTTPS automatique avec --domain)")
    sp.set_defaults(fn=cmd_server_start)
    sp.add_argument("--domain", help="nom de domaine, ex. bots.monsite.fr")
    sp.add_argument("--no-domain", action="store_true", help="oublie le domaine configuré (HTTP direct)")
    sp.add_argument("--code", help="code d'accès des utilisateurs (généré sinon)")
    sp.add_argument("--proxy", help="proxy(s) de sortie des bots, séparés par des virgules")
    sp.add_argument("--no-proxy", action="store_true", help="oublie le proxy configuré (les bots sortent par l'IP du serveur)")
    sp.add_argument("--reset-secrets", action="store_true", help="génère un nouveau code d'accès et un nouveau jeton")
    sp.add_argument("--port", type=int, help="port de l'application (défaut 8000)")
    sp.add_argument("--email", help="e-mail pour Let's Encrypt (avis d'expiration)")
    sp = ssub.add_parser("dns", help="affiche l'enregistrement DNS à créer et vérifie qu'il est en place")
    sp.set_defaults(fn=cmd_server_dns)
    sp.add_argument("--domain")
    sp = ssub.add_parser("info", help="rappelle l'adresse, le code d'accès et le jeton")
    sp.set_defaults(fn=cmd_server_info)
    sp = add("check-ip", cmd_check_ip, "vérifie que les bots sortent par le proxy et pas par l'IP de cette machine")
    sp.add_argument("--proxy", action="append", help="proxy à tester (défaut : celui configuré)")
    add("serve", cmd_serve, "lance le serveur au premier plan")
    sp = add("start", cmd_start, "lance le serveur en arrière-plan")
    sp.add_argument("--headed", action="store_true", help="Chromium non headless sous xvfb")
    add("shutdown", cmd_stop_server, "arrête le serveur (les bots quittent la réunion)")
    add("status", cmd_status, "état du serveur et des bots")
    sp = add("logs", cmd_logs, "affiche les logs du serveur")
    sp.add_argument("-f", "--follow", action="store_true")
    sp.add_argument("-n", "--lines", type=int, default=50)

    sp = add("spawn", cmd_spawn, "crée des bots dans une réunion")
    sp.add_argument("meeting", help="ID ou lien de réunion")
    sp.add_argument("-p", "--passcode")
    sp.add_argument("-n", "--name", default="Bot")
    sp.add_argument("-c", "--count", type=int, default=1)
    sp.add_argument("--no-reconnect", action="store_true")
    sp.add_argument("--wait", type=int, metavar="SEC", help="attend que les bots soient connectés")
    sp = add("list", cmd_list, "liste les bots")
    sp.add_argument("--json", action="store_true")
    sp = add("rm", cmd_rm, "fait quitter et supprime des bots")
    sp.add_argument("bots", nargs="*", help="identifiants (préfixes acceptés)")
    sp.add_argument("--all", action="store_true", help="tous les bots")
    sp.add_argument("--finished", action="store_true", help="seulement les bots terminés (ended, denied, error, left)")
    sp = add("mic", cmd_mic, "active ou coupe le micro d'un bot")
    sp.add_argument("bot")
    sp.add_argument("state", choices=["on", "off"])
    add("sounds", cmd_sounds, "liste les sons disponibles")
    sp = add("play", cmd_play, "joue un son (sur tous les bots sans --bot)")
    sp.add_argument("sound")
    sp.add_argument("-b", "--bot")
    sp.add_argument("-v", "--volume", type=float, default=1.0)
    sp = add("silence", cmd_silence, "arrête les sons en cours")
    sp.add_argument("bot", nargs="?")
    sp = add("stats", cmd_stats, "ressources consommées")
    sp.add_argument("--json", action="store_true")
    sp = add("events", cmd_events, "historique d'un bot")
    sp.add_argument("bot")
    sp = add("screenshot", cmd_screenshot, "capture d'écran d'un bot")
    sp.add_argument("bot")
    sp.add_argument("-o", "--output", default="screenshot.png")
    return p


def _adopt_running_server() -> None:
    """Les commandes (list, spawn…) visent le serveur lancé par `gui` ou `server start`, avec son jeton."""
    global BASE, TOKEN
    info = _run_info()
    if info and not os.environ.get("ZOOMBOTS_URL"):
        BASE = f"http://{_url_host(info['host'])}:{info['port']}"
    if not os.environ.get("ZOOMBOTS_TOKEN"):
        if info.get("mode") == "server":
            TOKEN = deploy.load_config().get("token")
        elif info.get("token"):
            TOKEN = info["token"]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _adopt_running_server()
    if args.cmd == "rm" and not (args.all or args.bots or args.finished):
        print("zoombots rm: indiquer des bots, --all ou --finished", file=sys.stderr)
        return 2
    try:
        return args.fn(args)
    except CliError as exc:
        print(f"erreur: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
