import base64
import io
import json
import math
import struct
import wave

from app.gemini import save_api_key
from tests.conftest import FakeGemini
from tests.test_http import new_session, session_items


def wav_bytes(seconds=0.5, rate=16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 8))) for i in range(int(seconds * rate))))
    return buf.getvalue()


def start_attempt(client, sid, pos=1, fallback=False):
    return client.post(f"/api/s/{sid}/item/{pos}/audio-attempt", json={"fallback": fallback})


def upload(client, sid, meta, file=None, pos=1):
    files = {"file": file} if file else None
    return client.post(f"/api/s/{sid}/item/{pos}/audio", data={"meta": json.dumps(meta)}, files=files)


def test_wav_upload_is_stored_and_sent_to_gemini_as_audio_wav(make_client, data_dir):
    fake = FakeGemini(judge={"transcript": "hello", "criteria": [], "overall_feedback": ""})
    save_api_key(data_dir, "k")
    client = make_client(transport=fake.transport())
    sid = new_session(client, "realtime", formats=["audio"], length=1)
    client.get(f"/s/{sid}/item/1")
    token = start_attempt(client, sid).json()["attempt_token"]
    audio = wav_bytes()
    r = upload(client, sid, {"answer": {"duration_ms": 500}, "attempt_token": token},
               ("answer.wav", audio, "audio/x-wav"))  # non-canonical mime on purpose
    assert r.status_code == 200, r.text
    with client.app.state.ctx.conn() as c:
        row = c.execute("SELECT response_id, response_json, grader FROM responses").fetchone()
    ans = json.loads(row["response_json"])
    assert ans["audio"] == f"audio/{row['response_id']}.wav" and ans["mime"] == "audio/wav"
    assert ans["attempt_started_at"] and "attempt_used" not in ans
    stored = data_dir / ans["audio"]
    assert stored.read_bytes() == audio
    with wave.open(str(stored)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
    inline = json.loads(fake.requests[-1].content)["contents"][0]["parts"][0]["inlineData"]
    assert inline["mimeType"] == "audio/wav" and base64.b64decode(inline["data"]) == audio
    assert row["grader"] == "gemini"
    assert client.get(f"/media/audio/{row['response_id']}").headers["content-type"] == "audio/wav"


def test_single_take_enforced_server_side(make_client):
    client = make_client()
    sid = new_session(client, "end", formats=["audio"], length=1)
    page = client.get(f"/s/{sid}/item/1")
    assert "attempt-used" not in page.text and "audio-begin" in page.text
    assert start_attempt(client, sid).status_code == 200
    assert start_attempt(client, sid).status_code == 409          # no second attempt
    # reload after the countdown started: no recorder, only the timed written fallback
    page = client.get(f"/s/{sid}/item/1")
    assert 'id="attempt-used"' in page.text and 'id="audio-begin"' not in page.text
    assert '"attemptUsed": true' in page.text
    assert start_attempt(client, sid).status_code == 409
    r = upload(client, sid, {"answer": {}}, ("answer.wav", wav_bytes(), "audio/wav"))
    assert r.status_code == 409                                     # a recording is refused
    r = upload(client, sid, {"answer": {"fallback": "text", "text": "What I had in mind."}})
    assert r.status_code == 200, r.text
    with client.app.state.ctx.conn() as c:
        ans = json.loads(c.execute("SELECT response_json FROM responses").fetchone()[0])
        types = [r[0] for r in c.execute("SELECT type FROM events WHERE session_id=? ORDER BY server_ts", (sid,))]
    assert ans["attempt_used"] is True and ans["fallback"] == "text" and ans["attempt_interrupted_at"]
    assert ans["text"] == "What I had in mind."
    assert types.count("audio_attempt") == 1 and types.count("audio_attempt_interrupted") == 1
    # clients cannot forge attempt markers through the raw events endpoint
    assert client.post("/api/events", json={"events": [{"type": "audio_attempt", "session_id": sid,
                                                         "position": 1}]}).json()["stored"] == 0


def test_attempt_token_must_match(make_client):
    client = make_client()
    sid = new_session(client, "end", formats=["audio"], length=1)
    client.get(f"/s/{sid}/item/1")
    token = start_attempt(client, sid).json()["attempt_token"]
    f = ("answer.wav", wav_bytes(0.1), "audio/wav")
    assert upload(client, sid, {"answer": {}, "attempt_token": "nope"}, f).status_code == 409
    assert upload(client, sid, {"answer": {}, "attempt_token": token}, f).status_code == 200
