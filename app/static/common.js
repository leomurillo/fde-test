/* Shared helpers: JSON fetch, raw event queue (batched every 5 s + sendBeacon on unload), feedback dialog. */
(function () {
  "use strict";

  const FDE = (window.FDE = window.FDE || {});
  const cfgEl = document.getElementById("item-config");
  FDE.config = cfgEl ? JSON.parse(cfgEl.textContent) : null;

  FDE.postJSON = async function (url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    let data = null;
    try { data = await res.json(); } catch (_) { /* not JSON */ }
    if (!res.ok) {
      const msg = (data && (data.error || data.detail)) || `Request failed (${res.status})`;
      const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
      err.status = res.status;
      throw err;
    }
    return data;
  };

  // ------------------------------------------------------------------ raw events
  const queue = [];
  FDE.track = function (type, payload, opts) {
    const c = FDE.config;
    queue.push({
      type,
      session_id: (opts && opts.session_id) || (c && c.sessionId) || null,
      position: (opts && opts.position) || (c && c.position) || null,
      client_ts_ms: Math.round(performance.now()),
      payload: payload || null,
    });
    if (queue.length >= 200) FDE.flushEvents();
  };
  FDE.flushEvents = function (useBeacon) {
    if (!queue.length) return;
    const batch = queue.splice(0, queue.length);
    const body = JSON.stringify({ events: batch });
    if (useBeacon && navigator.sendBeacon) {
      navigator.sendBeacon("/api/events", new Blob([body], { type: "application/json" }));
      return;
    }
    fetch("/api/events", { method: "POST", headers: { "content-type": "application/json" }, body, keepalive: true })
      .catch(() => { queue.unshift(...batch); });
  };
  setInterval(() => FDE.flushEvents(false), 5000);
  window.addEventListener("pagehide", () => FDE.flushEvents(true));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") FDE.flushEvents(true);
  });

  // ------------------------------------------------------------------ feedback dialog
  const dialog = document.getElementById("feedback-dialog");
  const form = document.getElementById("feedback-form");
  let target = {};
  FDE.dialogOpen = () => !!(dialog && dialog.open);

  function openFeedback(btn) {
    target = {
      item_id: btn.dataset.itemId || null,
      item_version: btn.dataset.itemVersion ? Number(btn.dataset.itemVersion) : null,
      session_id: btn.dataset.sessionId || null,
      position: btn.dataset.position ? Number(btn.dataset.position) : null,
    };
    const label = document.getElementById("fb-target");
    label.textContent = target.item_id
      ? `About item ${target.item_id}${target.item_version ? " (version " + target.item_version + ")" : ""}${target.position ? ", position " + target.position : ""}.`
      : "General feedback about the app or the bank.";
    form.querySelector(".form-msg").textContent = "";
    form.querySelector(".form-msg").className = "form-msg small";
    form.text.value = "";
    form.category.value = target.item_id ? "wrong_key" : "ui";
    if (target.session_id) FDE.track("feedback_open", { item_id: target.item_id }, target);
    if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
    setTimeout(() => form.category.focus(), 0);
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-feedback]");
    if (btn) { e.preventDefault(); openFeedback(btn); }
  });
  if (dialog) {
    dialog.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => dialog.close()));
    form.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); form.requestSubmit(); }
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const msg = form.querySelector(".form-msg");
      const submit = form.querySelector("[type=submit]");
      submit.disabled = true;
      try {
        await FDE.postJSON("/api/feedback", {
          ...target,
          category: form.category.value,
          text: form.text.value,
          page: location.pathname,
        });
        msg.textContent = "Thanks, saved.";
        msg.className = "form-msg small";
        setTimeout(() => dialog.close(), 600);
      } catch (err) {
        msg.textContent = err.message;
        msg.className = "form-msg small error";
      } finally {
        submit.disabled = false;
      }
    });
  }

  // ------------------------------------------------------------------ copy item as text (for pasting into an AI chat)
  // The server builds the text (presented option order; key and explanation only once the item is revealed).
  function copyViaTextarea(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_) { /* fall through */ }
    ta.remove();
    if (!ok) throw new Error("Copy is not available in this browser");
  }
  async function fetchCopyText(btn) {
    const res = await fetch(`/api/s/${btn.dataset.sessionId}/item/${btn.dataset.position}/copy`, { cache: "no-store" });
    if (!res.ok) throw new Error(`Request failed (${res.status})`);
    return (await res.json()).text;
  }
  async function copyItem(btn) {
    const label = btn.querySelector("[data-copy-label]");
    const idle = label.dataset.idle || (label.dataset.idle = label.textContent);
    let msg = "Copied";
    try {
      if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
        // Passing a promise keeps the click's permission while the text is fetched (needed by Safari).
        const blob = fetchCopyText(btn).then((t) => new Blob([t], { type: "text/plain" }));
        await navigator.clipboard.write([new ClipboardItem({ "text/plain": blob })]);
      } else {
        const text = await fetchCopyText(btn);
        if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(text);
        else copyViaTextarea(text);
      }
    } catch (_) {
      try { copyViaTextarea(await fetchCopyText(btn)); } catch (_2) { msg = "Copy failed"; }
    }
    label.textContent = msg;
    clearTimeout(btn._copyTimer);
    btn._copyTimer = setTimeout(() => { label.textContent = idle; }, 1600);
  }
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-copy]");
    if (btn) { e.preventDefault(); copyItem(btn); }
  });

  // ------------------------------------------------------------------ "End test" menu (runner header)
  document.addEventListener("click", (e) => {
    const menu = document.querySelector(".end-menu");
    if (menu && menu.open && !menu.contains(e.target)) menu.open = false;
  });
  document.addEventListener("keydown", (e) => {
    const menu = document.querySelector(".end-menu");
    if (e.key === "Escape" && menu && menu.open) { menu.open = false; menu.querySelector("summary").focus(); }
  });

  FDE.escape = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  };
  FDE.fmtClock = function (sec) {
    sec = Math.max(0, Math.floor(sec));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s).padStart(2, "0");
  };
})();
