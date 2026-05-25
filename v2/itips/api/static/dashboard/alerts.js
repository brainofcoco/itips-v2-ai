// Alerts tab — consume SSE from /api/alerts/stream and prepend to a list.

(function () {
  let es = null;

  function statusPill(label, kind) {
    const el = document.getElementById("sse-status");
    el.textContent = label;
    el.className = `pill ${kind}`;
  }

  function render(item) {
    const ul = document.getElementById("alerts-list");
    const li = document.createElement("li");

    const row = document.createElement("div");
    row.className = "row";
    const kind = document.createElement("span");
    kind.className = "kind";
    kind.textContent = item.kind || item.alert_type || "event";
    const ts = document.createElement("span");
    ts.className = "ts";
    ts.textContent = item.timestamp_utc || "";
    row.appendChild(kind);
    row.appendChild(ts);
    li.appendChild(row);

    const summary = document.createElement("div");
    summary.className = "muted";
    const bits = [];
    if (item.camera_id !== undefined) bits.push(`cam ${item.camera_id}`);
    if (item.alert_type) bits.push(item.alert_type);
    if (item.track_id !== undefined && item.track_id !== null) bits.push(`track ${item.track_id}`);
    if (item.incident_id) bits.push(`incident ${item.incident_id.slice(0, 8)}…`);
    summary.textContent = bits.join("  ·  ");
    li.appendChild(summary);

    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(item, null, 2);
    li.appendChild(pre);

    ul.insertBefore(li, ul.firstChild);
    while (ul.children.length > 200) ul.removeChild(ul.lastChild);
  }

  async function loadInitial() {
    try {
      const res = await fetch("/api/alerts/latest");
      const body = await res.json();
      const ul = document.getElementById("alerts-list");
      ul.innerHTML = "";
      (body || []).slice(-50).reverse().forEach(render);
    } catch (e) {
      /* ignore */
    }
  }

  function connect() {
    if (es) es.close();
    statusPill("Connecting…", "pill-idle");
    es = new EventSource("/api/alerts/stream");
    es.onopen = () => statusPill("Live", "pill-ok");
    es.onerror = () => statusPill("Reconnecting", "pill-err");
    es.onmessage = (ev) => {
      try { render(JSON.parse(ev.data)); } catch (e) { /* skip */ }
    };
  }

  function init() {
    document.getElementById("alerts-clear").addEventListener("click", () => {
      document.getElementById("alerts-list").innerHTML = "";
    });
    loadInitial().then(connect);
  }

  window.ITIPS = window.ITIPS || {};
  window.ITIPS.alerts = { init };
})();
