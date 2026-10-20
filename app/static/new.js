/* New-test form: presets, domain/subtopic tree, live pool size and duration preview. */
(function () {
  "use strict";
  const FDE = window.FDE;
  const form = document.getElementById("new-form");
  if (!form) return;
  const $$ = (sel) => Array.from(form.querySelectorAll(sel));
  const preset = document.getElementById("preset");
  const lengthInput = document.getElementById("length");
  const subs = () => $$("input[name=subtopics]");
  const fmts = () => $$("input[name=formats]");

  function syncDomains() {
    $$(".domain-check").forEach((dc) => {
      const children = subs().filter((s) => s.dataset.domain === dc.dataset.domain);
      const n = children.filter((s) => s.checked).length;
      dc.checked = n === children.length && n > 0;
      dc.indeterminate = n > 0 && n < children.length;
    });
  }
  function updateCounts() {
    const allowed = new Set(fmts().filter((f) => f.checked).map((f) => f.value));
    subs().forEach((s) => {
      const counts = JSON.parse(s.dataset.counts || "{}");
      let n = 0;
      for (const [t, k] of Object.entries(counts)) if (allowed.has(t)) n += k;
      const el = form.querySelector(`[data-count-for="${CSS.escape(s.value)}"]`);
      if (el) el.textContent = n;
    });
  }
  function applyPreset() {
    const opt = preset.selectedOptions[0];
    if (!opt || !opt.value) { refresh(); return; }
    const list = new Set((opt.dataset.subtopics || "").split(",").filter(Boolean));
    subs().forEach((s) => (s.checked = list.has(s.value)));
    if (opt.dataset.length) lengthInput.value = opt.dataset.length;
    const f = new Set((opt.dataset.formats || "").split(",").filter(Boolean));
    if (f.size) fmts().forEach((x) => (x.checked = f.has(x.value)));
    refresh();
  }
  function markCustom() {
    if (preset.value) preset.value = "";
  }

  let timer = null;
  function refresh() {
    syncDomains();
    updateCounts();
    clearTimeout(timer);
    timer = setTimeout(preview, 200);
  }
  async function preview() {
    const body = {
      subtopics: subs().filter((s) => s.checked).map((s) => s.value),
      formats: fmts().filter((f) => f.checked).map((f) => f.value),
      length: lengthInput.value,
      priority: (form.querySelector("input[name=priority]:checked") || {}).value,
    };
    const note = document.getElementById("pv-note");
    try {
      const r = await FDE.postJSON("/api/preview", body);
      document.getElementById("pv-pool").textContent = r.pool_size;
      document.getElementById("pv-count").textContent = r.count;
      document.getElementById("pv-time").textContent = r.count ? "≈ " + Math.round(r.est_seconds / 60) + " min" : "–";
      document.getElementById("pv-sections").textContent = Object.entries(r.by_section).map(([k, v]) => `${k} ${v}`).join(" · ");
      note.hidden = !r.note;
      note.textContent = r.note || "";
      document.getElementById("start-btn").disabled = !r.count;
    } catch (err) {
      note.hidden = false;
      note.textContent = err.message;
    }
  }

  preset.addEventListener("change", applyPreset);
  form.addEventListener("change", (e) => {
    if (e.target.classList.contains("domain-check")) {
      subs().filter((s) => s.dataset.domain === e.target.dataset.domain).forEach((s) => (s.checked = e.target.checked));
      markCustom();
    } else if (e.target.name === "subtopics") {
      markCustom();
    }
    if (e.target !== preset) refresh();
  });
  lengthInput.addEventListener("input", refresh);
  form.querySelector("[data-scope-all]").addEventListener("click", () => { subs().forEach((s) => (s.checked = true)); markCustom(); refresh(); });
  form.querySelector("[data-scope-none]").addEventListener("click", () => { subs().forEach((s) => (s.checked = false)); markCustom(); refresh(); });
  form.addEventListener("submit", () => {
    document.getElementById("client-info").value = JSON.stringify({
      ua: navigator.userAgent, lang: navigator.language, screen: [screen.width, screen.height],
      viewport: [innerWidth, innerHeight], tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
    });
  });
  refresh();
})();
