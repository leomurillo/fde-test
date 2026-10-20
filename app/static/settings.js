(function () {
  "use strict";
  const FDE = window.FDE;
  const msg = document.getElementById("gemini-msg");
  const say = (t, err) => { if (msg) { msg.textContent = t; msg.style.color = err ? "var(--bad)" : ""; } };
  const test = document.getElementById("test-conn");
  if (test) test.addEventListener("click", async () => {
    test.disabled = true;
    say("Testing…");
    try {
      const r = await FDE.postJSON("/api/gemini/test", {});
      say(r.message, !r.ok);
    } catch (e) { say(e.message, true); }
    test.disabled = false;
  });
  const load = document.getElementById("load-models");
  if (load) load.addEventListener("click", async () => {
    load.disabled = true;
    say("Loading models…");
    try {
      const r = await fetch("/api/gemini/models").then((x) => x.json());
      if (!r.ok) { say(r.message, true); return; }
      const dl = document.getElementById("model-list");
      dl.innerHTML = r.models.map((m) => `<option value="${FDE.escape(m)}"></option>`).join("");
      say(`${r.models.length} models available. Start typing in the Model field to pick one.`);
      document.getElementById("model-input").focus();
    } catch (e) { say(e.message, true); }
    finally { load.disabled = false; }
  });
})();
