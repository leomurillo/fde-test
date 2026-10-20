"""FastAPI app factory and routes."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Callable

import httpx
import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, store, views
from .assemble import ALL_TYPES, PRIORITIES, SECTION_ORDER
from .bank import BANK_DIR
from .db import connect, default_data_dir, dumps, get_setting, ms_between, new_id, now_iso, open_db, parse_iso, set_setting
from .export import build_data_zip, feedback_jsonl, feedback_md
from .gemini import (DEFAULT_MODEL, GeminiClient, GeminiError, check_connection, resolve_api_key, save_api_key)
from .grading import (RUBRIC_TYPES, Outcome, grade_deterministic, grade_rubric, pending_outcome, self_grade,
                      skipped_outcome)
from .graders.code import run_cases
from .graders.sql import run_query
from .markdown import inline as render_inline, plain_excerpt, render as render_md
from .sync import SyncReport, bank_dir_default, sync_bank
from .taxonomy import load_taxonomy

log = logging.getLogger("fde.app")

APP_DIR = Path(__file__).resolve().parent
SCOPES_DIR = BANK_DIR / "scopes"
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_EVENTS_PER_POST = 500
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TELEMETRY_INT_FIELDS = ("time_to_first_ms", "time_to_submit_ms", "active_ms", "answer_changes", "focus_losses",
                        "paste_count", "runs")
EVENT_TYPES = {"render", "select", "deselect", "input", "paste", "blur", "focus", "visibility", "run_sql", "run_code",
               "audio_start", "audio_stop", "flag", "reveal", "feedback_open", "skip", "submit", "confidence",
               "audio_prep", "audio_fallback", "timeout", "unload"}


# ---------------------------------------------------------------- context

class AppContext:
    def __init__(self, data_dir: Path, bank_dir: Path, gemini_transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.data_dir = Path(data_dir)
        self.bank_dir = Path(bank_dir)
        self.gemini_transport = gemini_transport
        self.sleep = sleep
        conn, self.installation_id = open_db(self.data_dir, __version__)
        self.db_path = self.data_dir / "fde.sqlite3"
        (self.data_dir / "audio").mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        try:
            self.sync_report: SyncReport = sync_bank(conn, self.bank_dir)
        finally:
            conn.close()

    @contextmanager
    def conn(self):
        c = connect(self.db_path)
        try:
            yield c
        finally:
            c.close()

    def reload_bank(self) -> SyncReport:
        with self._lock, self.conn() as c:
            self.sync_report = sync_bank(c, self.bank_dir)
        return self.sync_report

    def _persist_model(self, model: str) -> None:
        with self.conn() as c:
            set_setting(c, "gemini_model", model)

    def gemini_model(self, conn) -> str:
        return get_setting(conn, "gemini_model") or DEFAULT_MODEL

    def gemini_client(self, conn) -> GeminiClient | None:
        key, _ = resolve_api_key(self.data_dir)
        if not key:
            return None
        return GeminiClient(key, self.gemini_model(conn), transport=self.gemini_transport,
                            on_model_change=self._persist_model, sleep=self.sleep)

    def audio_path(self, rid: str) -> Path | None:
        if not UUID_RE.match(rid):
            return None
        for p in (self.data_dir / "audio").glob(f"{rid}.*"):
            return p
        return None

    def grade_later(self, rid: str) -> None:
        """Background rubric grading (end mode). Never raises."""
        try:
            with self.conn() as c:
                resp = store.get_response(c, rid)
                if not resp or resp["grader"] != "none":
                    return
                item = store.snapshot(c, resp["item_id"], resp["item_version"])
                audio, mime = self._audio_bytes(resp)
                outcome = grade_rubric(item, resp["response"], self.gemini_client(c), audio, mime)
                apply_outcome(c, rid, outcome, keep=resp["detail"])
        except Exception:
            log.exception("background grading failed for %s", rid)

    def _audio_bytes(self, resp: dict) -> tuple[bytes | None, str | None]:
        a = resp["response"]
        if not a.get("audio"):
            return None, None
        p = self.audio_path(resp["response_id"])
        return (p.read_bytes() if p else None), a.get("mime")


def apply_outcome(conn, rid: str, o: Outcome, keep: dict | None = None) -> None:
    detail = dict(keep or {})
    detail.pop("status", None)
    detail.pop("reason", None)
    detail.update(o.detail)
    conn.execute("""UPDATE responses SET correct = ?, score = ?, max_points = ?, points = ?, grader = ?,
                    grader_model = ?, grader_detail_json = ? WHERE response_id = ?""",
                 (o.correct_int, o.score, o.max_points, o.points, o.grader, o.grader_model, dumps(detail), rid))
    conn.commit()


def load_scopes() -> list[dict]:
    out = []
    for p in sorted(SCOPES_DIR.glob("*.yaml")):
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if d.get("id") and d.get("subtopics"):
                out.append(d)
        except (OSError, yaml.YAMLError):
            log.warning("could not read scope %s", p)
    return out


# ---------------------------------------------------------------- template helpers

def _fmt_duration(seconds) -> str:
    if seconds is None:
        return "–"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s:02d}s" if s else f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _fmt_ms(ms) -> str:
    return "–" if ms is None else _fmt_duration(ms / 1000)


def _fmt_dt(ts) -> str:
    d = parse_iso(ts) if isinstance(ts, str) else ts
    return d.strftime("%Y-%m-%d %H:%M UTC") if d else "–"


def _fmt_date(ts) -> str:
    d = parse_iso(ts) if isinstance(ts, str) else ts
    return d.strftime("%b %d, %Y") if d else "–"


def _jsonval(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def make_templates() -> Jinja2Templates:
    t = Jinja2Templates(directory=str(APP_DIR / "templates"))
    env = t.env
    env.filters["md"] = lambda s: Markup(render_md(s))
    env.filters["mdi"] = lambda s: Markup(render_inline(str(s or "")))
    env.filters["excerpt"] = plain_excerpt
    env.filters["dur"] = _fmt_duration
    env.filters["ms"] = _fmt_ms
    env.filters["dt"] = _fmt_dt
    env.filters["date"] = _fmt_date
    env.filters["jsonval"] = _jsonval
    env.globals["app_version"] = __version__
    env.globals["feedback_categories"] = store.FEEDBACK_CATEGORIES
    env.globals["type_labels"] = views.TYPE_LABELS
    return t


# ---------------------------------------------------------------- app factory

def create_app(data_dir: Path | str | None = None, bank_dir: Path | str | None = None,
               gemini_transport: httpx.BaseTransport | None = None,
               sleep: Callable[[float], None] = time.sleep) -> FastAPI:
    ctx = AppContext(Path(data_dir) if data_dir else default_data_dir(),
                     Path(bank_dir) if bank_dir else bank_dir_default(), gemini_transport, sleep)
    templates = make_templates()
    app = FastAPI(title="FDE Test", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx
    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

    def page(request: Request, name: str, status_code: int = 200, **context) -> HTMLResponse:
        context.setdefault("nav", "")
        context["installation_id"] = ctx.installation_id
        context["bank"] = ctx.sync_report
        return templates.TemplateResponse(request, name, context, status_code=status_code)

    def render_reveal(request: Request, conn, si: dict, resp: dict | None, sess: dict) -> str:
        item = store.snapshot(conn, si["item_id"], si["item_version"])
        audio_url = f"/media/audio/{resp['response_id']}" if resp and ctx.audio_path(resp["response_id"]) else None
        rv = views.reveal(item, si["presented_order"], resp, audio_url)
        tmpl = templates.get_template("_reveal.html")
        return tmpl.render(rv=rv, session=sess, position=si["position"], request=request)

    def need_session(conn, sid: str) -> dict:
        s = store.get_session(conn, sid) if UUID_RE.match(sid) else None
        if not s:
            raise HTTPException(404, "Session not found")
        return s

    def time_expired(s: dict) -> bool:
        limit = (s["scope"] or {}).get("time_limit_s")
        if not limit or not s["started_at"]:
            return False
        return (ms_between(s["started_at"], now_iso()) or 0) > (int(limit) + 5) * 1000

    # ------------------------------------------------------------ dashboard

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        with ctx.conn() as c:
            prog = store.progress(c)
            recent = store.recent_sessions(c, 10)
        resume = next((s for s in recent if s["status"] == "in_progress"), None)
        prog.pop("status")
        return page(request, "dashboard.html", nav="home", prog=prog, recent=recent, resume=resume)

    # ------------------------------------------------------------ new test

    def _pool_counts(conn) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}
        for it in store.pool_items(conn):
            for tag in it.all_tags:
                counts.setdefault(tag, {}).setdefault(it.type, 0)
                counts[tag][it.type] += 1
        return counts

    @app.get("/new", response_class=HTMLResponse)
    def new_form(request: Request, preset: str | None = None, error: str | None = None):
        scopes = load_scopes()
        with ctx.conn() as c:
            counts = _pool_counts(c)
        chosen = next((s for s in scopes if s["id"] == preset), scopes[0] if scopes else None)
        return page(request, "new.html", nav="new", scopes=scopes, chosen=chosen, tax=load_taxonomy(),
                    counts=counts, types=ALL_TYPES, priorities=PRIORITIES, error=error)

    def _parse_new(data: dict) -> store.NewSession:
        def as_list(v):
            if v is None:
                return []
            return v if isinstance(v, list) else [v]
        try:
            length = max(1, min(200, int(data.get("length") or 20)))
        except (TypeError, ValueError):
            length = 20
        limit = data.get("time_limit_min")
        try:
            time_limit_s = int(float(limit) * 60) if limit not in (None, "", "0") else None
        except (TypeError, ValueError):
            time_limit_s = None
        seed = data.get("seed")
        try:
            seed = int(seed) if seed not in (None, "") else None
        except (TypeError, ValueError):
            seed = None
        client = data.get("client")
        if isinstance(client, str):
            try:
                client = json.loads(client) if client else {}
            except ValueError:
                client = {}
        mode = data.get("mode") if data.get("mode") in store.MODES else "realtime"
        priority = data.get("priority") if data.get("priority") in PRIORITIES else "unseen_first"
        return store.NewSession(
            subtopics=[s for s in as_list(data.get("subtopics")) if load_taxonomy().has_tag(s)],
            length=length, formats=[f for f in as_list(data.get("formats")) if f in ALL_TYPES],
            priority=priority, mode=mode, preset=data.get("preset") or None, time_limit_s=time_limit_s,
            seed=seed, client=client if isinstance(client, dict) else {},
        )

    @app.post("/api/preview")
    async def api_preview(request: Request):
        ns = _parse_new(await request.json())
        with ctx.conn() as c:
            result = store.preview(c, ns)
        return {"pool_size": result.pool_size, "count": len(result.picked), "est_seconds": result.est_seconds,
                "by_section": result.by_section(), "note": result.note}

    @app.post("/new")
    async def new_submit(request: Request):
        form = await request.form()
        data = {k: form.getlist(k) if k in ("subtopics", "formats") else form.get(k) for k in form.keys()}
        ns = _parse_new(data)
        if not ns.subtopics or not ns.formats:
            return RedirectResponse("/new?error=" + "Pick at least one subtopic and one format.", status_code=303)
        try:
            with ctx.conn() as c:
                sid, _ = store.create_session(c, ns, ctx.installation_id, ctx.sync_report.fingerprint)
        except ValueError as exc:
            return RedirectResponse(f"/new?error={exc}", status_code=303)
        return RedirectResponse(f"/s/{sid}", status_code=303)

    # ------------------------------------------------------------ runner

    @app.get("/s/{sid}")
    def session_entry(sid: str):
        with ctx.conn() as c:
            s = need_session(c, sid)
            if s["status"] != "in_progress":
                return RedirectResponse(f"/s/{sid}/review", status_code=303)
            items = store.session_items(c, sid)
            pos = store.resume_position(items, store.latest_responses(c, sid))
        return RedirectResponse(f"/s/{sid}/item/{pos}" if pos else f"/s/{sid}/end", status_code=303)

    @app.get("/s/{sid}/item/{pos}", response_class=HTMLResponse)
    def item_page(request: Request, sid: str, pos: int):
        with ctx.conn() as c:
            s = need_session(c, sid)
            if s["status"] != "in_progress":
                return RedirectResponse(f"/s/{sid}/review", status_code=303)
            if time_expired(s):
                store.finish_session(c, sid)
                return RedirectResponse(f"/s/{sid}/review", status_code=303)
            items = store.session_items(c, sid)
            if not 1 <= pos <= len(items):
                raise HTTPException(404, "No such position")
            responses = store.latest_responses(c, sid)
            si = items[pos - 1]
            resp = responses.get(pos)
            answered = resp is not None and not resp["skipped"]
            reveal_html = None
            if answered:
                if s["mode"] != "realtime":
                    nxt = store.resume_position(items, responses)
                    return RedirectResponse(f"/s/{sid}/item/{nxt}" if nxt else f"/s/{sid}/end", status_code=303)
                reveal_html = Markup(render_reveal(request, c, si, resp, s))
            else:
                store.mark_presented(c, sid, pos)
                s = store.get_session(c, sid)
            item = store.snapshot(c, si["item_id"], si["item_version"])
            pub = views.public_item(item, si["presented_order"])
            attempt_used = False
            if item["type"] == "audio" and not answered:
                att = store.audio_attempt(c, sid, pos)
                if att is not None:
                    # The page was reloaded (or revisited) after the prep countdown started: no new recording.
                    attempt_used = True
                    if not att["interrupted_at"]:
                        store.add_server_event(c, sid, pos, si, "audio_attempt_interrupted", {"token": att["token"]})
            states = store.position_states(items, responses)
        strip = [{"pos": x["position"], "state": states[x["position"]], "section": x["section"]} for x in items]
        sections = [sec for sec in SECTION_ORDER if any(x["section"] == sec for x in items)]
        config = {
            "sessionId": sid, "position": pos, "total": len(items), "type": pub["type"], "mode": s["mode"],
            "answered": answered, "nOptions": len(pub.get("options") or []), "nCorrect": pub.get("n_correct"),
            "maxChars": pub.get("max_chars"), "prepS": pub.get("prep_s"), "maxS": pub.get("max_s"),
            "startedAt": s["started_at"], "timeLimitS": (s["scope"] or {}).get("time_limit_s"),
            "itemId": si["item_id"], "itemVersion": si["item_version"], "attemptUsed": attempt_used,
        }
        return page(request, "runner.html", nav="run", session=s, si=si, item=pub, pos=pos, total=len(items),
                    strip=strip, sections=sections, resp=resp if answered else None, reveal_html=reveal_html,
                    config=config, attempt_used=attempt_used)

    def _telemetry(payload: dict) -> dict:
        tel = payload.get("telemetry") or {}
        out = {}
        for k in TELEMETRY_INT_FIELDS:
            v = tel.get(k)
            try:
                out[k] = max(0, int(v)) if v is not None else None
            except (TypeError, ValueError):
                out[k] = None
        conf = payload.get("confidence", tel.get("confidence"))
        try:
            conf = int(conf) if conf not in (None, "") else None
        except (TypeError, ValueError):
            conf = None
        out["confidence"] = conf if conf in (1, 2, 3) else None
        out["flagged"] = 1 if payload.get("flagged") else 0
        return out

    def _normalize_answer(item: dict, si: dict, raw: dict) -> dict:
        t = item["type"]
        raw = raw if isinstance(raw, dict) else {}
        if t in ("single", "multi"):
            presented = raw.get("selected_presented", raw.get("selected")) or []
            if not isinstance(presented, list):
                presented = [presented]
            return {"selected": views.map_selected(item, si["presented_order"], presented),
                    "selected_presented": [int(p) for p in presented if str(p).lstrip("-").isdigit()]}
        if t == "numeric":
            return {"value": str(raw.get("value", ""))[:100]}
        if t == "open":
            return {"text": str(raw.get("text", ""))[: int(item.get("max_chars") or 2000) + 200]}
        if t == "sql":
            return {"sql": str(raw.get("sql", ""))[:20000]}
        if t == "code":
            return {"code": str(raw.get("code", ""))[:50000]}
        if t == "audio":
            out = {}
            if raw.get("fallback") == "text":
                out = {"fallback": "text", "text": str(raw.get("text", ""))[:10000]}
            for k in ("duration_ms", "mic_error", "encode_error"):
                if raw.get(k) is not None:
                    out[k] = _int_or_none(raw[k]) if k == "duration_ms" else str(raw[k])[:200]
            return out
        return {}

    def _check_audio_attempt(c, sid: str, pos: int, payload: dict, answer: dict, has_file: bool) -> None:
        """Single take, enforced server-side. Once the prep countdown started (marker), a page reload marks the
        attempt interrupted: from then on only the timed written fallback is accepted."""
        att = store.audio_attempt(c, sid, pos)
        if att is None:
            answer["attempt"] = "unmarked"
            return
        answer["attempt_started_at"] = att["started_at"]
        if att["interrupted_at"]:
            if has_file:
                raise HTTPException(409, "The single recording attempt for this item was already used; "
                                         "submit a written answer instead.")
            answer["attempt_used"] = True
            answer["attempt_interrupted_at"] = att["interrupted_at"]
            answer.setdefault("fallback", "text")
            answer.setdefault("text", "")
        elif payload.get("attempt_token") != att["token"]:
            raise HTTPException(409, "This recording does not belong to the current attempt.")

    def _record(request: Request, c, s: dict, si: dict, payload: dict, background: BackgroundTasks,
                audio: bytes | None = None, audio_mime: str | None = None, audio_name: str | None = None) -> dict:
        sid, pos = s["session_id"], si["position"]
        responses = store.latest_responses(c, sid)
        prev = responses.get(pos)
        if prev is not None and not prev["skipped"]:
            raise HTTPException(409, "This item has already been answered.")
        item = store.snapshot(c, si["item_id"], si["item_version"])
        skipped = bool(payload.get("skipped"))
        answer = {} if skipped else _normalize_answer(item, si, payload.get("answer") or {})
        rid = new_id()
        if item["type"] == "audio" and not skipped:
            _check_audio_attempt(c, sid, pos, payload, answer, has_file=audio is not None)
        if audio is not None and not skipped:
            if len(audio) > MAX_AUDIO_BYTES:
                raise HTTPException(413, "Recording too large")
            ext = views.audio_ext(audio_mime, audio_name)
            path = ctx.data_dir / "audio" / f"{rid}.{ext}"
            path.write_bytes(audio)
            # canonical mime per stored extension (audio/x-wav -> audio/wav etc.); this is what Gemini receives
            mime = views.AUDIO_MIME.get(ext) or (audio_mime or "application/octet-stream").split(";")[0]
            answer.update({"audio": f"audio/{rid}.{ext}", "mime": mime, "bytes": len(audio)})
        realtime = s["mode"] == "realtime"
        grade_in_background = False
        if skipped:
            outcome = skipped_outcome(item)
        elif item["type"] in RUBRIC_TYPES:
            client = ctx.gemini_client(c)
            if client is None or realtime:
                outcome = grade_rubric(item, answer, client, audio, answer.get("mime"))
            else:
                text_empty = (item["type"] == "open" or answer.get("fallback") == "text") and not str(
                    answer.get("text") or "").strip()
                if text_empty or (item["type"] == "audio" and answer.get("fallback") != "text" and not audio):
                    outcome = grade_rubric(item, answer, None, audio, answer.get("mime"))
                else:
                    outcome = pending_outcome(item, "grading in background")
                    grade_in_background = True
        else:
            try:
                outcome = grade_deterministic(item, answer)
            except ValueError as exc:  # broken reference query in the bank: do not crash the test
                outcome = Outcome(None, None, 1.0, None, "none", detail={"status": "ungraded", "reason": str(exc)})

        submitted_at = now_iso()
        tel = _telemetry(payload)
        presented_at = si.get("presented_at")
        if tel["time_to_submit_ms"] is None:
            tel["time_to_submit_ms"] = ms_between(presented_at, submitted_at)
        if tel["runs"] is None:
            tel["runs"] = c.execute("""SELECT COUNT(*) FROM events WHERE session_id = ? AND position = ?
                                       AND type IN ('run_sql','run_code')""", (sid, pos)).fetchone()[0]
        first_at = None
        if tel["time_to_first_ms"] is not None and tel["time_to_submit_ms"] is not None:
            delta = tel["time_to_submit_ms"] - tel["time_to_first_ms"]
            first_dt = parse_iso(submitted_at)
            if first_dt is not None and delta >= 0:
                first_at = (first_dt - timedelta(milliseconds=delta)).isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z")
        revealed_at = submitted_at if realtime and not skipped else None
        c.execute("""INSERT INTO responses(response_id, session_id, position, installation_id, item_id, item_version,
                     response_json, correct, score, max_points, points, grader, grader_model, grader_detail_json,
                     presented_at, first_interaction_at, submitted_at, time_to_first_ms, time_to_submit_ms, active_ms,
                     answer_changes, focus_losses, paste_count, runs, confidence, flagged, skipped, mode,
                     feedback_revealed_at, created_at, superseded)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                  (rid, sid, pos, ctx.installation_id, si["item_id"], si["item_version"], dumps(answer),
                   outcome.correct_int, outcome.score, outcome.max_points, outcome.points, outcome.grader,
                   outcome.grader_model, dumps(outcome.detail), presented_at, first_at, submitted_at,
                   tel["time_to_first_ms"], tel["time_to_submit_ms"], tel["active_ms"], tel["answer_changes"],
                   tel["focus_losses"], tel["paste_count"], tel["runs"], tel["confidence"], tel["flagged"],
                   int(skipped), s["mode"], revealed_at, submitted_at))
        if prev is not None:
            c.execute("UPDATE responses SET superseded = 1 WHERE response_id = ?", (prev["response_id"],))
        c.commit()
        if grade_in_background:
            background.add_task(ctx.grade_later, rid)

        items = store.session_items(c, sid)
        responses = store.latest_responses(c, sid)
        nxt = store.next_position(items, responses, pos)
        next_url = f"/s/{sid}/item/{nxt}" if nxt else f"/s/{sid}/end"
        out = {"ok": True, "response_id": rid, "next_url": next_url, "skipped": skipped}
        if realtime and not skipped:
            resp = responses[pos]
            out["verdict"] = views.verdict(resp)
            out["reveal_html"] = render_reveal(request, c, items[pos - 1], resp, s)
        return out

    def _load_for_submit(c, sid: str, pos: int) -> tuple[dict, dict]:
        s = need_session(c, sid)
        if s["status"] != "in_progress":
            raise HTTPException(409, "This session is finished.")
        items = store.session_items(c, sid)
        if not 1 <= pos <= len(items):
            raise HTTPException(404, "No such position")
        return s, items[pos - 1]

    @app.post("/api/s/{sid}/item/{pos}/submit")
    async def api_submit(request: Request, sid: str, pos: int, background: BackgroundTasks):
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(400, "Expected JSON")
        with ctx.conn() as c:
            s, si = _load_for_submit(c, sid, pos)
            return _record(request, c, s, si, payload, background)

    @app.post("/api/s/{sid}/item/{pos}/audio")
    async def api_audio(request: Request, sid: str, pos: int, background: BackgroundTasks):
        form = await request.form()
        meta = form.get("meta") or "{}"
        try:
            payload = json.loads(meta)
        except ValueError:
            raise HTTPException(400, "meta must be JSON")
        upload = form.get("file")
        audio = mime = name = None
        if upload is not None and hasattr(upload, "read"):
            audio = await upload.read()
            mime = upload.content_type
            name = upload.filename
        with ctx.conn() as c:
            s, si = _load_for_submit(c, sid, pos)
            return _record(request, c, s, si, payload, background, audio, mime, name)

    @app.post("/api/s/{sid}/item/{pos}/audio-attempt")
    async def api_audio_attempt(request: Request, sid: str, pos: int):
        """Called when the prep countdown starts. One attempt per (session, position)."""
        try:
            body = await request.json()
        except ValueError:
            body = {}
        with ctx.conn() as c:
            s, si = _load_for_submit(c, sid, pos)
            if store.snapshot(c, si["item_id"], si["item_version"])["type"] != "audio":
                raise HTTPException(400, "Not a spoken item")
            prev = store.latest_responses(c, sid).get(pos)
            if prev is not None and not prev["skipped"]:
                raise HTTPException(409, "This item has already been answered.")
            if store.audio_attempt(c, sid, pos) is not None:
                raise HTTPException(409, "The single attempt for this item was already used.")
            token = new_id()
            store.add_server_event(c, sid, pos, si, "audio_attempt",
                                   {"token": token, "fallback": bool(body.get("fallback"))})
        return {"ok": True, "attempt_token": token}

    @app.post("/api/s/{sid}/item/{pos}/run")
    async def api_run(request: Request, sid: str, pos: int):
        body = await request.json()
        with ctx.conn() as c:
            s, si = _load_for_submit(c, sid, pos)
            item = store.snapshot(c, si["item_id"], si["item_version"])
            if item["type"] == "sql":
                res = run_query(item["sql"]["setup"], str(body.get("sql", ""))[:20000], max_rows=views.RUN_PREVIEW_ROWS)
                result = {"columns": res.columns, "rows": [list(r) for r in res.rows], "truncated": res.truncated,
                          "error": res.error}
                ev_type, ev_payload = "run_sql", {"ok": res.error is None, "rows": len(res.rows)}
            elif item["type"] == "code":
                code = item["code"]
                examples = code.get("examples") or []
                data = run_cases(str(body.get("code", ""))[:50000], code["function"], examples,
                                 float(code.get("timeout_s", 5)))
                if "fatal" in data:
                    result = {"error": data["fatal"], "cases": []}
                else:
                    result = {"error": None, "stdout": data.get("stdout", ""), "cases": [
                        {"args": e.get("args", []), "kwargs": e.get("kwargs", {}), "expected": e.get("expected"),
                         "got": r.get("got"), "passed": r["passed"], "error": r.get("error")}
                        for e, r in zip(examples, data["results"])]}
                ev_type = "run_code"
                ev_payload = {"ok": result["error"] is None,
                              "passed": sum(1 for x in result["cases"] if x["passed"]), "total": len(examples)}
            else:
                raise HTTPException(400, "Only SQL and code items can be run")
            store.add_server_event(c, sid, pos, si, ev_type, ev_payload)
        return result

    @app.post("/api/responses/{rid}/selfgrade")
    async def api_selfgrade(request: Request, rid: str):
        body = await request.json()
        with ctx.conn() as c:
            resp = store.get_response(c, rid) if UUID_RE.match(rid) else None
            if not resp:
                raise HTTPException(404, "Response not found")
            s = store.get_session(c, resp["session_id"])
            item = store.snapshot(c, resp["item_id"], resp["item_version"])
            if item["type"] not in RUBRIC_TYPES or resp["skipped"]:
                raise HTTPException(400, "Only open and spoken answers can be self-graded")
            if resp["grader"] not in ("none", "self"):
                raise HTTPException(409, "This answer was already graded")
            if not (resp["feedback_revealed_at"] or s["status"] != "in_progress"):
                raise HTTPException(409, "Not revealed yet")
            pts = body.get("points") or []
            try:
                pts = [float(p) for p in pts]
            except (TypeError, ValueError):
                raise HTTPException(400, "points must be numbers")
            if len(pts) != len(item.get("rubric") or []):
                raise HTTPException(400, "one score per rubric criterion")
            outcome = self_grade(item, pts, resp["detail"])
            apply_outcome(c, rid, outcome, keep=resp["detail"])
            items = store.session_items(c, resp["session_id"])
            si = items[resp["position"] - 1]
            new = store.latest_responses(c, resp["session_id"])[resp["position"]]
            return {"ok": True, "reveal_html": render_reveal(request, c, si, new, s), "verdict": views.verdict(new)}

    @app.get("/s/{sid}/end", response_class=HTMLResponse)
    def end_page(request: Request, sid: str):
        with ctx.conn() as c:
            s = need_session(c, sid)
            if s["status"] != "in_progress":
                return RedirectResponse(f"/s/{sid}/review", status_code=303)
            items = store.session_items(c, sid)
            states = store.position_states(items, store.latest_responses(c, sid))
        strip = [{"pos": x["position"], "state": states[x["position"]], "section": x["section"]} for x in items]
        return page(request, "end.html", nav="run", session=s, strip=strip,
                    skipped=[p for p, st in states.items() if st == "skipped"],
                    unanswered=[p for p, st in states.items() if st == "open"],
                    answered=sum(1 for st in states.values() if st == "answered"), total=len(items))

    @app.post("/s/{sid}/finish")
    def finish(sid: str):
        with ctx.conn() as c:
            need_session(c, sid)
            store.finish_session(c, sid)
        return RedirectResponse(f"/s/{sid}/review", status_code=303)

    @app.post("/s/{sid}/abandon")
    def abandon(sid: str):
        with ctx.conn() as c:
            need_session(c, sid)
            store.finish_session(c, sid, "abandoned")
        return RedirectResponse("/", status_code=303)

    @app.get("/s/{sid}/review", response_class=HTMLResponse)
    def review(request: Request, sid: str):
        with ctx.conn() as c:
            s = need_session(c, sid)
            if s["status"] == "in_progress":
                return RedirectResponse(f"/s/{sid}/end", status_code=303)
            ts = now_iso()
            c.execute("""UPDATE responses SET feedback_revealed_at = ? WHERE session_id = ? AND skipped = 0
                         AND feedback_revealed_at IS NULL""", (ts, sid))
            c.commit()
            items = store.session_items(c, sid)
            responses = store.latest_responses(c, sid)
            summary = store.session_summary(c, sid, items, responses)
            cards = []
            for si in items:
                item = store.snapshot(c, si["item_id"], si["item_version"])
                resp = responses.get(si["position"])
                cards.append({"si": si, "item": views.public_item(item, si["presented_order"]), "resp": resp,
                              "reveal_html": Markup(render_reveal(request, c, si, resp, s))})
        return page(request, "review.html", nav="run", session=s, summary=summary, cards=cards)

    @app.get("/media/audio/{rid}")
    def media_audio(rid: str):
        p = ctx.audio_path(rid)
        if not p:
            raise HTTPException(404, "No recording")
        ext = p.suffix.lstrip(".")
        return FileResponse(p, media_type=views.AUDIO_MIME.get(ext, "application/octet-stream"))

    # ------------------------------------------------------------ events & feedback

    @app.post("/api/events")
    async def api_events(request: Request):
        raw = await request.body()
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            raise HTTPException(400, "Expected JSON")
        events = body.get("events") if isinstance(body, dict) else body
        if not isinstance(events, list):
            raise HTTPException(400, "Expected a list of events")
        ts = now_iso()
        stored = 0
        with ctx.conn() as c:
            cache: dict[tuple[str, int], tuple | None] = {}
            for ev in events[:MAX_EVENTS_PER_POST]:
                if not isinstance(ev, dict) or ev.get("type") not in EVENT_TYPES:
                    continue
                sid = ev.get("session_id")
                pos = _int_or_none(ev.get("position"))
                item_id = item_version = None
                if sid and pos:
                    if (sid, pos) not in cache:
                        r = c.execute("SELECT item_id, item_version FROM session_items WHERE session_id = ? AND "
                                      "position = ?", (sid, pos)).fetchone()
                        cache[(sid, pos)] = tuple(r) if r else None
                    if cache[(sid, pos)] is None:
                        continue
                    item_id, item_version = cache[(sid, pos)]
                payload = ev.get("payload")
                payload_json = dumps(payload) if payload is not None else None
                if payload_json and len(payload_json) > 2000:
                    payload_json = dumps({"truncated": True})
                client_ts = ev.get("client_ts_ms")
                c.execute("""INSERT INTO events(event_id, session_id, position, item_id, item_version, type,
                             client_ts_ms, server_ts, payload_json) VALUES (?,?,?,?,?,?,?,?,?)""",
                          (new_id(), sid, pos, item_id, item_version, ev["type"],
                           float(client_ts) if isinstance(client_ts, (int, float)) else None, ts, payload_json))
                stored += 1
            c.commit()
        return {"ok": True, "stored": stored}

    @app.post("/api/feedback")
    async def api_feedback(request: Request):
        body = await request.json()
        category = body.get("category")
        text = str(body.get("text") or "").strip()
        if category not in store.FEEDBACK_CATEGORIES:
            raise HTTPException(400, "Unknown category")
        if not text and category in ("other", "ui", "explanation"):
            raise HTTPException(400, "Please add a short note")
        sid = body.get("session_id") or None
        pos = _int_or_none(body.get("position"))
        item_id = body.get("item_id") or None
        item_version = _int_or_none(body.get("item_version"))
        context: dict = {"page": str(body.get("page") or "")[:200]}
        with ctx.conn() as c:
            if sid:
                s = store.get_session(c, sid) if UUID_RE.match(str(sid)) else None
                if not s:
                    raise HTTPException(404, "Session not found")
                context["mode"] = s["mode"]
                if pos:
                    items = store.session_items(c, sid)
                    if not 1 <= pos <= len(items):
                        raise HTTPException(404, "No such position")
                    si = items[pos - 1]
                    item_id, item_version = si["item_id"], si["item_version"]
                    resp = store.latest_responses(c, sid).get(pos)
                    context["position"] = pos
                    context["answered"] = bool(resp and not resp["skipped"])
                    context["skipped"] = bool(resp and resp["skipped"])
                    revealed = bool(resp and (resp["feedback_revealed_at"] or s["status"] != "in_progress"))
                    context["revealed"] = revealed
                    if revealed:
                        item = store.snapshot(c, item_id, item_version)
                        context["user_answer"] = views.answer_summary(item, resp, si["presented_order"])[:4000]
                        context["correct"] = resp["correct"]
                        context["score"] = resp["score"]
            if item_id and item_version is None:
                r = c.execute("SELECT current_version FROM items WHERE item_id = ?", (item_id,)).fetchone()
                item_version = r[0] if r else None
            if item_id and not c.execute("SELECT 1 FROM items WHERE item_id = ?", (item_id,)).fetchone():
                raise HTTPException(404, "Unknown item")
            fid = store.add_feedback(c, ctx.installation_id, category, text[:5000], sid, item_id, item_version,
                                     context)
        return {"ok": True, "feedback_id": fid}

    # ------------------------------------------------------------ progress

    BROWSER_PAGE_SIZE = 50
    BROWSER_STATUSES = ("never_seen", "wrong", "right", "seen", "flagged")

    def _browser_rows(c, st_map: dict, status: str, domain: str, type_: str) -> list[dict]:
        """Item-browser rows (metadata only for never-seen items), filtered."""
        out = []
        for r in store.current_items(c, include_retired=True):
            st = st_map.get(r["item_id"]) or {"status": "never_seen", "attempts": 0, "last_at": None,
                                              "history": [], "flagged": False}
            if r["retired"] and not st["attempts"]:
                continue
            if status == "flagged":
                if not st.get("flagged"):
                    continue
            elif status and st["status"] != status:
                continue
            if domain and r["domain_id"] != domain:
                continue
            if type_ and r["type"] != type_:
                continue
            row = {"item_id": r["item_id"], "version": r["version"], "type": r["type"], "subtopic": r["subtopic"],
                   "domain_id": r["domain_id"], "difficulty": r["difficulty"], "level": r["level"],
                   "time_s": r["time_s"], "retired": r["retired"], **st, "excerpt": None}
            if st["status"] != "never_seen":
                # never-seen items expose metadata only; their stem never reaches the browser
                row["excerpt"] = plain_excerpt(json.loads(r["content_json"]).get("stem"), 220)
            row["subtopic_name"] = views.subtopic_name(r["subtopic"])
            out.append(row)
        return out

    def _browser_page(rows: list[dict], page_no: int) -> tuple[list[dict], dict]:
        pages = max(1, -(-len(rows) // BROWSER_PAGE_SIZE))
        page_no = min(max(1, page_no), pages)
        lo = (page_no - 1) * BROWSER_PAGE_SIZE
        return rows[lo:lo + BROWSER_PAGE_SIZE], {"page": page_no, "pages": pages, "total": len(rows),
                                                  "first": lo + 1 if rows else 0,
                                                  "last": min(lo + BROWSER_PAGE_SIZE, len(rows))}

    def _clean_filters(status: str, type_: str) -> tuple[str, str]:
        return (status if status in BROWSER_STATUSES else ""), (type_ if type_ in ALL_TYPES else "")

    @app.get("/progress", response_class=HTMLResponse)
    def progress_page(request: Request, status: str = "", domain: str = "", type: str = "", page_no: int = 1):
        tax = load_taxonomy()
        status, type = _clean_filters(status, type)
        with ctx.conn() as c:
            prog = store.progress(c)
            st_map = prog.pop("status")
            rows = _browser_rows(c, st_map, status, "", type)
        counts = {d.id: 0 for d in tax.domains}
        for r in rows:
            counts[r["domain_id"]] = counts.get(r["domain_id"], 0) + 1
        open_rows, pager = ([], None)
        if domain in counts:
            open_rows, pager = _browser_page([r for r in rows if r["domain_id"] == domain], page_no)
        return page(request, "progress.html", nav="progress", prog=prog, tax=tax, counts=counts,
                    total_matching=len(rows), open_domain=domain if domain in counts else "",
                    open_rows=open_rows, pager=pager, f_status=status, f_type=type, types=ALL_TYPES)

    @app.get("/progress/items", response_class=HTMLResponse)
    def progress_items(request: Request, domain: str, status: str = "", type: str = "", page_no: int = 1):
        """HTML fragment: one domain's items, one page (loaded when a domain is opened)."""
        status, type = _clean_filters(status, type)
        with ctx.conn() as c:
            st_map = store.item_status_map(c)
            rows = _browser_rows(c, st_map, status, domain, type)
        items, pager = _browser_page(rows, page_no)
        return templates.TemplateResponse(request, "_browser.html", {
            "items": items, "pager": pager, "domain": domain, "f_status": status, "f_type": type,
            "type_labels": views.TYPE_LABELS})

    # ------------------------------------------------------------ feedback list

    @app.get("/feedback", response_class=HTMLResponse)
    def feedback_page(request: Request, show: str = "open"):
        with ctx.conn() as c:
            rows = store.list_feedback(c, include_resolved=(show == "all"))
            counts = c.execute("SELECT SUM(resolved = 0), SUM(resolved = 1) FROM feedback").fetchone()
        return page(request, "feedback.html", nav="feedback", rows=rows, show=show,
                    n_open=counts[0] or 0, n_resolved=counts[1] or 0)

    @app.post("/feedback/{fid}/resolve")
    async def feedback_resolve(request: Request, fid: str):
        form = await request.form()
        resolved = 1 if form.get("resolved", "1") == "1" else 0
        with ctx.conn() as c:
            c.execute("UPDATE feedback SET resolved = ? WHERE feedback_id = ?", (resolved, fid))
            c.commit()
        return RedirectResponse(f"/feedback?show={form.get('show') or 'open'}", status_code=303)

    # ------------------------------------------------------------ exports

    @app.get("/export", response_class=HTMLResponse)
    def export_page(request: Request):
        with ctx.conn() as c:
            counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("sessions", "responses", "events", "feedback", "item_versions")}
        n_audio = len(list((ctx.data_dir / "audio").glob("*")))
        return page(request, "export.html", nav="export", counts=counts, n_audio=n_audio,
                    data_dir=str(ctx.data_dir))

    @app.get("/export/feedback.jsonl")
    def export_feedback_jsonl():
        with ctx.conn() as c:
            body = feedback_jsonl(c)
        return Response(body, media_type="application/x-ndjson",
                        headers={"content-disposition": 'attachment; filename="feedback.jsonl"'})

    @app.get("/export/feedback.md")
    def export_feedback_md():
        with ctx.conn() as c:
            body = feedback_md(c)
        return Response(body, media_type="text/markdown; charset=utf-8",
                        headers={"content-disposition": 'attachment; filename="feedback.md"'})

    @app.get("/export/data.zip")
    def export_zip(audio: int = 0):
        with ctx.conn() as c:
            name, data = build_data_zip(c, ctx.data_dir, bool(audio), ctx.sync_report.fingerprint)
        return Response(data, media_type="application/zip",
                        headers={"content-disposition": f'attachment; filename="{name}"'})

    # ------------------------------------------------------------ settings

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, msg: str = ""):
        key, source = resolve_api_key(ctx.data_dir)
        with ctx.conn() as c:
            model = ctx.gemini_model(c)
        return page(request, "settings.html", nav="settings", key_source=source, has_key=bool(key), model=model,
                    default_model=DEFAULT_MODEL, msg=msg, report=ctx.sync_report, data_dir=str(ctx.data_dir))

    @app.post("/settings/key")
    async def settings_key(request: Request):
        form = await request.form()
        if form.get("action") == "clear":
            save_api_key(ctx.data_dir, None)
            msg = "Saved key removed."
        else:
            key = str(form.get("api_key") or "").strip()
            if not key:
                return RedirectResponse("/settings?msg=Enter a key first.", status_code=303)
            save_api_key(ctx.data_dir, key)
            msg = "Key saved to the local secrets file."
        return RedirectResponse(f"/settings?msg={msg}", status_code=303)

    @app.post("/settings/model")
    async def settings_model(request: Request):
        form = await request.form()
        model = str(form.get("model") or "").strip().removeprefix("models/")
        if model and not re.match(r"^[a-z0-9][a-z0-9.\-]{1,80}$", model):
            return RedirectResponse("/settings?msg=That does not look like a model id.", status_code=303)
        with ctx.conn() as c:
            set_setting(c, "gemini_model", model or None)
        return RedirectResponse(f"/settings?msg=Model set to {model or DEFAULT_MODEL}.", status_code=303)

    @app.post("/settings/reload-bank")
    def settings_reload():
        r = ctx.reload_bank()
        return RedirectResponse(f"/settings?msg=Bank reloaded: {r.loaded} items, {r.skipped} skipped, "
                                f"{len(r.new_versions)} new versions.", status_code=303)

    @app.post("/api/gemini/test")
    def api_gemini_test():
        with ctx.conn() as c:
            client = ctx.gemini_client(c)
        if client is None:
            return {"ok": False, "message": "No API key configured."}
        ok, info = check_connection(client)
        return {"ok": ok, "message": f"Connected; model {info}" if ok else info}

    @app.get("/api/gemini/models")
    def api_gemini_models():
        with ctx.conn() as c:
            client = ctx.gemini_client(c)
        if client is None:
            return {"ok": False, "models": [], "message": "No API key configured."}
        try:
            models = client.list_models()
        except GeminiError as exc:
            return {"ok": False, "models": [], "message": str(exc)}
        names = sorted(m["name"].removeprefix("models/") for m in models
                       if m.get("name", "").startswith("models/gemini")
                       and "generateContent" in (m.get("supportedGenerationMethods") or ["generateContent"]))
        return {"ok": True, "models": names}

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)
        return page(request, "error.html", status_code=exc.status_code, code=exc.status_code, detail=exc.detail)

    return app


def _int_or_none(v):
    try:
        return int(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None
