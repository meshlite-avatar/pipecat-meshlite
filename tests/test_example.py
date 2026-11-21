"""The example loads, builds its page, and keeps the secret key off the page."""

import importlib.util
from pathlib import Path

import pytest
from conftest import PK, SK
from fastapi.testclient import TestClient

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "bot.py"


@pytest.fixture
def example(monkeypatch):
    pytest.importorskip("pipecat.runner.run")
    monkeypatch.setenv("MESHLITE_PUBLISHABLE_KEY", PK)
    monkeypatch.setenv("MESHLITE_SECRET_KEY", SK)
    monkeypatch.setenv("MESHLITE_AVATAR", "av-1")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    spec = importlib.util.spec_from_file_location("meshlite_example_bot", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_page_has_the_sdk_and_public_settings_only(example):
    response = TestClient(example.app).get("/")
    assert response.status_code == 200
    html = response.text
    assert "https://console.meshlite.io/sdk/1.11.60/meshlite.js" in html
    assert f'"publishableKey": "{PK}"' in html
    assert '"avatar": "av-1"' in html
    assert "meshlite-session" in html and "attachPipecat(client)" in html
    assert SK not in html


def test_example_has_an_eval_transport(example):
    assert {"eval", "webrtc"} <= set(example.transport_params)
