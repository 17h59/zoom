"""Le vrai GUI dans un vrai Chromium (serveur uvicorn réel, faux bots), pour vérifier le JavaScript."""

import asyncio
import glob
import socket
import subprocess
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from zoombots import api, gui_api
from zoombots import manager as mgr
from zoombots import sessions as S
from zoombots.bot import Bot


class FakePlaywright:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *a):
        pass


@pytest.fixture
def server(tmp_path, monkeypatch):
    async def fake_run(self, pw, gate=None):
        self._set_status("in_meeting")
        await asyncio.sleep(3600)

    async def fake_play_wait(self, path, volume=1.0):
        await asyncio.sleep(0.4)

    async def noop(*a, **k):
        return 0

    async def fake_screenshot(self):
        raise RuntimeError("pas de capture")

    monkeypatch.setattr(Bot, "run", fake_run)
    monkeypatch.setattr(Bot, "play_wait", fake_play_wait)
    monkeypatch.setattr(Bot, "screenshot", fake_screenshot)
    monkeypatch.setattr(mgr, "async_playwright", FakePlaywright)
    monkeypatch.setattr(mgr, "cleanup_orphans", noop)
    monkeypatch.setattr(S, "ROOT", tmp_path / "sessions")
    monkeypatch.setattr(S, "GRACE_S", 0.5)
    monkeypatch.setattr(api.manager, "_state_path", tmp_path / "state.json")
    monkeypatch.setattr(api, "TOKEN", None)
    monkeypatch.setattr(gui_api, "ACCESS_CODE", None)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(10)


@pytest.fixture
def browser():
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception:  # noqa: BLE001
            found = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
            if not found:
                pytest.skip("Chromium indisponible")
            b = pw.chromium.launch(executable_path=found[-1], args=["--no-sandbox"])
        yield b
        b.close()


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "beep.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=0.5", str(path)], check=True)
    return path


def wait_for(predicate, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_full_user_journey(server, browser, wav, tmp_path):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    # les 404 de capture d'écran sont attendus : les faux bots n'ont pas de page
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "404" not in m.text else None)
    page.goto(server)

    # arrivée : rien dessus
    page.wait_for_selector("#conn.on")
    assert "Aucun bot" in page.inner_text("#bots")
    assert page.inner_text("#sounds").startswith("Aucun son")

    # déployer 3 bots répartis sur 2 liens
    page.fill("#links", "123456789\nhttps://acme.zoom.us/j/987654321?pwd=abc")
    page.fill("#count", "3")
    page.click("#deploy-btn")
    page.wait_for_selector(".card")
    assert wait_for(lambda: page.locator(".card").count() == 3)
    links = page.locator(".card .muted").all_inner_texts()
    assert sorted(l for l in links if "/" in l) == ["acme.zoom.us/987654321", "app.zoom.us/123456789", "app.zoom.us/123456789"]
    assert page.locator(".card .badge.in_meeting").count() == 3

    # déposer un fichier (parcourir) -> le son apparaît
    page.set_input_files("#file-input", str(wav))
    page.wait_for_selector("#sounds .chip:not(.pending):has-text('beep.wav')")
    assert wait_for(lambda: page.locator("#act-sound option:has-text('beep.wav')").count() == 1)

    # action : 2 répétitions sur tous les bots
    page.fill("#act-count", "2")
    page.click("#act-btn")
    ok = wait_for(lambda: page.locator(".card:has-text('2/2')").count() == 3)
    assert ok, (page.inner_text("#toast"), page.inner_text("#bots"))

    # cliquer un bot ouvre le panneau, avec son historique
    page.locator(".card").first.click()
    page.wait_for_selector("#drawer:not([hidden])")
    assert wait_for(lambda: page.locator("#d-events li").count() >= 1)
    page.screenshot(path="/tmp/gui.png")
    page.click("#d-close")

    # retirer un bot individuellement
    page.locator(".card").first.locator("button:has-text('Retirer')").click()
    assert wait_for(lambda: page.locator(".card").count() == 2)

    # glisser un son sur un bot le joue une fois
    page.evaluate("""() => {
        const chip = document.querySelector('#sounds .chip');
        const card = document.querySelector('.card');
        const dt = new DataTransfer();
        chip.dispatchEvent(new DragEvent('dragstart', {dataTransfer: dt, bubbles: true}));
        card.dispatchEvent(new DragEvent('dragover', {dataTransfer: dt, bubbles: true, cancelable: true}));
        card.dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true}));
    }""")
    assert wait_for(lambda: page.locator(".card:has-text('1/1')").count() >= 1)

    # un rechargement reprend la session (même onglet)
    page.reload()
    page.wait_for_selector("#conn.on")
    assert wait_for(lambda: page.locator(".card").count() == 2)

    # un autre onglet = une autre session, vide
    other = ctx.new_page()
    other.goto(server)
    other.wait_for_selector("#conn.on")
    assert "Aucun bot" in other.inner_text("#bots")
    assert len(api.manager.bots) == 2

    # fermer l'onglet ferme ses bots
    page.close()
    assert wait_for(lambda: len(api.manager.bots) == 0, 8)
    assert not errors, errors
