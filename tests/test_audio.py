import asyncio
import os
import stat
import subprocess
import time

import pytest

from zoombots import audio, stats
from zoombots.audio import VirtualMic


@pytest.fixture
def fake_paplay(tmp_path, monkeypatch):
    """paplay factice : écrit ce qu'il reçoit sur stdin dans un fichier."""
    out = tmp_path / "played.raw"
    script = tmp_path / "bin" / "paplay"
    script.parent.mkdir()
    script.write_text(f"#!/bin/sh\ncat > {out}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{script.parent}:{os.environ['PATH']}")
    return out


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "beep.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=0.3", str(path)], check=True)
    return path


async def _finish(mic):
    """Attend la fin naturelle des lectures (destroy() les interrompt)."""
    await asyncio.wait(set(mic._tasks), timeout=10)


async def test_play_pipes_decoded_audio_to_player(fake_paplay, wav):
    mic = VirtualMic("abcdef01")
    await mic.play(wav, 1.0)
    assert mic.playing
    await _finish(mic)
    await mic.destroy()
    assert fake_paplay.stat().st_size > 1000 and not mic.playing


async def test_corrupt_file_is_logged_not_silent(fake_paplay, tmp_path, caplog):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not audio")
    mic = VirtualMic("abcdef01")
    with caplog.at_level("WARNING", logger="zoombots.audio"):
        await mic.play(bad)
        await _finish(mic)
        await mic.destroy()
    assert any("échec" in r.message for r in caplog.records)


async def test_stop_kills_playback_and_survives_dead_group(fake_paplay, wav):
    mic = VirtualMic("abcdef01")
    await mic.play(wav)
    mic.stop()
    await mic.destroy()
    mic.stop()  # groupe déjà disparu : pas d'exception
    assert not mic.playing


async def test_player_limit(fake_paplay, wav, monkeypatch):
    monkeypatch.setattr(audio, "MAX_PLAYERS", 1)
    mic = VirtualMic("abcdef01")
    await mic.play(wav)
    with pytest.raises(RuntimeError):
        await mic.play(wav)
    await mic.destroy()


async def test_create_rolls_back_on_failure(monkeypatch):
    calls = []

    async def fake_run(*args):
        calls.append(args)
        if args[2] == "module-remap-source":
            raise RuntimeError("boom")
        return "42" if args[1] == "load-module" else ""

    monkeypatch.setattr(audio, "_run", fake_run)
    mic = VirtualMic("abcdef01")
    with pytest.raises(RuntimeError):
        await mic.create()
    assert ("pactl", "unload-module", "42") in calls and not mic._modules


async def test_cleanup_orphans_only_touches_zoombots_modules(monkeypatch):
    unloaded = []

    async def fake_run(*args):
        if args[1] == "list":
            return ("5\tmodule-null-sink\tsink_name=zb_abcdef01 sink_properties=x\n"
                    "6\tmodule-remap-source\tmaster=zb_abcdef01.monitor source_name=zb_abcdef01_mic\n"
                    "7\tmodule-null-sink\tsink_name=my_zb_sink\n"
                    "8\tmodule-loopback\tsource=zb_other_app\n")
        unloaded.append(args[2])
        return ""

    monkeypatch.setattr(audio, "_run", fake_run)
    assert await audio.cleanup_orphans() == 2
    assert unloaded == ["5", "6"]


def test_scan_finds_processes_by_pulse_source():
    proc = subprocess.Popen(["sleep", "5"], env={**os.environ, "PULSE_SOURCE": "zb_abcdef01_mic"})
    try:
        for _ in range(50):  # le fils hérite d'abord de l'environnement du parent, jusqu'à l'exec
            found = stats.scan_bot_processes()
            if proc.pid in found.get("zb_abcdef01_mic", []):
                break
            time.sleep(0.05)
        assert proc.pid in found["zb_abcdef01_mic"]
        assert stats.process_usage(found["zb_abcdef01_mic"])["processes"] >= 1
    finally:
        proc.kill()


def _reload(monkeypatch, instance):
    import importlib

    monkeypatch.setenv("ZOOMBOTS_INSTANCE", instance)
    return importlib.reload(audio)


def test_instances_have_distinct_names_and_patterns(monkeypatch):
    try:
        a = _reload(monkeypatch, "a")
        assert a.VirtualMic("abcdef01").sink == "zb_a_abcdef01"
        assert a.ORPHAN.search("sink_name=zb_a_abcdef01 x") and a.ORPHAN.search("source_name=zb_a_abcdef01_mic")
        assert not a.ORPHAN.search("sink_name=zb_b_abcdef01") and not a.ORPHAN.search("sink_name=zb_abcdef01")
        default = _reload(monkeypatch, "")
        assert default.VirtualMic("abcdef01").sink == "zb_abcdef01"
        assert default.ORPHAN.search("sink_name=zb_abcdef01") and not default.ORPHAN.search("sink_name=zb_a_abcdef01")
    finally:
        monkeypatch.delenv("ZOOMBOTS_INSTANCE")
        importlib_reload()


def importlib_reload():
    import importlib

    importlib.reload(audio)


async def test_cleanup_leaves_other_instances_alone(monkeypatch):
    try:
        a = _reload(monkeypatch, "a")
        unloaded = []

        async def fake_run(*args):
            if args[1] == "list":
                return ("1\tmodule-null-sink\tsink_name=zb_a_abcdef01\n"
                        "2\tmodule-null-sink\tsink_name=zb_b_abcdef02\n"
                        "3\tmodule-null-sink\tsink_name=zb_abcdef03\n")
            unloaded.append(args[2])
            return ""

        monkeypatch.setattr(a, "_run", fake_run)
        assert await a.cleanup_orphans() == 1 and unloaded == ["1"]
    finally:
        monkeypatch.delenv("ZOOMBOTS_INSTANCE")
        importlib_reload()


def test_invalid_instance_name_is_refused(monkeypatch):
    with pytest.raises(SystemExit):
        _reload(monkeypatch, "Bad_Name")
    monkeypatch.delenv("ZOOMBOTS_INSTANCE")
    importlib_reload()
