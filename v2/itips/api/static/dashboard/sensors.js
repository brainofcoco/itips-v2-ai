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

  // ─── calibration wizard ───────────────────────────────────────
  // Atomic save-preset + bind-sensor flow. The operator picks a zone,
  // jogs the camera until the sensor's physical location is in
  // frame, then Save & bind writes a Dahua preset *and* the
  // SensorMap binding in one click. Test pan verifies the chain by
  // panning away then back to the just-saved preset.

  let calibState = { zones: [], mappings: {}, currentZone: null };

  async function calibLoadZones() {
    const sel = document.getElementById("calib-zone");
    if (!sel) return;
    sel.innerHTML = "";
    let zones = [];
    let mappings = {};
    // Pull live hub zones if available; fall back to whatever's in the
    // SensorMap so the wizard still works without a hub connection.
    try {
      const res = await fetch("/api/sensors/listener/zones");
      const body = await res.json();
      const list = (body.raw && body.raw.ZoneList) || [];
      zones = list.map((entry) => entry.Zone || {})
                  .filter((z) => z && Number.isFinite(z.id));
    } catch (e) {
      zones = [];
    }
    try {
      const res = await fetch("/api/sensors/map");
      const body = await res.json();
      (body.mappings || []).forEach((m) => { mappings[m.zone_id] = m; });
    } catch (e) { /* no-op */ }
    // If the hub didn't give us zones, synthesize from existing bindings.
    if (!zones.length) {
      zones = Object.values(mappings).map((m) => ({
        id: m.zone_id,
        name: `zone ${m.zone_id}`,
        detectorType: m.sensor_type,
      }));
    }
    calibState.zones = zones;
    calibState.mappings = mappings;

    sel.appendChild(el("option", { value: "" }, "— pick a zone —"));
    zones.sort((a, b) => a.id - b.id).forEach((z) => {
      const bound = mappings[z.id];
      const label = `zone ${z.id} · ${z.name || "?"}` +
        (bound ? ` (bound → cam${bound.camera_id})` : " · UNBOUND");
      sel.appendChild(el("option", { value: String(z.id) }, label));
    });
    if (!zones.length) {
      sel.appendChild(el("option", { value: "" }, "(no zones — hub offline?)"));
    }
  }

  async function calibLoadCameras() {
    const sel = document.getElementById("calib-camera");
    if (!sel || sel.options.length > 0) return;
    sel.innerHTML = "";
    try {
      const res = await fetch("/api/cameras");
      const body = await res.json();
      (body.cameras || []).forEach((c) => sel.appendChild(
        el("option", { value: String(c.camera_id) }, `cam${c.camera_id}`)));
    } catch (e) {
      sel.appendChild(el("option", { value: "" }, "(load failed)"));
    }
  }

  function calibSetFeed(cameraId) {
    const img = document.getElementById("calib-feed-img");
    const hint = document.getElementById("calib-feed-hint");
    if (!img) return;
    if (!cameraId) {
      img.removeAttribute("src");
      hint.textContent = "select a camera to preview";
      hint.style.display = "";
      return;
    }
    // MJPEG stream — the live feed served by /live/<id> is a long-lived
    // multipart-mixed response, so the <img> just paints frames as
    // they arrive. Add a cache-buster so a previous frozen frame
    // doesn't linger.
    img.src = `/live/${cameraId}?t=${Date.now()}`;
    hint.style.display = "none";
  }

  function calibSetStatus(text, cls) {
    const pill = document.getElementById("calib-status");
    if (!pill) return;
    pill.textContent = text;
    pill.className = "pill " + (cls || "pill-idle");
  }

  function calibRenderBinding(zoneId) {
    const el2 = document.getElementById("calib-binding");
    if (!el2) return;
    const m = calibState.mappings[zoneId];
    if (!m) {
      el2.textContent = "no binding";
      el2.className = "muted";
      return;
    }
    el2.textContent = `bound → cam${m.camera_id} preset "${m.preset_name}"` +
      (m.description ? ` · ${m.description}` : "");
    el2.className = "";
  }

  function calibCurrentZoneId() {
    const sel = document.getElementById("calib-zone");
    if (!sel || !sel.value) return null;
    const id = parseInt(sel.value, 10);
    return Number.isFinite(id) ? id : null;
  }
  function calibCurrentCameraId() {
    const sel = document.getElementById("calib-camera");
    if (!sel || !sel.value) return null;
    const id = parseInt(sel.value, 10);
    return Number.isFinite(id) ? id : null;
  }

  function bindCalibrationJog() {
    const speedInput = document.getElementById("calib-speed");
    document.querySelectorAll("[data-calib-dir]").forEach((btn) => {
      if (btn.dataset.calibJogBound === "1") return;
      btn.dataset.calibJogBound = "1";
      const dir = btn.getAttribute("data-calib-dir");
      let pressed = false;
      const call = async (action) => {
        const camId = calibCurrentCameraId();
        if (!camId) return;
        const body = action === "stop"
          ? { action: "stop" }
          : { action: "start", speed: parseInt(speedInput.value, 10) || 4 };
        try {
          const res = await fetch(`/api/test/ptz/${camId}/${dir}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          if (!res.ok) {
            const reply = await res.json().catch(() => ({}));
            calibSetStatus(`jog ${dir} ${action}: ${reply.error || res.status}`, "pill-err");
          }
        } catch (e) { /* swallow — jog noise */ }
      };
      const start = (e) => {
        if (e && e.cancelable) e.preventDefault();
        if (pressed) return;
        pressed = true;
        call("start");
      };
      const stop = (e) => {
        if (e && e.cancelable) e.preventDefault();
        if (!pressed) return;
        pressed = false;
        call("stop");
      };
      btn.addEventListener("mousedown", start);
      btn.addEventListener("mouseup", stop);
      btn.addEventListener("mouseleave", stop);
      btn.addEventListener("touchstart", start, { passive: false });
      btn.addEventListener("touchend", stop, { passive: false });
      btn.addEventListener("touchcancel", stop, { passive: false });
    });
  }

  async function calibSaveAndBind() {
    const zoneId = calibCurrentZoneId();
    const cameraId = calibCurrentCameraId();
    if (zoneId === null) {
      calibSetStatus("pick a zone first", "pill-err");
      return;
    }
    if (cameraId === null) {
      calibSetStatus("pick a camera first", "pill-err");
      return;
    }
    const name = document.getElementById("calib-name").value.trim();
    const description = document.getElementById("calib-description").value.trim();
    const sensorType = document.getElementById("calib-sensor-type").value;
    calibSetStatus("saving preset + binding…", "pill-idle");
    try {
      const res = await fetch(`/api/sensors/zones/${zoneId}/calibrate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          camera_id: cameraId,
          name: name || undefined,
          sensor_type: sensorType || undefined,
          description: description || undefined,
        }),
      });
      const body = await res.json();
      if (!body.ok) {
        calibSetStatus(`save failed: ${body.error || res.status}`, "pill-err");
        return;
      }
      calibSetStatus(
        `bound zone ${zoneId} → cam${cameraId} preset "${body.preset.name}" (#${body.preset.index})`,
        "pill-ok"
      );
      // Refresh state — both the wizard dropdown labels and the
      // mappings list below — so the wizard reflects truth.
      await calibLoadZones();
      await loadMappings();
      // Re-select the zone we just bound.
      document.getElementById("calib-zone").value = String(zoneId);
      calibRenderBinding(zoneId);
    } catch (e) {
      calibSetStatus(`save failed: ${e}`, "pill-err");
    }
  }

  async function calibTestPan() {
    const zoneId = calibCurrentZoneId();
    if (zoneId === null) {
      calibSetStatus("pick a zone first", "pill-err");
      return;
    }
    calibSetStatus("test pan in progress…", "pill-idle");
    try {
      const res = await fetch(`/api/sensors/zones/${zoneId}/test-pan`, {
        method: "POST",
      });
      const body = await res.json();
      if (!body.ok) {
        calibSetStatus(`test pan failed: ${body.error || res.status}`, "pill-err");
        return;
      }
      calibSetStatus(`test pan OK — camera moved to "${body.mapping.preset_name}"`,
                     "pill-ok");
      // Reload the live feed so the snapshot frame the test produced
      // shows up (the MJPEG stream may have stalled during the pan).
      calibSetFeed(body.mapping.camera_id);
    } catch (e) {
      calibSetStatus(`test pan failed: ${e}`, "pill-err");
    }
  }

  async function calibUnbind() {
    const zoneId = calibCurrentZoneId();
    if (zoneId === null) return;
    if (!confirm(`Remove binding for zone ${zoneId}? The camera preset stays in place — only the binding is removed.`)) return;
    try {
      const res = await fetch(`/api/sensors/map/${zoneId}`, { method: "DELETE" });
      const body = await res.json();
      if (!body.ok) {
        calibSetStatus(`unbind failed: ${body.error || res.status}`, "pill-err");
        return;
      }
      calibSetStatus(`unbound zone ${zoneId}`, "pill-ok");
      await calibLoadZones();
      await loadMappings();
      calibRenderBinding(zoneId);
    } catch (e) {
      calibSetStatus(`unbind failed: ${e}`, "pill-err");
    }
  }

  function bindCalibrationWizard() {
    const zoneSel = document.getElementById("calib-zone");
    const camSel = document.getElementById("calib-camera");
    if (!zoneSel) return;
    zoneSel.addEventListener("change", () => {
      const zid = calibCurrentZoneId();
      calibRenderBinding(zid);
      if (zid !== null) {
        // Auto-jump to the bound camera + name so the operator can
        // adjust an existing binding instead of starting from scratch.
        const m = calibState.mappings[zid];
        if (m) {
          camSel.value = String(m.camera_id);
          document.getElementById("calib-name").value = m.preset_name;
          document.getElementById("calib-description").value = m.description || "";
          document.getElementById("calib-sensor-type").value = m.sensor_type || "";
          calibSetFeed(m.camera_id);
          calibSetStatus(`zone ${zid} already bound — adjust and re-Save to update`, "pill-warn");
        } else {
          document.getElementById("calib-name").value = "";
          document.getElementById("calib-description").value = "";
          document.getElementById("calib-sensor-type").value = "";
          calibSetStatus(`zone ${zid} — drive the camera, then Save & bind`, "pill-idle");
        }
      } else {
        calibSetStatus("pick a zone", "pill-idle");
      }
    });
    camSel.addEventListener("change", () => calibSetFeed(calibCurrentCameraId()));
    document.getElementById("calib-refresh-zones").addEventListener("click", calibLoadZones);
    document.getElementById("calib-save").addEventListener("click", calibSaveAndBind);
    document.getElementById("calib-test").addEventListener("click", calibTestPan);
    document.getElementById("calib-unbind").addEventListener("click", calibUnbind);
    bindCalibrationJog();
  }

  // ─── lifecycle ─────────────────────────────────────────────────

  // ─── siren panel ───────────────────────────────────────────────
  // Manual control of the hub's panic-alarm sirens. Disable the
  // buttons during a request so a double-click can't fire two POSTs
  // in flight at the same time.

  function bindSirenPanel() {
    const soundBtn = document.getElementById("siren-sound");
    const stopBtn = document.getElementById("siren-silence");
    const testBtn = document.getElementById("siren-test");
    const statusEl = document.getElementById("siren-status");
    if (!soundBtn || !stopBtn || !testBtn) return;
    const subInput = document.getElementById("siren-subid");

    const setBusy = (busy, label) => {
      [soundBtn, stopBtn, testBtn].forEach((b) => { b.disabled = !!busy; });
      if (busy) {
        statusEl.textContent = label || "working…";
        statusEl.className = "pill pill-idle";
      }
    };
    const renderResult = (label, body, cls) => {
      statusEl.textContent = label;
      statusEl.className = "pill " + (cls || "pill-ok");
      if (body && !body.ok) console.warn("siren response:", body);
    };
    const callSiren = async (action, label, extraBody = {}) => {
      const subId = parseInt(subInput.value, 10) || 1;
      setBusy(true, `${label}…`);
      try {
        const res = await fetch(`/api/sensors/hub/siren/${action}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sub_id: subId, ...extraBody }),
        });
        const body = await res.json().catch(() => ({}));
        if (!body.ok) {
          renderResult(`${label}: ${body.error || `HTTP ${res.status}`}`, body, "pill-err");
        } else {
          renderResult(`${label}: ok`, body, "pill-ok");
        }
      } catch (e) {
        renderResult(`${label}: ${e}`, null, "pill-err");
      } finally {
        setBusy(false);
      }
    };

    soundBtn.addEventListener("click", () => {
      if (!confirm("Sound the hub siren now? This triggers a real 100+ dB alarm "
                   + "at the site. Use Test for a 2-second burst.")) return;
      callSiren("start", "SOUNDING");
    });
    stopBtn.addEventListener("click", () => callSiren("stop", "Silence"));
    testBtn.addEventListener("click", () => callSiren("test", "Test", { duration_s: 2.0 }));
  }

  async function loadSirenStatus() {
    const statusEl = document.getElementById("siren-status");
    if (!statusEl) return;
    try {
      const res = await fetch("/api/sensors/hub/siren/status");
      const body = await res.json();
      if (!body.ok || !body.sirens || !body.sirens.length) {
        statusEl.textContent = "siren: none paired";
        statusEl.className = "pill pill-idle";
        return;
      }
      // Summarise: how many are online vs offline.
      const total = body.sirens.length;
      const online = body.sirens.filter((s) => s.status === "online").length;
      const tamper = body.sirens.some((s) => s.tamperEvident);
      let cls = "pill-ok", text = `siren: ${online}/${total} online`;
      if (online === 0) { cls = "pill-err"; text = "siren: all offline"; }
      else if (online < total) { cls = "pill-idle"; text += " (degraded)"; }
      if (tamper) { cls = "pill-err"; text += " · TAMPER"; }
      statusEl.textContent = text;
      statusEl.className = "pill " + cls;
    } catch (e) {
      statusEl.textContent = "siren: status error";
      statusEl.className = "pill pill-err";
    }
  }

  function init() {
    document.getElementById("sensor-add").addEventListener("click", addMapping);
    document.getElementById("sensor-refresh").addEventListener("click", () => {
      loadMappings(); loadRecentEvents();
    });
    document.getElementById("sensor-camera").addEventListener("change", (evt) => {
      const camId = parseInt(evt.target.value, 10);
      if (Number.isFinite(camId)) loadPresetsFor(camId);
    });
    bindSirenPanel();
    bindCalibrationWizard();
  }

  function onShow() {
    loadCameras();
    loadMappings();
    loadRecentEvents();
    loadHubStatus();
    loadSirenStatus();
    calibLoadCameras();
    calibLoadZones();
    // Light auto-refresh so simulated triggers' outcomes appear
    // without a manual reload, and so the hub-connection pill flips
    // green/red as the listener reconnects in the background.
    if (pollHandle) clearInterval(pollHandle);
    pollHandle = setInterval(() => {
      loadRecentEvents();
      loadHubStatus();
      loadSirenStatus();
    }, 3000);
  }

  function onHide() {
    if (pollHandle) { clearInterval(pollHandle); pollHandle = null; }
  }

  window.ITIPS = window.ITIPS || {};
  window.ITIPS.sensors = { init, onShow, onHide };
})();
