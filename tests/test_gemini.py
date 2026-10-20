import json
import os
import stat

import httpx
import pytest
import yaml

from app.gemini import (GeminiClient, GeminiError, GeminiUnavailable, check_connection, grade_audio, grade_text,
                        pick_stable_flash, resolve_api_key, save_api_key)
from tests.conftest import FIXTURES, FakeGemini, gemini_reply

ITEMS = {d["id"]: d for d in yaml.safe_load((FIXTURES / "D06-sample.yaml").read_text())["items"]}


def client(fake, model=None, **kw):
    return GeminiClient("secret-key-123", model, transport=fake.transport(), sleep=lambda s: None, **kw)


def test_success_request_shape():
    fake = FakeGemini()
    g = grade_text(client(fake), ITEMS["D6-904"], "Answer text")
    assert g.model == "gemini-flash-latest" and g.max_points == 7
    req = fake.requests[0]
    assert req.url.path == "/v1beta/models/gemini-flash-latest:generateContent"
    assert req.headers["x-goog-api-key"] == "secret-key-123"
    assert "key=" not in str(req.url)
    body = json.loads(req.content)
    cfg = body["generationConfig"]
    assert cfg["temperature"] == 0 and cfg["responseMimeType"] == "application/json" and "responseSchema" in cfg
    text = body["contents"][0]["parts"][0]["text"]
    assert "Bottom line first" in text and "Answer text" in text and "MODEL ANSWER" in text
    assert body["systemInstruction"]["parts"][0]["text"]


def test_audio_inline_and_transcript():
    fake = FakeGemini(judge={"transcript": "Not yet, because...", "criteria": [
        {"criterion": "Clear position", "points": 2, "max_points": 2, "justification": "clear"}],
        "overall_feedback": "fine"})
    g = grade_audio(client(fake), ITEMS["D6-905"], b"OggS-fake-bytes", "audio/ogg;codecs=opus")
    assert g.transcript == "Not yet, because..." and g.points == 2 and g.max_points == 6 and g.correct is False
    part = json.loads(fake.requests[0].content)["contents"][0]["parts"][0]
    assert part["inlineData"]["mimeType"] == "audio/ogg" and part["inlineData"]["data"]


def test_404_falls_back_to_newest_stable_flash_and_persists():
    fake = FakeGemini(missing_models={"gemini-flash-latest"})
    persisted = []
    c = client(fake, on_model_change=persisted.append)
    g = grade_text(c, ITEMS["D6-904"], "x")
    assert g.model == "gemini-2.5-flash" and persisted == ["gemini-2.5-flash"] and c.model == "gemini-2.5-flash"
    paths = [r.url.path for r in fake.requests]
    assert paths == ["/v1beta/models/gemini-flash-latest:generateContent", "/v1beta/models",
                     "/v1beta/models/gemini-2.5-flash:generateContent"]


def test_pick_stable_flash_excludes_variants():
    models = [{"name": f"models/{n}", "supportedGenerationMethods": ["generateContent"]} for n in
              ["gemini-2.0-flash-001", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3.0-flash-preview-05",
               "gemini-2.5-flash-preview-tts", "gemini-live-2.5-flash", "gemini-2.5-flash-image", "gemini-2.5-pro",
               "gemini-10.0-flash-exp"]]
    assert pick_stable_flash(models) == "gemini-2.5-flash"
    assert pick_stable_flash(models[:1]) == "gemini-2.0-flash-001"
    assert pick_stable_flash([]) is None


def test_retry_once_on_429_then_fail():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json=gemini_reply({"criteria": [], "overall_feedback": ""}))
    sleeps = []
    c = GeminiClient("k", transport=httpx.MockTransport(handler), sleep=sleeps.append)
    g = grade_text(c, ITEMS["D6-904"], "x")
    assert len(calls) == 2 and sleeps and g.points == 0

    fake = FakeGemini(fail_status=503)
    with pytest.raises(GeminiError) as exc:
        grade_text(client(fake), ITEMS["D6-904"], "x")
    assert exc.value.status == 503 and len(fake.requests) == 2
    assert "secret-key-123" not in str(exc.value)


def test_non_retryable_error_and_bad_json():
    fake = FakeGemini(fail_status=400)
    with pytest.raises(GeminiError):
        grade_text(client(fake), ITEMS["D6-904"], "x")
    assert len(fake.requests) == 1
    bad = httpx.MockTransport(lambda r: httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "nope"}]}}]}))
    with pytest.raises(GeminiError):
        grade_text(GeminiClient("k", transport=bad), ITEMS["D6-904"], "x")


def test_check_connection():
    ok_t = httpx.MockTransport(lambda r: httpx.Response(200, json=gemini_reply({"ok": True})))
    assert check_connection(GeminiClient("k", transport=ok_t))[0] is True
    assert check_connection(client(FakeGemini(fail_status=403)))[0] is False


def test_key_resolution_and_file_mode(tmp_path, monkeypatch):
    assert resolve_api_key(tmp_path) == (None, "none")
    with pytest.raises(GeminiUnavailable):
        GeminiClient(None)
    save_api_key(tmp_path, " file-key ")
    assert resolve_api_key(tmp_path) == ("file-key", "file")
    assert stat.S_IMODE(os.stat(tmp_path / "secrets.json").st_mode) == 0o600
    monkeypatch.setenv("GOOGLE_API_KEY", "g-key")
    assert resolve_api_key(tmp_path) == ("g-key", "env:GOOGLE_API_KEY")
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    assert resolve_api_key(tmp_path) == ("env-key", "env:GEMINI_API_KEY")
    save_api_key(tmp_path, None)
    assert not (tmp_path / "secrets.json").exists()
    assert "env-key" not in repr(GeminiClient("env-key"))
