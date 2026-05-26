// Incidents tab — list packages, run a test incident, inspect a
// package (metadata + thumbnails + PDF + signature verify).

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

  async function runTestIncident() {
    const btn = document.getElementById("incidents-test");
    btn.disabled = true;
    btn.textContent = "Building test package…";
    try {
      const res = await fetch("/api/evidence/test/run", { method: "POST" });
      const body = await res.json();
      if (!body.ok) {
        alert("Test incident failed: " + (body.error || body.message));
        return;
      }
      await refresh();
      select(body.incident_id);
    } catch (e) {
      alert("Test incident failed: " + e);
    } finally {
      btn.disabled = false;
      btn.textContent = "Run test incident";
    }
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

  function fileUrl(incidentId, filename, inline = false) {
    const base = `/api/incidents/${encodeURIComponent(incidentId)}/files/`
                  + encodeURI(filename);
    return inline ? base + "?inline=1" : base;
  }

  function renderDetail(body) {
    const panel = document.getElementById("incident-detail");
    panel.innerHTML = "";

    // Header — incident_id + signature verify pill + quick actions.
    const sigPill = el("span", { class: "pill pill-idle" }, "signature: ?");
    const verifyBtn = el("button", { class: "primary" }, "Verify signature");
    verifyBtn.addEventListener("click", () => verifySignature(body.incident_id, sigPill));
    const header = el("div", { class: "toolbar" },
      el("span", { class: "kind" }, body.incident_id),
      sigPill,
      verifyBtn,
    );
    if (body.has_pdf) {
      header.appendChild(el("a", {
        class: "topbar-docs-link",
        href: fileUrl(body.incident_id, "incident_summary.pdf", true),
        target: "_blank", rel: "noopener",
      }, "Open PDF ↗"));
    }
    panel.appendChild(header);

    // Metadata + manifest summary.
    const meta = body.metadata || {};
    const summary = [
      ["Status", meta.status || "?"],
      ["Closed reason", meta.closed_reason || "—"],
      ["Started (UTC)", meta.started_utc || "—"],
      ["Finalized (UTC)", meta.finalized_utc || "—"],
      ["Cameras active", (meta.camera_ids_active || []).join(", ") || "—"],
      ["Classification", meta.incident_classification || "—"],
      ["Events", String(meta.event_count ?? "—")],
      ["Sensor events", String(meta.sensor_event_count ?? "—")],
      ["Face captures", String(meta.face_capture_count ?? body.face_captures.length ?? 0)],
      ["Plate captures", String(meta.plate_capture_count ?? body.plate_captures.length ?? 0)],
    ];
    const tbl = el("table", { class: "thin-table" });
    summary.forEach(([k, v]) => {
      tbl.appendChild(el("tr", {},
        el("th", {}, k),
        el("td", {}, String(v)),
      ));
    });
    panel.appendChild(el("h3", {}, "Summary"));
    panel.appendChild(tbl);

    // Thumbnails.
    renderThumbnails(panel, body, "Face captures", body.face_captures);
    renderThumbnails(panel, body, "Plate captures", body.plate_captures);

    // Video files.
    if (body.video_files && body.video_files.length) {
      panel.appendChild(el("h3", {}, "Video evidence"));
      const list = el("ul", { class: "file-list" });
      body.video_files.forEach((f) => {
        list.appendChild(el("li", {},
          el("a", { href: fileUrl(body.incident_id, f, true), target: "_blank" }, f),
        ));
      });
      panel.appendChild(list);
    }

    // Full manifest + signature dump for the curious.
    panel.appendChild(el("h3", {}, "Manifest"));
    panel.appendChild(el("pre", {}, JSON.stringify(body.manifest || {}, null, 2)));
    panel.appendChild(el("h3", {}, "Signature"));
    panel.appendChild(el("pre", {}, JSON.stringify(body.signature || {}, null, 2)));
  }

  function renderThumbnails(panel, body, title, captures) {
    if (!captures || !captures.length) return;
    panel.appendChild(el("h3", {}, `${title} (${captures.length})`));
    const wrap = el("div", { class: "grid" });
    captures.forEach((c) => {
      const card = el("div", { class: "cam" });
      const feed = el("div", { class: "feed" });
      feed.appendChild(el("img", { src: fileUrl(body.incident_id, c.filename, true) }));
      card.appendChild(feed);
      card.appendChild(el("header", {},
        el("h2", {}, c.filename.split("/").pop()),
        el("span", { class: "muted" }, `${c.bytes} B`),
      ));
      wrap.appendChild(card);
    });
    panel.appendChild(wrap);
  }

  async function verifySignature(incidentId, pill) {
    pill.className = "pill pill-idle";
    pill.textContent = "signature: verifying…";
    try {
      const res = await fetch(
        `/api/incidents/${encodeURIComponent(incidentId)}/verify`,
        { method: "POST" },
      );
      const body = await res.json();
      if (body.ok) {
        pill.className = "pill pill-ok";
        pill.textContent = "signature: valid";
      } else {
        pill.className = "pill pill-err";
        const bad = (body.files || []).filter((f) => f.status !== "ok");
        pill.textContent = `signature: ${bad.length} file mismatch${bad.length === 1 ? "" : "es"}`;
        pill.title = JSON.stringify(bad, null, 2);
      }
    } catch (e) {
      pill.className = "pill pill-err";
      pill.textContent = "signature: verify failed";
      pill.title = String(e);
    }
  }

  function init() {
    document.getElementById("incidents-refresh").addEventListener("click", () => refresh());
    document.getElementById("incidents-test").addEventListener("click", runTestIncident);
    refresh();
  }

  window.ITIPS = window.ITIPS || {};
  window.ITIPS.incidents = { init, onShow: refresh };
})();
