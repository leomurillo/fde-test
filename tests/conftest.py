import json
import shutil
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """No real Gemini key, and the fixture bank, for every test."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("FDE_BANK_DIR", str(FIXTURES))


@pytest.fixture
def bank_copy(tmp_path):
    """A writable copy of the fixture bank."""
    d = tmp_path / "bank"
    d.mkdir()
    shutil.copy(FIXTURES / "D06-sample.yaml", d / "D06-sample.yaml")
    return d


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "var"


@pytest.fixture
def make_client(data_dir):
    from app.main import create_app

    def _make(bank_dir=None, transport=None, **kw):
        app = create_app(data_dir=data_dir, bank_dir=bank_dir or FIXTURES, gemini_transport=transport,
                         sleep=lambda s: None, **kw)
        return TestClient(app)
    return _make


def gemini_reply(obj) -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(obj)}]}}]}


class FakeGemini:
    """httpx.MockTransport handler recording requests. `responses` maps model -> list of (status, json)."""

    def __init__(self, judge=None, missing_models=(), models=None, fail_status=None):
        self.requests: list[httpx.Request] = []
        self.judge = judge or {
            "criteria": [{"criterion": "x", "points": 2, "max_points": 2, "justification": "good"}] * 3,
            "overall_feedback": "Solid.",
        }
        self.missing = set(missing_models)
        self.models = models if models is not None else [
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.0-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.0-flash-preview", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
        ]
        self.fail_status = fail_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "GET" and path.endswith("/models"):
            return httpx.Response(200, json={"models": self.models})
        if path.endswith(":generateContent"):
            model = path.rsplit("/", 1)[-1].split(":")[0]
            if self.fail_status:
                return httpx.Response(self.fail_status, json={"error": {"message": "boom"}})
            if model in self.missing:
                return httpx.Response(404, json={"error": {"message": f"models/{model} is not found"}})
            return httpx.Response(200, json=gemini_reply(self.judge))
        return httpx.Response(404, json={"error": {"message": "unknown"}})

    def transport(self):
        return httpx.MockTransport(self)
