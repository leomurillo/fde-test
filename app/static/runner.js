/* Item runner: answer capture, telemetry, keyboard shortcuts, SQL/code runs, audio recording, reveal, self-grade.
 *
 * Telemetry (all relative to item render, via performance.now()):
 *   time_to_first_ms  first select / keystroke / paste / run / recording start
 *   time_to_submit_ms render -> submit
 *   active_ms         time with the page visible AND the window focused
 *   answer_changes    single: re-selections after the first pick; multi: deselections;
 *                     text/numeric/sql/code: editing bursts (1.5 s pauses) after the first burst
 *   focus_losses      transitions from active to inactive (blur or tab hidden)
 *   paste_count, runs (sql/code executions), confidence (1 guess, 2 unsure, 3 sure; optional)
 */
(function () {
  "use strict";
  const FDE = window.FDE;
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  // ---------------------------------------------------------------- self-grade (runner + review pages)
  document.addEventListener("submit", async (e) => {
    const form = e.target.closest("form[data-selfgrade]");
    if (!form) return;
    e.preventDefault();
    const rid = form.dataset.selfgrade;
    const crits = {};
    $$("input[type=radio]:checked", form).forEach((i) => { crits[i.dataset.crit] = Number(i.value); });
    const n = $$("fieldset.crit", form).length;
    const points = [];
    for (let i = 0; i < n; i++) {
      if (!(String(i) in crits)) { alert("Score every criterion first."); return; }
      points.push(crits[i]);
    }
    const btn = $("[type=submit]", form);
    btn.disabled = true;
    try {
      const data = await FDE.postJSON(`/api/responses/${rid}/selfgrade`, { points });
      const card = form.closest(".reveal");
      const tmp = document.createElement("div");
      tmp.innerHTML = data.reveal_html;
      card.replaceWith(tmp.firstElementChild);
    } catch (err) {
      alert(err.message);
      btn.disabled = false;
    }
  });

  const C = FDE.config;
  if (!C) return;

  // ---------------------------------------------------------------- timers
  const startedAt = C.startedAt ? Date.parse(C.startedAt) : Date.now();
  const elapsedEl = $("#elapsed");
  const countdownEl = $("#countdown");
  let timeUp = false;
  function tickClock() {
    const elapsed = (Date.now() - startedAt) / 1000;
    if (elapsedEl) elapsedEl.textContent = FDE.fmtClock(elapsed);
    if (countdownEl && C.timeLimitS) {
      const left = C.timeLimitS - elapsed;
      countdownEl.textContent = FDE.fmtClock(left);
      countdownEl.parentElement.classList.toggle("low", left < 60);
      if (left <= 0 && !timeUp) {
        timeUp = true;
        FDE.track("timeout", {});
        FDE.flushEvents(false);
        const f = document.createElement("form");
        f.method = "post";
        f.action = `/s/${C.sessionId}/finish`;
        document.body.appendChild(f);
        alert("Time is up. Your test will be finished now.");
        f.submit();
      }
    }
  }
  tickClock();
  setInterval(tickClock, 1000);

  const nextBar = $("#next-bar");
  const nextBtn = $("#next-btn");
  if (C.answered) {
    // already answered (real-time mode, revisiting): read-only view with reveal
    document.addEventListener("keydown", (e) => {
      if (inEndMenu(e.target)) return;
      if (e.key === "Enter" && !FDE.dialogOpen() && !isTyping(e.target)) { e.preventDefault(); nextBtn.click(); }
    });
    return;
  }

  // ---------------------------------------------------------------- telemetry state
  const T = {
    t0: performance.now(), firstAt: null, activeMs: 0, activeSince: null, changes: 0, focusLosses: 0,
    pastes: 0, runs: 0, bursts: 0, burstTimer: null, picks: 0, submitted: false,
  };
  const isActive = () => document.visibilityState === "visible" && document.hasFocus();
  if (isActive()) T.activeSince = T.t0;
  function updateActive() {
    const now = performance.now();
    const active = isActive();
    if (active && T.activeSince === null) {
      T.activeSince = now;
      FDE.track("focus", {});
    } else if (!active && T.activeSince !== null) {
      T.activeMs += now - T.activeSince;
      T.activeSince = null;
      T.focusLosses += 1;
      FDE.track(document.visibilityState === "hidden" ? "visibility" : "blur", { state: document.visibilityState });
    }
  }
  window.addEventListener("focus", updateActive);
  window.addEventListener("blur", updateActive);
  document.addEventListener("visibilitychange", updateActive);
  function interacted() { if (T.firstAt === null) T.firstAt = performance.now(); }
  function telemetry() {
    const now = performance.now();
    const active = T.activeMs + (T.activeSince !== null ? now - T.activeSince : 0);
    const conf = $("input[name=confidence]:checked");
    return {
      time_to_first_ms: T.firstAt === null ? null : Math.round(T.firstAt - T.t0),
      time_to_submit_ms: Math.round(now - T.t0),
      active_ms: Math.round(active),
      answer_changes: T.changes,
      focus_losses: T.focusLosses,
      paste_count: T.pastes,
      runs: T.runs,
      confidence: conf ? Number(conf.value) : null,
    };
  }
  FDE.track("render", { type: C.type, item_id: C.itemId, item_version: C.itemVersion });

  // ---------------------------------------------------------------- inputs
  const form = $("#answer-form");
  const msg = $("#submit-msg");
  const say = (text, isError) => { if (msg) { msg.textContent = text || ""; msg.className = "form-msg small" + (isError ? " error" : ""); } };

  if (C.type === "single" || C.type === "multi") {
    $$("input[name=opt]", form).forEach((inp) => {
      inp.addEventListener("change", () => {
        interacted();
        T.picks += 1;
        if (C.type === "single" && T.picks > 1) T.changes += 1;
        if (C.type === "multi" && !inp.checked) T.changes += 1;
        FDE.track(inp.checked ? "select" : "deselect", { option: Number(inp.value) });
        say("");
      });
    });
  }

  function textInput(el) {
    if (!el) return;
    el.addEventListener("input", () => {
      interacted();
      if (T.burstTimer === null) {
        T.bursts += 1;
        if (T.bursts > 1) T.changes += 1;
      } else {
        clearTimeout(T.burstTimer);
      }
      T.burstTimer = setTimeout(() => {
        T.burstTimer = null;
        FDE.track("input", { len: el.value.length });
      }, 1500);
      say("");
    });
    el.addEventListener("paste", (e) => {
      interacted();
      T.pastes += 1;
      const text = (e.clipboardData && e.clipboardData.getData("text")) || "";
      FDE.track("paste", { len: text.length });
    });
  }
  const numericInput = $("#numeric-input");
  const openInput = $("#open-input");
  const codeInput = $("#code-input");
  [numericInput, openInput, codeInput].forEach(textInput);

  if (openInput) {
    const counter = $("#char-count");
    const upd = () => { counter.textContent = openInput.value.length; };
    openInput.addEventListener("input", upd);
    upd();
  }
  if (codeInput) {
    codeInput.addEventListener("keydown", (e) => {
      if (e.key === "Tab" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        const s = codeInput.selectionStart, end = codeInput.selectionEnd;
        codeInput.setRangeText("    ", s, end, "end");
        codeInput.dispatchEvent(new Event("input"));
      } else if (e.key === "'" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        runCode();
      }
    });
  }
  $$("input[name=confidence]").forEach((i) => i.addEventListener("change", () => FDE.track("confidence", { value: Number(i.value) })));

  // ---------------------------------------------------------------- flag
  const flagBtn = $("#flag-btn");
  let flagged = false;
  function toggleFlag() {
    if (!flagBtn) return;
    flagged = !flagged;
    flagBtn.setAttribute("aria-pressed", String(flagged));
    FDE.track("flag", { flagged });
  }
  if (flagBtn) flagBtn.addEventListener("click", toggleFlag);

  // ---------------------------------------------------------------- run sql / code
  const runBtn = $("#run-btn");
  const runOut = $("#run-output");
  function table(cols, rows) {
    if (!cols || !cols.length) return '<p class="muted small">No columns returned.</p>';
    const head = cols.map((c) => `<th>${FDE.escape(c)}</th>`).join("");
    const body = rows.map((r) => "<tr>" + r.map((v) => `<td>${v === null ? "NULL" : FDE.escape(v)}</td>`).join("") + "</tr>").join("");
    return `<div class="table-wrap"><table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  async function runCode() {
    if (!runBtn || runBtn.disabled) return;
    interacted();
    T.runs += 1;
    runBtn.disabled = true;
    runOut.innerHTML = '<p class="muted small">Running… <span class="spinner"></span></p>';
    const body = C.type === "sql" ? { sql: codeInput.value } : { code: codeInput.value };
    try {
      const r = await FDE.postJSON(`/api/s/${C.sessionId}/item/${C.position}/run`, body);
      if (C.type === "sql") {
        runOut.innerHTML = r.error
          ? `<p class="sql-error">${FDE.escape(r.error)}</p>`
          : table(r.columns, r.rows) + `<p class="muted small">${r.rows.length}${r.truncated ? "+" : ""} row(s)${r.truncated ? " (first 50 shown)" : ""}</p>`;
      } else if (r.error) {
        runOut.innerHTML = `<pre class="code error">${FDE.escape(r.error)}</pre>`;
      } else {
        const lines = r.cases.map((c, i) => {
          const call = JSON.stringify(c.args) + (c.kwargs && Object.keys(c.kwargs).length ? " " + JSON.stringify(c.kwargs) : "");
          const got = c.error ? c.error : JSON.stringify(c.got);
          return `<li class="${c.passed ? "case-pass" : "case-fail"}">${c.passed ? "✓" : "✕"} Example ${i + 1}: <code>${FDE.escape(call)}</code> → expected <code>${FDE.escape(JSON.stringify(c.expected))}</code>, got <code>${FDE.escape(got)}</code></li>`;
        });
        const passed = r.cases.filter((c) => c.passed).length;
        runOut.innerHTML = `<p><strong>${passed} / ${r.cases.length}</strong> examples pass.</p><ul class="examples">${lines.join("")}</ul>` +
          (r.stdout ? `<details><summary class="small">stdout</summary><pre class="code">${FDE.escape(r.stdout)}</pre></details>` : "");
      }
    } catch (err) {
      runOut.innerHTML = `<p class="sql-error">${FDE.escape(err.message)}</p>`;
    } finally {
      runBtn.disabled = false;
    }
  }
  if (runBtn) runBtn.addEventListener("click", runCode);

  // ---------------------------------------------------------------- answer + submit
  function collectAnswer() {
    switch (C.type) {
      case "single":
      case "multi": {
        const sel = $$("input[name=opt]:checked", form).map((i) => Number(i.value));
        if (!sel.length) return { error: "Select an answer, or use Skip." };
        if (C.type === "multi" && C.nCorrect && sel.length !== C.nCorrect) return { error: `Select exactly ${C.nCorrect} options.` };
        return { answer: { selected_presented: sel } };
      }
      case "numeric": {
        const v = numericInput.value.trim();
        if (!v) return { error: "Enter a number, or use Skip." };
        if (!/^[-+$]?[\d.,_\s]*\d[\d.,_\s]*%?$/.test(v) && isNaN(Number(v))) return { error: "That does not look like a number." };
        return { answer: { value: v } };
      }
      case "open":
        if (!openInput.value.trim()) return { error: "Write an answer, or use Skip." };
        return { answer: { text: openInput.value } };
      case "sql":
        if (!codeInput.value.trim()) return { error: "Write a query, or use Skip." };
        return { answer: { sql: codeInput.value } };
      case "code":
        return { answer: { code: codeInput.value } };
      default:
        return { error: "Unsupported item type." };
    }
  }

  const submitBtn = $("#submit-btn");
  const skipBtn = $("#skip-btn");
  let busy = false;

  function afterSubmit(data, skipped) {
    T.submitted = true;
    FDE.track(skipped ? "skip" : "submit", {});
    if (data.reveal_html) {
      $("#reveal").innerHTML = data.reveal_html;
      FDE.track("reveal", { verdict: data.verdict });
      form.setAttribute("inert", "");
      $$(".actions, .confidence").forEach((el) => (el.hidden = true));
      if (flagBtn) flagBtn.disabled = true;
      nextBtn.href = data.next_url;
      nextBar.hidden = false;
      FDE.flushEvents(false);
      $("#reveal").scrollIntoView({ behavior: "smooth", block: "start" });
      nextBtn.focus({ preventScroll: true });
    } else {
      FDE.flushEvents(true);
      location.href = data.next_url;
    }
  }

  async function submit(skipped) {
    if (busy || T.submitted) return;
    if (T.burstTimer !== null) {
      clearTimeout(T.burstTimer);
      T.burstTimer = null;
      const el = codeInput || openInput || numericInput;
      if (el) FDE.track("input", { len: el.value.length });
    }
    let payload = { skipped: !!skipped, flagged, telemetry: telemetry() };
    if (!skipped) {
      const a = collectAnswer();
      if (a.error) { say(a.error, true); return; }
      payload.answer = a.answer;
    }
    payload.confidence = payload.telemetry.confidence;
    busy = true;
    if (submitBtn) submitBtn.setAttribute("aria-busy", "true");
    const grading = (C.type === "open" && C.mode === "realtime" && !skipped);
    say(grading ? "Grading…" : skipped ? "" : "Saving…");
    try {
      const data = await FDE.postJSON(`/api/s/${C.sessionId}/item/${C.position}/submit`, payload);
      say("");
      afterSubmit(data, skipped);
    } catch (err) {
      say(err.message, true);
      if (err.status === 409) setTimeout(() => location.reload(), 800);
    } finally {
      busy = false;
      if (submitBtn) submitBtn.removeAttribute("aria-busy");
    }
  }
  if (submitBtn) submitBtn.addEventListener("click", () => submit(false));
  if (skipBtn) skipBtn.addEventListener("click", () => {
    if (C.type === "audio" && audio.started) return;
    submit(true);
  });

  // ---------------------------------------------------------------- keyboard
  function inEndMenu(el) {
    return !!(el && el.closest && el.closest(".end-menu"));
  }
  function isTyping(el) {
    if (!el) return false;
    const tag = el.tagName;
    return tag === "TEXTAREA" || tag === "SELECT" || (tag === "INPUT" && !["radio", "checkbox"].includes(el.type));
  }
  document.addEventListener("keydown", (e) => {
    if (FDE.dialogOpen() || e.altKey || inEndMenu(e.target)) return;
    const typing = isTyping(e.target);
    if (e.key === "Enter") {
      if (!nextBar.hidden) { e.preventDefault(); nextBtn.click(); return; }
      if (e.target.tagName === "TEXTAREA") {
        if (e.ctrlKey || e.metaKey) { e.preventDefault(); if (C.type !== "audio") submit(false); }
        return;
      }
      if (e.target.tagName === "BUTTON" || e.target.tagName === "A") return;
      if (C.type !== "audio") { e.preventDefault(); submit(false); }
      return;
    }
    if (typing || e.ctrlKey || e.metaKey) return;
    if (/^[1-9]$/.test(e.key) && (C.type === "single" || C.type === "multi") && !T.submitted) {
      const inp = $$("input[name=opt]", form)[Number(e.key) - 1];
      if (inp) {
        e.preventDefault();
        if (C.type === "single") { if (!inp.checked) { inp.checked = true; inp.dispatchEvent(new Event("change", { bubbles: true })); } }
        else { inp.checked = !inp.checked; inp.dispatchEvent(new Event("change", { bubbles: true })); }
        inp.focus({ preventScroll: true });
      }
    } else if ((e.key === "f" || e.key === "F") && !T.submitted) {
      e.preventDefault();
      toggleFlag();
    }
  });

  // ---------------------------------------------------------------- audio (CandiTech "Video")
  // Single take: when the prep countdown starts we POST an attempt marker; the server refuses a second one and,
  // if the page is reloaded afterwards, renders the timed written fallback instead of a new recording.
  // Recordings are converted in the browser to 16 kHz mono 16-bit PCM WAV (a format Gemini documents);
  // the original MediaRecorder blob is uploaded only if decoding fails.
  const audio = { started: false, stream: null, recorder: null, chunks: [], mime: "", recStart: 0, fallback: false,
                  timer: null, done: false, micError: null, token: null, attemptUsed: !!C.attemptUsed };
  const box = $("#audio-box");
  function stage(name) { $$(".audio-stage", box).forEach((s) => (s.hidden = s.dataset.stage !== name)); }
  function pickMime() {
    if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return "";
    return ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"].find((m) => MediaRecorder.isTypeSupported(m)) || "";
  }
  function countdown(seconds, el, onDone) {
    let left = seconds;
    el.textContent = left;
    clearInterval(audio.timer);
    audio.timer = setInterval(() => {
      left -= 1;
      el.textContent = Math.max(0, left);
      if (left <= 0) { clearInterval(audio.timer); onDone(); }
    }, 1000);
  }
  async function markAttempt() {
    try {
      const r = await FDE.postJSON(`/api/s/${C.sessionId}/item/${C.position}/audio-attempt`, { fallback: audio.fallback });
      audio.token = r.attempt_token;
      return true;
    } catch (err) {
      if (err.status === 409) { location.reload(); return false; }  // attempt already used: server shows fallback
      say(err.message, true);
      return false;
    }
  }
  async function startPrep() {
    interacted();
    $$("#audio-begin, #audio-text").forEach((b) => (b.disabled = true));
    if (!(await markAttempt())) return;
    FDE.track("audio_prep", { prep_s: C.prepS, fallback: audio.fallback });
    stage("prep");
    countdown(C.prepS, $("#prep-left"), startAnswer);
  }
  function meter(stream) {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      const ctx = new Ctx();
      const an = ctx.createAnalyser();
      an.fftSize = 512;
      ctx.createMediaStreamSource(stream).connect(an);
      const buf = new Uint8Array(an.fftSize);
      const bar = $("#level-bar");
      const loop = () => {
        if (audio.done) { ctx.close(); return; }
        an.getByteTimeDomainData(buf);
        let peak = 0;
        for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
        bar.style.width = Math.min(100, (peak / 128) * 180) + "%";
        requestAnimationFrame(loop);
      };
      loop();
    } catch (_) { /* level meter is optional */ }
  }
  function startAnswer() {
    if (audio.started) return;
    audio.started = true;
    clearInterval(audio.timer);
    stage("rec");
    if (skipBtn) skipBtn.disabled = true;
    audio.recStart = performance.now();
    if (audio.fallback) {
      $("#rec-label").textContent = "Writing";
      $(".level", box).hidden = true;
      const wrap = $(".text-fallback", box);
      wrap.hidden = false;
      const ta = $("#fallback-input");
      noEdit(ta);
      ta.focus();
      FDE.track("audio_start", { fallback: "text", attempt_used: audio.attemptUsed });
    } else {
      audio.recorder = new MediaRecorder(audio.stream, audio.mime ? { mimeType: audio.mime } : undefined);
      audio.recorder.ondataavailable = (ev) => { if (ev.data && ev.data.size) audio.chunks.push(ev.data); };
      audio.recorder.onstop = upload;
      audio.recorder.start(1000);
      meter(audio.stream);
      FDE.track("audio_start", { mime: audio.recorder.mimeType || audio.mime });
    }
    countdown(C.maxS, $("#rec-left"), stopAnswer);
  }
  function stopAnswer() {
    if (audio.done) return;
    audio.done = true;
    clearInterval(audio.timer);
    const dur = Math.round(performance.now() - audio.recStart);
    audio.durationMs = dur;
    FDE.track("audio_stop", { duration_ms: dur });
    if (audio.fallback) { upload(); return; }
    if (audio.recorder && audio.recorder.state !== "inactive") audio.recorder.stop();
    if (audio.stream) audio.stream.getTracks().forEach((t) => t.stop());
  }
  function noEdit(ta) {
    const blocked = new Set(["Backspace", "Delete", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"]);
    ta.addEventListener("keydown", (e) => {
      if (blocked.has(e.key) || ((e.ctrlKey || e.metaKey) && /^[zxyav]$/i.test(e.key))) e.preventDefault();
    });
    ["paste", "cut", "drop"].forEach((t) => ta.addEventListener(t, (e) => { e.preventDefault(); if (t === "paste") T.pastes += 1; }));
    const toEnd = () => { const n = ta.value.length; ta.setSelectionRange(n, n); };
    ["click", "select", "focus", "mouseup"].forEach((t) => ta.addEventListener(t, toEnd));
    ta.addEventListener("input", interacted);
  }

  const WAV_RATE = 16000;
  async function toWav(blob) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx();
    let decoded;
    try {
      decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
    } finally {
      if (ctx.close) ctx.close();
    }
    const frames = Math.max(1, Math.ceil(decoded.duration * WAV_RATE));
    const off = new OfflineAudioContext(1, frames, WAV_RATE);  // 1 channel: the graph downmixes to mono
    const src = off.createBufferSource();
    src.buffer = decoded;
    src.connect(off.destination);
    src.start(0);
    const pcm = (await off.startRendering()).getChannelData(0);
    const view = new DataView(new ArrayBuffer(44 + pcm.length * 2));
    const str = (o, s) => { for (let i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i)); };
    str(0, "RIFF"); view.setUint32(4, 36 + pcm.length * 2, true); str(8, "WAVE");
    str(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, WAV_RATE, true); view.setUint32(28, WAV_RATE * 2, true); view.setUint16(32, 2, true);
    view.setUint16(34, 16, true); str(36, "data"); view.setUint32(40, pcm.length * 2, true);
    for (let i = 0; i < pcm.length; i++) {
      const v = Math.max(-1, Math.min(1, pcm[i]));
      view.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7fff, true);
    }
    return new Blob([view], { type: "audio/wav" });
  }

  let uploadFile = null;
  async function upload() {
    stage("upload");
    const meta = {
      skipped: false, flagged, telemetry: telemetry(), attempt_token: audio.token,
      answer: audio.fallback
        ? { fallback: "text", text: $("#fallback-input").value, duration_ms: audio.durationMs, mic_error: audio.micError }
        : { duration_ms: audio.durationMs },
    };
    meta.confidence = meta.telemetry.confidence;
    if (!audio.fallback && !uploadFile) {
      const type = (audio.recorder && audio.recorder.mimeType) || audio.mime || "audio/webm";
      const raw = new Blob(audio.chunks, { type });
      try {
        uploadFile = { blob: await toWav(raw), name: "answer.wav" };
      } catch (err) {
        meta.answer.encode_error = String((err && err.name) || err);
        const ext = type.includes("ogg") ? "ogg" : type.includes("mp4") ? "m4a" : "webm";
        uploadFile = { blob: raw, name: `answer.${ext}` };
      }
    }
    const fd = new FormData();
    fd.append("meta", JSON.stringify(meta));
    if (uploadFile) fd.append("file", uploadFile.blob, uploadFile.name);
    try {
      const res = await fetch(`/api/s/${C.sessionId}/item/${C.position}/audio`, { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `Upload failed (${res.status})`);
      $("[data-stage=upload]", box).innerHTML = `<p class="muted">${audio.fallback ? "Written answer saved." : "Answer recorded and saved."}</p>`;
      afterSubmit(data, false);
    } catch (err) {
      stage("upload");
      $("[data-stage=upload]", box).innerHTML = `<p class="sql-error">${FDE.escape(err.message)}</p><button type="button" class="btn" id="retry-upload">Retry upload</button>`;
      $("#retry-upload").addEventListener("click", upload);
    }
  }
  function useTextFallback(reason) {
    audio.fallback = true;
    audio.micError = reason || null;
    FDE.track("audio_fallback", { reason: reason || "user choice" });
    startPrep();
  }
  if (box && C.type === "audio") {
    if (audio.attemptUsed) {
      // attempt already used on an earlier page load: timed written answer only, no new recording, no prep
      $("#audio-text-used").addEventListener("click", () => {
        interacted();
        audio.fallback = true;
        FDE.track("audio_fallback", { reason: "attempt_used" });
        startAnswer();
      });
    } else {
      $("#audio-begin").addEventListener("click", async () => {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
          useTextFallback("recording not supported in this browser");
          return;
        }
        try {
          audio.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          audio.mime = pickMime();
          startPrep();
        } catch (err) {
          say("Microphone unavailable: switching to a timed written answer.", true);
          useTextFallback(err && err.name ? err.name : "microphone unavailable");
        }
      });
      $("#audio-text").addEventListener("click", () => useTextFallback(null));
    }
    $("#audio-start-now").addEventListener("click", startAnswer);
    $("#audio-stop").addEventListener("click", stopAnswer);
  }
})();
