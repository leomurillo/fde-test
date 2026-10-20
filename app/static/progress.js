/* Progress item browser: load a domain's items when its section is opened; paginate in place. */
(function () {
  "use strict";
  async function load(body, url) {
    body.innerHTML = '<p class="muted small">Loading… <span class="spinner" aria-hidden="true"></span></p>';
    try {
      const res = await fetch(url, { headers: { accept: "text/html" } });
      if (!res.ok) throw new Error(`Could not load items (${res.status})`);
      body.innerHTML = await res.text();
      body.dataset.loaded = "1";
    } catch (err) {
      body.innerHTML = `<p class="sql-error">${window.FDE.escape(err.message)}</p>`;
    }
  }
  document.querySelectorAll("details.browser-domain").forEach((d) => {
    const body = d.querySelector(".browser-body");
    d.addEventListener("toggle", () => {
      if (d.open && !body.dataset.loaded) load(body, body.dataset.src);
    });
    body.addEventListener("click", (e) => {
      const a = e.target.closest("a[data-page-link]");
      if (!a) return;
      e.preventDefault();
      const page = new URL(a.href, location.href).searchParams.get("page_no") || "1";
      load(body, body.dataset.src + "&page_no=" + encodeURIComponent(page)).then(() => d.scrollIntoView({ block: "start" }));
    });
  });
})();
