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

from .log import DATA_DIR

HOST = os.environ.get("ZOOMBOTS_HOST", "127.0.0.1")
PORT = int(os.environ.get("ZOOMBOTS_PORT", "8000"))
BASE = os.environ.get("ZOOMBOTS_URL") or f"http://{'127.0.0.1' if HOST == '0.0.0.0' else HOST}:{PORT}"
PID_FILE = Path(DATA_DIR) / "server.pid"
LOG_FILE = Path(DATA_DIR) / "server.log"


class CliError(Exception):
    pass


def call(method: str, path: str, body: dict | None = None, raw: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail", exc.reason)
        except ValueError:
            detail = exc.reason
        raise CliError(f"{exc.code}: {detail}") from None
    except urllib.error.URLError:
        raise CliError(f"serveur injoignable sur {BASE} (lancer `zoombots start`)") from None
    return payload if raw else json.loads(payload)


def resolve(prefix: str) -> str:
    """Accepte un préfixe d'identifiant de bot."""
    ids = [b["id"] for b in call("GET", "/bots")]
    matches = [i for i in ids if i.startswith(prefix)]
    if len(matches) != 1:
        raise CliError(f"bot {prefix!r} " + ("introuvable" if not matches else "ambigu"))
    return matches[0]


def table(rows: list[dict], cols: list[str]) -> None:
    if not rows:
        print("(aucun)")
        return
    widths = [max(len(c), *(len(str(r.get(c) or "")) for r in rows)) for c in cols]
    print("  ".join(c.upper().ljust(w) for c, w in zip(cols, widths)))
    for r in rows:
        print("  ".join(str(r.get(c) or "").ljust(w) for c, w in zip(cols, widths)))


# -- gestion du serveur -------------------------------------------------------

def _pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text())
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


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


def cmd_serve(args) -> None:
    ensure_pulseaudio()
    import uvicorn
    uvicorn.run("zoombots.api:app", host=HOST, port=PORT)


def cmd_start(args) -> None:
    if _pid():
        raise CliError(f"déjà lancé (pid {_pid()})")
    ensure_pulseaudio()
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "zoombots"]
    if args.headed:
        if not shutil.which("xvfb-run"):
            raise CliError("--headed nécessite xvfb-run")
        cmd = ["xvfb-run", "-a", *cmd]
    env = {**os.environ, "ZOOMBOTS_HEADED": "1" if args.headed else os.environ.get("ZOOMBOTS_HEADED", "")}
    with open(LOG_FILE, "ab") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True, env=env)
    PID_FILE.write_text(str(proc.pid))
    for _ in range(40):
        if proc.poll() is not None:
            PID_FILE.unlink(missing_ok=True)
            raise CliError(f"le serveur s'est arrêté au démarrage, voir {LOG_FILE}")
        try:
            call("GET", "/bots")
            print(f"zoombots lancé (pid {proc.pid}) : {BASE}")
            return
        except CliError:
            time.sleep(0.5)
    raise CliError(f"le serveur ne répond pas, voir {LOG_FILE}")


def cmd_stop_server(args) -> None:
    pid = _pid()
    if not pid:
        PID_FILE.unlink(missing_ok=True)
        print("serveur non lancé")
        return
    os.killpg(os.getpgid(pid), signal.SIGTERM)  # arrêt propre : les bots quittent et les micros sont déchargés
    for _ in range(60):
        if not _pid():
            break
        time.sleep(0.5)
    else:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    print("serveur arrêté")


def cmd_status(args) -> None:
    pid = _pid()
    print(f"serveur : {'lancé (pid %d)' % pid if pid else 'arrêté'}  {BASE}")
    if pid:
        table(call("GET", "/bots"), ["id", "name", "meeting", "status", "reconnects", "error"])


def cmd_logs(args) -> None:
    if not LOG_FILE.exists():
        raise CliError("pas de fichier de log")
    cmd = ["tail", "-n", str(args.lines)] + (["-f"] if args.follow else []) + [str(LOG_FILE)]
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        pass


# -- commandes bots -----------------------------------------------------------

def cmd_spawn(args) -> None:
    bots = call("POST", "/bots", {"meeting": args.meeting, "passcode": args.passcode, "name": args.name,
                                  "count": args.count, "auto_reconnect": not args.no_reconnect})
    table(bots, ["id", "name", "meeting", "status"])
    if args.wait:
        wait_for(args.wait, [b["id"] for b in bots])


def wait_for(timeout: int, ids: list[str]) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        bots = [b for b in call("GET", "/bots") if b["id"] in ids]
        if all(b["status"] in ("in_meeting", "denied", "ended", "error") for b in bots):
            table(bots, ["id", "name", "status", "error"])
            return
        time.sleep(2)
    raise CliError("délai dépassé en attendant la connexion des bots")


def cmd_list(args) -> None:
    bots = call("GET", "/bots")
    if args.json:
        print(json.dumps(bots, indent=2))
    else:
        table(bots, ["id", "name", "meeting", "status", "reconnects", "error"])


def cmd_rm(args) -> None:
    ids = [b["id"] for b in call("GET", "/bots")] if args.all else [resolve(p) for p in args.bots]
    for bot_id in ids:
        call("DELETE", f"/bots/{bot_id}")
        print(f"supprimé : {bot_id}")


def cmd_mic(args) -> None:
    call("POST", f"/bots/{resolve(args.bot)}/mic", {"on": args.state == "on"})


def cmd_sounds(args) -> None:
    for name in call("GET", "/sounds"):
        print(name)


def cmd_play(args) -> None:
    sound = urllib.parse.quote(args.sound, safe="")
    if args.bot:
        call("POST", f"/bots/{resolve(args.bot)}/sounds/{sound}", {"volume": args.volume})
    else:
        print(f"joué sur {call('POST', f'/broadcast/sounds/{sound}', {'volume': args.volume})['bots']} bot(s)")


def cmd_silence(args) -> None:
    ids = [resolve(args.bot)] if args.bot else [b["id"] for b in call("GET", "/bots")]
    for bot_id in ids:
        call("POST", f"/bots/{bot_id}/stop")


def cmd_stats(args) -> None:
    s = call("GET", "/stats")
    if args.json:
        print(json.dumps(s, indent=2))
        return
    print(f"RAM bots : {s['total_rss_mb']} Mo  (moyenne {s['avg_rss_mb_per_bot']} Mo/bot)")
    print(f"RAM dispo : {s['system']['mem_available_mb']} / {s['system']['mem_total_mb']} Mo")
    print(f"charge : {s['system']['load_avg']}  CPU : {s['system']['cpu_count']}")
    print(f"capacité estimée : {s['estimated_capacity']}  limite configurée : {s['max_bots']}")


def cmd_events(args) -> None:
    for e in call("GET", f"/bots/{resolve(args.bot)}/events"):
        print(time.strftime("%H:%M:%S", time.localtime(e["ts"])), e.get("status"), e.get("error") or "")


def cmd_screenshot(args) -> None:
    Path(args.output).write_bytes(call("GET", f"/bots/{resolve(args.bot)}/screenshot", raw=True))
    print(args.output)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="zoombots", description="Gère des bots Zoom (serveur local + API).")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        return sp

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
    sp.add_argument("--all", action="store_true")
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "rm" and not args.all and not args.bots:
        print("zoombots rm: indiquer des bots ou --all", file=sys.stderr)
        return 2
    try:
        args.fn(args)
    except CliError as exc:
        print(f"erreur: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
