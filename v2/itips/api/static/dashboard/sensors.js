// Sensors tab — bind AX PRO zones to camera + PTZ preset, simulate
// triggers, watch the dispatcher's outcomes land in real time.
//
// The dropdown for "preset" depends on which camera is selected, so
// it's repopulated each time the camera selector changes by calling
// /api/cameras/<id>/presets. That endpoint hits the camera's own
// preset table — no Jetson-side preset registry to keep in sync.

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

  let pollHandle = null;

  // ─── camera + preset selectors ─────────────────────────────────

  async function loadCameras() {
    const sel = document.getElementById("sensor-camera");
    sel.innerHTML = "";
    try {
      const res = await fetch("/api/cameras");
      const body = await res.json();
      const cams = body.cameras || [];
      if (!cams.length) {
        sel.appendChild(el("option", { value: "" }, "(no cameras)"));
      } else {
        cams.forEach((c) => sel.appendChild(
          el("option", { value: String(c.camera_id) },
            `cam${c.camera_id} · ${c.endpoint}`)));
        // Populate the preset dropdown for whichever camera lands first.
        loadPresetsFor(cams[0].camera_id);
      }
    } catch (e) {
      sel.appendChild(el("option", { value: "" }, "(load failed)"));
    }
  }

  async function loadPresetsFor(cameraId) {
    const sel = document.getElementById("sensor-preset");
    sel.innerHTML = "";
    sel.appendChild(el("option", { value: "" }, "— pick a preset —"));
    if (!cameraId) return;
    try {
      const res = await fetch(`/api/cameras/${cameraId}/presets`);
      if (!res.ok) {
        sel.appendChild(el("option", { value: "" }, "(presets unavailable)"));
        return;
      }
      const body = await res.json();
      (body.presets || []).forEach((p) => {
        sel.appendChild(el("option", { value: p.name },
          `${p.name} (#${p.index})`));
      });
      if ((body.presets || []).length === 0) {
        sel.appendChild(el("option", { value: "" },
          "(no presets defined on this camera)"));
      }
    } catch (e) {
      sel.appendChild(el("option", { value: "" }, "(presets fetch failed)"));
    }
  }

  // ─── bindings CRUD ─────────────────────────────────────────────

  async function loadHubStatus() {
    const pill = document.getElementById("sensor-hub-pill");
    const hostEl = document.getElementById("sensor-hub-host");
    try {
      const res = await fetch("/api/sensors/listener/status");
      const body = await res.json();
      if (!body.wired) {
        pill.className = "pill pill-idle";
        pill.textContent = "not wired";
        hostEl.textContent = body.reason || "";
        return;
      }
      hostEl.textContent = `host=${body.host}` +
        (body.last_error ? ` · last_error=${body.last_error}` : "");
      if (body.connected) {
        pill.className = body.armed ? "pill pill-ok" : "pill pill-warn";
        pill.textContent = body.armed ? "connected · armed" : "connected · disarmed";
      } else {
        pill.className = "pill pill-err";
        pill.textContent = "disconnected";
      }
    } catch (e) {
      pill.className = "pill pill-err";
      pill.textContent = "status fetch failed";
      hostEl.textContent = String(e);
    }
  }

  async function loadMappings() {
    const ul = document.getElementById("sensor-mappings");
    ul.innerHTML = "";
    try {
      const res = await fetch("/api/sensors/map");
      const body = await res.json();
      if (!body.available) {
        ul.appendChild(el("li", { class: "muted" }, "Sensor map not wired."));
        return;
      }
      if (!body.mappings.length) {
        ul.appendChild(el("li", { class: "muted" }, "No bindings yet."));
        return;
      }
      body.mappings.forEach((m) => ul.appendChild(renderMapping(m)));
    } catch (e) {
      ul.appendChild(el("li", { class: "muted" }, "Failed to load: " + e));
    }
  }

  function renderMapping(m) {
    const simBtn = el("button", { class: "primary" }, "Simulate trigger");
    simBtn.addEventListener("click", () => simulate(m.zone_id, m.sensor_type));

    const delBtn = el("button", {}, "Delete");
    delBtn.addEventListener("click", async () => {
      if (!confirm(`Remove binding for zone ${m.zone_id}?`)) return;
      const res = await fetch(`/api/sensors/map/${m.zone_id}`, { method: "DELETE" });
      if (res.ok) loadMappings();
    });

    return el("li", {},
      el("div", { class: "row" },
        el("span", { class: "kind" }, `zone ${m.zone_id}`),
        el("span", { class: "muted" }, ` · ${m.sensor_type || "?"}`),
        el("span", { class: "muted" }, ` → cam${m.camera_id} preset "${m.preset_name}"`),
        el("span", { class: "ts" }, simBtn, " ", delBtn),
      ),
      m.description
        ? el("pre", {}, m.description)
        : el("pre", { class: "muted" }, "(no description)"),
    );
  }

  async function addMapping() {
    const zoneId = parseInt(document.getElementById("sensor-zone-id").value, 10);
    const cameraId = parseInt(document.getElementById("sensor-camera").value, 10);
    const presetName = document.getElementById("sensor-preset").value;
    const sensorType = document.getElementById("sensor-type").value;
    const description = document.getElementById("sensor-description").value.trim();

    if (!Number.isFinite(zoneId) || zoneId <= 0) {
      alert("zone_id must be a positive integer");
      return;
    }
    if (!Number.isFinite(cameraId)) {
      alert("Pick a camera");
      return;
    }
    if (!presetName) {
      alert("Pick a preset");
      return;
    }
    const body = {
      zone_id: zoneId, camera_id: cameraId,
      preset_name: presetName, sensor_type: sensorType, description,
    };
    const res = await fetch("/api/sensors/map", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const reply = await res.json();
    if (!reply.ok) {
      alert("Save failed: " + (reply.error || res.status));
      return;
    }
    document.getElementById("sensor-zone-id").value = "";
    document.getElementById("sensor-description").value = "";
    loadMappings();
  }

  // ─── simulate + recent events ──────────────────────────────────

  async function simulate(zoneId, sensorType) {
    try {
      const res = await fetch(`/api/sensors/simulate/${zoneId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_type: sensorType || "doorContact" }),
      });
      const body = await res.json();
      if (!body.ok) {
        alert("Simulate failed: " + (body.error || res.status));
        return;
      }
      // Give the dispatcher a beat to produce an outcome, then refresh.
      setTimeout(loadRecentEvents, 1500);
      setTimeout(loadRecentEvents, 4000);
    } catch (e) {
      alert("Simulate failed: " + e);
    }
  }

  async function loadRecentEvents() {
    const ul = document.getElementById("sensor-events");
    try {
      const res = await fetch("/api/sensors/events/recent?limit=30");
      const body = await res.json();
      ul.innerHTML = "";
      if (!body.available) {
        ul.appendChild(el("li", { class: "muted" }, "Tap not wired."));
        return;
      }
      if (!body.events.length) {
        ul.appendChild(el("li", { class: "muted" }, "No sensor events yet."));
        return;
      }
      body.events.forEach((e) => ul.appendChild(renderEvent(e)));
    } catch (e) {
      ul.appendChild(el("li", { class: "muted" }, "Refresh failed: " + e));
    }
  }

  function renderEvent(e) {
    const verdict = (e.outcome && e.outcome.verdict) || "?";
    const verdictPillClass =
      verdict === "authorised" ? "pill pill-ok" :
      verdict === "intruder"   ? "pill pill-err" :
      verdict.startsWith("unverified") ? "pill pill-warn" :
      verdict === "cooldown"   ? "pill pill-idle" :
      "pill pill-warn";

    const when = new Date((e.received_ts || 0) * 1000).toLocaleTimeString();

    return el("li", {},
      el("div", { class: "row" },
        el("span", { class: "kind" }, `zone ${e.zone_id}`),
        el("span", { class: "muted" }, ` · ${e.event_type}`),
        el("span", { class: "muted" }, ` · src=${e.source}`),
        el("span", { class: verdictPillClass }, verdict),
        el("span", { class: "ts" }, when),
      ),
      el("pre", {}, JSON.stringify(e.outcome || {}, null, 2)),
    );
  }

  // ─── lifecycle ─────────────────────────────────────────────────

  function init() {
    document.getElementById("sensor-add").addEventListener("click", addMapping);
    document.getElementById("sensor-refresh").addEventListener("click", () => {
      loadMappings(); loadRecentEvents();
    });
    document.getElementById("sensor-camera").addEventListener("change", (evt) => {
      const camId = parseInt(evt.target.value, 10);
      if (Number.isFinite(camId)) loadPresetsFor(camId);
    });
  }

  function onShow() {
    loadCameras();
    loadMappings();
    loadRecentEvents();
    loadHubStatus();
    // Light auto-refresh so simulated triggers' outcomes appear
    // without a manual reload, and so the hub-connection pill flips
    // green/red as the listener reconnects in the background.
    if (pollHandle) clearInterval(pollHandle);
    pollHandle = setInterval(() => {
      loadRecentEvents();
      loadHubStatus();
    }, 3000);
  }

  function onHide() {
    if (pollHandle) { clearInterval(pollHandle); pollHandle = null; }
  }

  window.ITIPS = window.ITIPS || {};
  window.ITIPS.sensors = { init, onShow, onHide };
})();
