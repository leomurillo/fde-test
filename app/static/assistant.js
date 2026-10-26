/* AI assistant sidebar: open/close (remembered per browser), send messages, render replies.
 * Every message is stored server-side with the item on screen; pasted characters are reported with it. */
(function () {
  "use strict";
  const FDE = window.FDE;
  const C = FDE.config;
  const panel = document.getElementById("ai-panel");
  const toggle = document.getElementById("ai-toggle");
  if (!C || !panel || !toggle) return;
  const log = document.getElementById("ai-log");
  const form = document.getElementById("ai-form");
  const input = document.getElementById("ai-input");
  const KEY = "fde.aiPanelOpen";

  function setOpen(open, track) {
    panel.hidden = !open;
    document.body.classList.toggle("ai-open", open);
    toggle.setAttribute("aria-expanded", String(open));
    try { localStorage.setItem(KEY, open ? "1" : "0"); } catch (_) { /* storage is optional */ }
    if (track) FDE.track("ai_panel", { open });
    if (open && input) { input.focus({ preventScroll: true }); }
    if (open) log.scrollTop = log.scrollHeight;
  }
  let initial = false;
  try { initial = localStorage.getItem(KEY) === "1"; } catch (_) { initial = false; }
  setOpen(initial, false);
  toggle.addEventListener("click", () => setOpen(panel.hidden, true));
  document.getElementById("ai-close").addEventListener("click", () => { setOpen(false, true); toggle.focus(); });

  if (!form) return;
  let pasted = 0;
  let busy = false;
  input.addEventListener("paste", (e) => {
    const text = (e.clipboardData && e.clipboardData.getData("text")) || "";
    pasted += text.length;
  });
  function bubble(role, content, isHtml) {
    const empty = document.getElementById("ai-empty");
    if (empty) empty.remove();
    const li = document.createElement("li");
    li.className = "ai-msg ai-" + role;
    const div = document.createElement("div");
    if (isHtml) { div.className = "prose"; div.innerHTML = content; } else { div.className = "ai-text"; div.textContent = content; }
    li.appendChild(div);
    log.appendChild(li);
    log.scrollTop = log.scrollHeight;
    return li;
  }
  async function send() {
    const text = input.value.trim();
    if (!text || busy) return;
    busy = true;
    bubble("user", text, false);
    const pending = bubble("pending", "Thinking…", false);
    const body = { position: C.position, text, pasted_chars: pasted };
    input.value = "";
    pasted = 0;
    try {
      const r = await FDE.postJSON(`/api/s/${C.sessionId}/ai/chat`, body);
      pending.remove();
      if (r.role === "model") bubble("model", r.html, true); else bubble("error", r.text, false);
    } catch (err) {
      pending.remove();
      bubble("error", err.message, false);
    } finally {
      busy = false;
      input.focus({ preventScroll: true });
    }
  }
  form.addEventListener("submit", (e) => { e.preventDefault(); send(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });
})();
