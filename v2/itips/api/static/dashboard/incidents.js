// Incidents tab — list packages, show manifest/signature, link to files.

(function () {
  let items = [];
  let active = null;

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    children.forEach((c) => node.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
    return node;
  }

  async function refresh() {
    const res = await fetch("/api/incidents");
    const body = await res.json();
    items = body.incidents || [];
    document.getElementById("incidents-meta").textContent =
      items.length ? `${items.length} package(s)` : "no incidents yet";
    renderList();
  }

  function renderList() {
    const ul = document.getElementById("incidents-list");
    ul.innerHTML = "";
    if (!items.length) {
      ul.appendChild(el("li", { class: "muted" }, "No incidents on disk."));
      return;
    }
    items.forEach((it) => {
      const li = el("li", { onclick: () => select(it.incident_id) },
        el("strong", {}, it.incident_id.slice(0, 12) + "…"),
        el("small", {},
          (it.started_utc || "—") + (it.finalized ? "  ·  signed" : "  ·  open"),
        ),
      );
      if (active === it.incident_id) li.classList.add("active");
      ul.appendChild(li);
    });
  }

  async function select(incidentId) {
    active = incidentId;
    renderList();
    const panel = document.getElementById("incident-detail");
    panel.className = "incident-detail";
    panel.textContent = "Loading…";
    try {
      const res = await fetch(`/api/incidents/${encodeURIComponent(incidentId)}`);
      if (!res.ok) {
        panel.textContent = "Failed to load incident.";
        return;
      }
      const body = await res.json();
      renderDetail(body);
    } catch (e) {
      panel.textContent = String(e);
    }
  }

  function renderDetail(body) {
    const panel = document.getElementById("incident-detail");
    panel.innerHTML = "";
    panel.appendChild(el("h3", {}, "Metadata"));
    panel.appendChild(el("pre", {}, JSON.stringify(body.metadata || {}, null, 2)));
    panel.appendChild(el("h3", {}, "Signature"));
    panel.appendChild(el("pre", {}, JSON.stringify(body.signature || {}, null, 2)));
    panel.appendChild(el("h3", {}, "Manifest"));
    panel.appendChild(el("pre", {}, JSON.stringify(body.manifest || {}, null, 2)));
    panel.appendChild(el("h3", {}, "Files"));
    const list = el("ul", { class: "file-list" });
    (body.files || []).forEach((name) => {
      const href = `/api/incidents/${encodeURIComponent(body.incident_id)}/files/${encodeURIComponent(name)}`;
      list.appendChild(el("li", {}, el("a", { href }, name)));
    });
    panel.appendChild(list);
  }

  function init() {
    document.getElementById("incidents-refresh").addEventListener("click", () => refresh());
    refresh();
  }

  window.ITIPS = window.ITIPS || {};
  window.ITIPS.incidents = { init, onShow: refresh };
})();
