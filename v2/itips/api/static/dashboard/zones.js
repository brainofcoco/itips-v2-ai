// Zones tab — draw polygon regions / lines that the BehaviorEngine
// evaluates on every motion event when a camera has no native IVS.
//
// Coordinates are stored normalised to [0, 1] in image space so the
// same zone survives a camera resolution change. The canvas renders
// the snapshot at its natural aspect, scaled to the panel width,
// and converts mouse position → image px → normalised on click.

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

  // Per-zone colour palette — purely visual, keeps it easy for an
  // operator to tell adjacent zones apart on a busy snapshot.
  const ZONE_COLORS = [
    "#7c3aed", "#22c55e", "#f59e0b", "#ef4444",
    "#14b8a6", "#ec4899", "#3b82f6", "#a855f7",
  ];

  const state = {
    cameraId: null,
    image: null,        // HTMLImageElement of the current snapshot
    zones: [],          // saved zones for the current camera
    drawing: [],        // in-progress polygon points in normalised coords
  };

  function statusMsg(msg, ok = true) {
    const node = document.getElementById("zone-status");
    node.textContent = msg;
    node.style.color = ok ? "var(--muted)" : "var(--err)";
  }

  // ─── canvas math ───────────────────────────────────────────────

  function canvas() { return document.getElementById("zone-canvas"); }
  function ctx() { return canvas().getContext("2d"); }

  function fitCanvasToImage() {
    const c = canvas();
    const img = state.image;
    if (!img) return;
    // Snap canvas pixel dimensions to the natural image so the
    // drawing API works in image-px. CSS scales it down to fit.
    c.width = img.naturalWidth || 640;
    c.height = img.naturalHeight || 360;
  }

  function normalisedFromEvent(evt) {
    const c = canvas();
    const rect = c.getBoundingClientRect();
    const px = (evt.clientX - rect.left) * (c.width / rect.width);
    const py = (evt.clientY - rect.top) * (c.height / rect.height);
    return [px / c.width, py / c.height];
  }

  function pxFromNormalised([nx, ny]) {
    const c = canvas();
    return [nx * c.width, ny * c.height];
  }

  // ─── draw pipeline ─────────────────────────────────────────────

  function redraw() {
    const c = canvas();
    const g = ctx();
    g.clearRect(0, 0, c.width, c.height);
    if (state.image) {
      g.drawImage(state.image, 0, 0, c.width, c.height);
    } else {
      g.fillStyle = "#1c2128";
      g.fillRect(0, 0, c.width, c.height);
    }
    state.zones.forEach((z, idx) => drawSavedZone(g, z, idx));
    drawInProgress(g);
  }

  function drawSavedZone(g, zone, idx) {
    const color = ZONE_COLORS[idx % ZONE_COLORS.length];
    if (zone.zone_type === "region") {
      drawPolygon(g, zone.points.map(pxFromNormalised), {
        stroke: color, fill: color + "33", close: true, width: 2,
      });
    } else if (zone.zone_type === "line") {
      drawPolyline(g, zone.points.map(pxFromNormalised), {
        stroke: color, width: 3, dashed: false,
      });
    }
    // Label the first vertex.
    if (zone.points.length > 0) {
      const [lx, ly] = pxFromNormalised(zone.points[0]);
      g.fillStyle = color;
      g.font = "12px ui-monospace, SFMono-Regular, Menlo, monospace";
      g.fillText(zone.name || zone.zone_id, lx + 6, ly - 6);
    }
  }

  function drawInProgress(g) {
    if (state.drawing.length === 0) return;
    const pts = state.drawing.map(pxFromNormalised);
    const wantRegion = document.getElementById("zone-type").value === "region";
    if (wantRegion && pts.length >= 3) {
      drawPolygon(g, pts, { stroke: "#ffffff", fill: "#ffffff22", close: true, width: 2, dashed: true });
    } else {
      drawPolyline(g, pts, { stroke: "#ffffff", width: 2, dashed: true });
    }
    // Dot every vertex so the operator sees what they've placed.
    g.fillStyle = "#ffffff";
    pts.forEach(([x, y]) => {
      g.beginPath();
      g.arc(x, y, 4, 0, Math.PI * 2);
      g.fill();
    });
  }

  function drawPolygon(g, pts, opts) {
    g.lineWidth = opts.width || 2;
    g.strokeStyle = opts.stroke;
    if (opts.dashed) g.setLineDash([6, 4]); else g.setLineDash([]);
    g.beginPath();
    pts.forEach(([x, y], i) => (i === 0 ? g.moveTo(x, y) : g.lineTo(x, y)));
    if (opts.close) g.closePath();
    if (opts.fill) {
      g.fillStyle = opts.fill;
      g.fill();
    }
    g.stroke();
    g.setLineDash([]);
  }

  function drawPolyline(g, pts, opts) {
    g.lineWidth = opts.width || 2;
    g.strokeStyle = opts.stroke;
    if (opts.dashed) g.setLineDash([6, 4]); else g.setLineDash([]);
    g.beginPath();
    pts.forEach(([x, y], i) => (i === 0 ? g.moveTo(x, y) : g.lineTo(x, y)));
    g.stroke();
    g.setLineDash([]);
  }

  // ─── data loading ──────────────────────────────────────────────

  async function loadCameras() {
    const sel = document.getElementById("zone-camera");
    sel.innerHTML = "";
    try {
      const res = await fetch("/api/cameras");
      const body = await res.json();
      (body.cameras || []).forEach((cam) => {
        sel.appendChild(el("option", { value: String(cam.camera_id) }, `cam${cam.camera_id} · ${cam.endpoint}`));
      });
      if (!sel.options.length) {
        sel.appendChild(el("option", { value: "" }, "(no cameras configured)"));
      }
    } catch (e) {
      sel.appendChild(el("option", { value: "" }, "(failed to load)"));
    }
  }

  async function loadSnapshot(camId) {
    state.image = null;
    state.zones = [];
    state.drawing = [];
    redraw();
    const hint = document.getElementById("zone-hint");
    hint.textContent = "Loading snapshot…";
    try {
      const img = new Image();
      // Cache-bust so refreshing the tab fetches a fresh frame.
      img.src = `/api/snapshot/${camId}?t=${Date.now()}`;
      await new Promise((resolve, reject) => {
        img.onload = resolve;
        img.onerror = () => reject(new Error("snapshot failed"));
      });
      state.image = img;
      fitCanvasToImage();
      hint.textContent = `Image ${img.naturalWidth} × ${img.naturalHeight}. Click to add points.`;
    } catch (e) {
      hint.textContent = `No snapshot available — ${e.message}. You can still draw on a blank canvas.`;
    }
    redraw();
  }

  async function loadZones(camId) {
    try {
      const res = await fetch(`/api/zones/${camId}`);
      const body = await res.json();
      if (!body.available) {
        statusMsg("ML zone store not wired — zones disabled.", false);
        state.zones = [];
      } else {
        state.zones = body.zones || [];
      }
    } catch (e) {
      statusMsg("Failed to load zones: " + e, false);
      state.zones = [];
    }
    renderExistingList();
    redraw();
  }

  async function loadEngineStatus() {
    const pill = document.getElementById("zone-engine-pill");
    try {
      const res = await fetch("/api/health/capabilities");
      const body = await res.json();
      if (!body.available) {
        pill.className = "pill pill-idle";
        pill.textContent = "engine: not wired";
        return;
      }
      if (body.behavior_engine_ready) {
        pill.className = "pill pill-ok";
        pill.textContent = "behavior engine: ready";
      } else {
        pill.className = "pill pill-warn";
        pill.textContent = "behavior engine: warming up";
      }
    } catch (e) {
      pill.className = "pill pill-idle";
      pill.textContent = "engine: unknown";
    }
  }

  // ─── list rendering ────────────────────────────────────────────

  function renderExistingList() {
    const ul = document.getElementById("zone-existing");
    ul.innerHTML = "";
    if (!state.zones.length) {
      ul.appendChild(el("li", { class: "muted" }, "No zones yet for this camera."));
      return;
    }
    state.zones.forEach((z, idx) => {
      const dot = el("span", { class: "pill" }, z.zone_type);
      dot.style.background = ZONE_COLORS[idx % ZONE_COLORS.length] + "33";
      dot.style.color = ZONE_COLORS[idx % ZONE_COLORS.length];

      const delBtn = el("button", {}, "Delete");
      delBtn.addEventListener("click", () => deleteZone(z.zone_id));

      ul.appendChild(el("li", {},
        el("div", {},
          el("strong", {}, z.zone_id),
          z.name ? el("div", { class: "muted" }, z.name) : el("span"),
          el("div", { class: "muted" },
            `${z.points.length} pts · ${z.zone_type === "line" ? z.direction : "region"}`),
        ),
        el("div", {}, dot, " ", delBtn),
      ));
    });
  }

  // ─── interactions ──────────────────────────────────────────────

  function onCanvasClick(evt) {
    if (!state.cameraId) return;
    const [nx, ny] = normalisedFromEvent(evt);
    state.drawing.push([nx, ny]);
    redraw();
  }

  function clearDrawing() {
    state.drawing = [];
    redraw();
  }

  async function saveZone() {
    if (!state.cameraId) {
      statusMsg("Pick a camera first.", false);
      return;
    }
    const zoneId = document.getElementById("zone-id").value.trim();
    if (!zoneId) {
      statusMsg("zone id is required.", false);
      return;
    }
    const zoneType = document.getElementById("zone-type").value;
    const minPoints = zoneType === "region" ? 3 : 2;
    if (state.drawing.length < minPoints) {
      statusMsg(`${zoneType} needs at least ${minPoints} points (have ${state.drawing.length}).`, false);
      return;
    }
    const payload = {
      zone_id: zoneId,
      zone_type: zoneType,
      points: state.drawing,
      name: document.getElementById("zone-name").value.trim(),
      direction: zoneType === "line"
        ? document.getElementById("zone-direction").value
        : "Any",
    };
    try {
      const res = await fetch(`/api/zones/${state.cameraId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await res.json();
      if (!body.ok) {
        statusMsg("Save failed: " + (body.error || "unknown"), false);
        return;
      }
      statusMsg(`Saved zone ${zoneId}.`);
      state.drawing = [];
      document.getElementById("zone-id").value = "";
      document.getElementById("zone-name").value = "";
      await loadZones(state.cameraId);
    } catch (e) {
      statusMsg("Save failed: " + e, false);
    }
  }

  async function deleteZone(zoneId) {
    if (!confirm(`Delete zone "${zoneId}"?`)) return;
    try {
      const res = await fetch(
        `/api/zones/${state.cameraId}/${encodeURIComponent(zoneId)}`,
        { method: "DELETE" },
      );
      const body = await res.json();
      if (!body.ok) {
        statusMsg("Delete failed: " + (body.error || "unknown"), false);
        return;
      }
      statusMsg(`Deleted ${zoneId}.`);
      await loadZones(state.cameraId);
    } catch (e) {
      statusMsg("Delete failed: " + e, false);
    }
  }

  function onCameraChange() {
    const sel = document.getElementById("zone-camera");
    state.cameraId = sel.value ? Number(sel.value) : null;
    state.drawing = [];
    if (state.cameraId == null) return;
    loadSnapshot(state.cameraId);
    loadZones(state.cameraId);
  }

  function onZoneTypeChange() {
    const t = document.getElementById("zone-type").value;
    document.getElementById("zone-direction").disabled = t !== "line";
    redraw();
  }

  // ─── init / lifecycle ──────────────────────────────────────────

  function init() {
    document.getElementById("zone-camera").addEventListener("change", onCameraChange);
    document.getElementById("zone-type").addEventListener("change", onZoneTypeChange);
    document.getElementById("zone-save").addEventListener("click", saveZone);
    document.getElementById("zone-clear").addEventListener("click", clearDrawing);
    document.getElementById("zone-refresh").addEventListener("click", () => {
      if (state.cameraId != null) {
        loadSnapshot(state.cameraId);
        loadZones(state.cameraId);
      }
      loadEngineStatus();
    });
    canvas().addEventListener("click", onCanvasClick);
  }

  async function onShow() {
    await loadCameras();
    // Auto-select the first camera so the user lands on a usable view.
    const sel = document.getElementById("zone-camera");
    if (sel.options.length && !state.cameraId) {
      sel.selectedIndex = 0;
      onCameraChange();
    }
    loadEngineStatus();
  }

  window.ITIPS = window.ITIPS || {};
  window.ITIPS.zones = { init, onShow };
})();
