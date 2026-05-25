// Health tab — per-camera Dahua capability matrix.
//
// Surfaces what each camera natively exposes vs what we'd need a
// Jetson-side backup for. Active probes (snapshot, face DB, ANPR, PTZ,
// deterrence, MJPEG, IVS) plus passive "have we seen this event code"
// checks from the EventTap.

(function () {
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

  const STATUS_PILL = {
    ok:      { cls: "pill-ok",   icon: "✓" },
    missing: { cls: "pill-err",  icon: "✗" },
    error:   { cls: "pill-err",  icon: "!" },
    unknown: { cls: "pill-warn", icon: "?" },
    "n/a":   { cls: "pill-idle", icon: "—" },
  };

  const CATEGORY_LABEL = {
    core: "Core",
    media: "Media",
    ai: "AI features",
    control: "Control",
    events: "Events observed (passive)",
  };

  function statusPill(status) {
    const meta = STATUS_PILL[status] || STATUS_PILL.unknown;
    return el("span", { class: `pill ${meta.cls}` }, `${meta.icon} ${status}`);
  }

  function renderCheckRow(check) {
    const row = el("li", { class: "check-row" });
    const head = el("div", { class: "check-head" },
      statusPill(check.status),
      el("span", { class: "check-label" }, check.label),
    );
    if (check.detail || check.backup_hint) {
      head.appendChild(el("span", { class: "muted check-toggle" }, "▾"));
      row.classList.add("clickable");
    }
    row.appendChild(head);

    if (check.detail || check.backup_hint) {
      const body = el("div", { class: "check-body" });
      if (check.detail) {
        body.appendChild(el("div", { class: "muted" }, `Detail: ${check.detail}`));
      }
      if (check.backup_hint) {
        body.appendChild(el("div", { class: "backup-hint" },
          el("strong", {}, "Backup: "),
          document.createTextNode(check.backup_hint),
        ));
      }
      body.style.display = "none";
      row.appendChild(body);
      head.addEventListener("click", () => {
        body.style.display = body.style.display === "none" ? "block" : "none";
        head.querySelector(".check-toggle").textContent =
          body.style.display === "none" ? "▾" : "▴";
      });
    }
    return row;
  }

  function renderCamera(camera) {
    const card = el("article", { class: "health-card" });
    card.appendChild(el("header", { class: "health-card-header" },
      el("h3", {}, `Camera ${camera.camera_id}`),
      el("span", { class: "muted" }, camera.endpoint),
      el("span", { class: "muted health-model" },
        camera.model ? `· ${camera.model}` : "· model unknown"),
      el("span", { class: "spacer" }),
      el("span", { class: "pill pill-ok" }, `${camera.green_count} ok`),
      el("span", { class: camera.red_count ? "pill pill-err" : "pill pill-idle" },
        `${camera.red_count} missing`),
    ));

    // Group checks by category.
    const byCat = {};
    camera.checks.forEach((c) => {
      (byCat[c.category] = byCat[c.category] || []).push(c);
    });
    Object.keys(byCat).forEach((cat) => {
      card.appendChild(el("h4", { class: "health-cat" },
        CATEGORY_LABEL[cat] || cat,
      ));
      const list = el("ul", { class: "check-list" });
      byCat[cat].forEach((c) => list.appendChild(renderCheckRow(c)));
      card.appendChild(list);
    });

    // Backup recommendations summary.
    if (camera.backup_recommendations && camera.backup_recommendations.length) {
      const back = el("div", { class: "backup-panel" });
      back.appendChild(el("h4", {}, "What you'd need a backup for"));
      const ul = el("ul", {});
      camera.backup_recommendations.forEach((r) => {
        ul.appendChild(el("li", {},
          el("strong", {}, r.label),
          document.createTextNode(` — ${r.hint}`),
        ));
      });
      back.appendChild(ul);
      card.appendChild(back);
    }

    return card;
  }

  async function loadHealth(force = false) {
    const meta = document.getElementById("health-meta");
    const wrap = document.getElementById("health-results");
    const btn = document.getElementById("health-rerun");
    btn.disabled = true;
    meta.textContent = "Probing every camera… (≤ 15s)";
    try {
      const url = force ? "/api/health/cameras?force=1" : "/api/health/cameras";
      const res = await fetch(url);
      const body = await res.json();
      const ts = body.timestamp ? new Date(body.timestamp).toLocaleString() : "?";
      meta.textContent = body.cached
        ? `Cached result from ${ts} — click again to re-probe.`
        : `Probed ${ts}.`;
      wrap.innerHTML = "";
      if (!body.cameras || !body.cameras.length) {
        wrap.appendChild(el("p", { class: "muted" }, "No cameras configured."));
      } else {
        body.cameras.forEach((c) => wrap.appendChild(renderCamera(c)));
      }
    } catch (e) {
      meta.textContent = "Failed: " + e;
    } finally {
      btn.disabled = false;
    }
  }

  function init() {
    document.getElementById("health-rerun").addEventListener(
      "click",
      () => loadHealth(true),
    );
  }

  window.ITIPS = window.ITIPS || {};
  window.ITIPS.health = {
    init,
    onShow: () => loadHealth(false),
  };
})();
