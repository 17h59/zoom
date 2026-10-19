import asyncio

import pytest

_real_sleep = asyncio.sleep


@pytest.fixture
def fast_sleep(monkeypatch):
    async def sleep(*_):
        await _real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", sleep)


@pytest.fixture(autouse=True)
def isolated_cli_state(tmp_path, monkeypatch):
    """La CLI ne doit jamais lire l'état d'un vrai serveur de la machine de développement."""
    from zoombots import cli, deploy

    monkeypatch.setattr(cli, "RUN_FILE", tmp_path / "run.json")
    monkeypatch.setattr(deploy, "CONFIG_FILE", tmp_path / "server.json")
