// Zone editor — capture a snapshot, click points to draw polygons,
// save them keyed to the active (camera, preset).

(function () {
  const ZONE_COLOURS = {
    intrusion: "#f85149",
    climbing: "#d29922",
    gate: "#e3b341",
    generator: "#bc8cff",
    default: "#7c3aed",
  };

  let state = null;
  const editor = {
    cameraId: null,
    presetId: null,
    image: null,         // HTMLImageElement (the snapshot, native pixels)
    frameW: 0,
    frameH: 0,
    canvasScale: 1,      // canvas pixel → frame pixel
    zones: {},           // {name: [[x, y], ...]} in frame-pixel coords
    drafting: false,
    draftPoints: [],     // canvas coords for in-progress polygon
  };

  function $(id) { return document.getElementById(id); }

  function setHint(msg) { $("zone-hint").textContent = msg; }
  function setStatus(msg, kind = "muted") {
    const el = $("zone-status");
    el.textContent = msg;
    el.className = kind;
  }

  function colourFor(name) {
    return ZONE_COLOURS[name] || ZONE_COLOURS.default;
  }

  // ── camera/preset selection ────────────────────────────────────
  function fillCameraSelect() {
    const sel = $("zone-camera");
    sel.innerHTML = "";
    state.cameras.forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c.camera_id;
      opt.textContent = `Camera ${c.camera_id}`;
      sel.appendChild(opt);
    });
    if (state.cameras.length) {
      editor.cameraId = state.cameras[0].camera_id;
      sel.value = String(editor.cameraId);
      fillPresetSelect();
    }
  }

  function fillPresetSelect() {
    const cam = state.cameras.find((c) => c.camera_id === editor.cameraId);
    const sel = $("zone-preset");
    sel.innerHTML = "";
    (cam ? cam.presets : ["default"]).forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p; opt.textContent = p;
      sel.appendChild(opt);
    });
    editor.presetId = cam ? cam.active_preset : "default";
    sel.value = editor.presetId;
  }

  // ── snapshot capture ───────────────────────────────────────────
  async function captureSnapshot() {
    setStatus("Capturing…");
    const url = `/api/snapshot/${editor.cameraId}?clean=1&_=${Date.now()}`;
    const img = new Image();
    img.crossOrigin = "anonymous";
    await new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = () => reject(new Error("snapshot fetch failed"));
      img.src = url;
    });
    editor.image = img;
    editor.frameW = img.naturalWidth;
    editor.frameH = img.naturalHeight;
    resizeCanvasToImage();
    await loadZones();
    redraw();
    setHint(`Click points on the frame. Click near the first point or press Finish to close a polygon.`);
    setStatus(`Captured ${editor.frameW}×${editor.frameH}`, "muted");
  }

  function resizeCanvasToImage() {
    const canvas = $("zone-canvas");
    const wrapWidth = canvas.parentElement.clientWidth - 24;  // padding
    const scale = wrapWidth / editor.frameW;
    canvas.width = wrapWidth;
    canvas.height = Math.round(editor.frameH * scale);
    editor.canvasScale = scale;
  }

  // ── zone fetch/save ────────────────────────────────────────────
  async function loadZones() {
    const res = await fetch(`/api/zones/${editor.cameraId}?preset_id=${encodeURIComponent(editor.presetId)}`);
    const body = await res.json();
    editor.zones = body.zones || {};
    renderZoneList();
  }

  async function saveZones() {
    if (editor.drafting) {
      setStatus("Finish or cancel the in-progress polygon first.", "pill pill-err");
      return;
    }
    setStatus("Saving…");
    const res = await fetch(`/api/zones/${editor.cameraId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preset_id: editor.presetId, zones: editor.zones }),
    });
    const body = await res.json();
    if (!body.ok) {
      setStatus("Save failed: " + (body.error || "unknown"), "pill pill-err");
      return;
    }
    const refMsg = body.reference_written
      ? "  (world-anchor reference stored)"
      : "";
    setStatus(`Saved ${body.zone_count} zone(s).${refMsg}`, "pill pill-ok");
  }

  // ── drawing ────────────────────────────────────────────────────
  function frameToCanvas(p) { return [p[0] * editor.canvasScale, p[1] * editor.canvasScale]; }
  function canvasToFrame(p) { return [p[0] / editor.canvasScale, p[1] / editor.canvasScale]; }

  function redraw() {
    const canvas = $("zone-canvas");
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    if (editor.image) ctx.drawImage(editor.image, 0, 0, canvas.width, canvas.height);

    for (const [name, polygon] of Object.entries(editor.zones)) {
      drawPolygon(ctx, polygon.map(frameToCanvas), name, colourFor(name), 0.18);
    }

    if (editor.drafting && editor.draftPoints.length) {
      drawPolygon(ctx, editor.draftPoints, "(drafting)", "#7c3aed", 0.10, true);
    }
  }

  function drawPolygon(ctx, points, label, colour, alpha, dashed = false) {
    if (!points.length) return;
    ctx.save();
    ctx.lineWidth = 2;
    ctx.strokeStyle = colour;
    ctx.setLineDash(dashed ? [6, 4] : []);
    ctx.fillStyle = colour + Math.round(alpha * 255).toString(16).padStart(2, "0");
    ctx.beginPath();
    ctx.moveTo(points[0][0], points[0][1]);
    for (let i = 1; i < points.length; i++) ctx.lineTo(points[i][0], points[i][1]);
    if (!dashed) ctx.closePath();
    ctx.stroke();
    if (!dashed) ctx.fill();

    points.forEach(([x, y]) => {
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fillStyle = colour;
      ctx.fill();
    });
    ctx.fillStyle = colour;
    ctx.font = "12px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.fillText(label, points[0][0] + 6, points[0][1] - 6);
    ctx.restore();
  }

  function renderZoneList() {
    const ul = $("zone-items");
    ul.innerHTML = "";
    const names = Object.keys(editor.zones).sort();
    if (!names.length) {
      const li = document.createElement("li");
      li.className = "muted";
      li.textContent = "(no polygons on this preset yet)";
      ul.appendChild(li);
      return;
    }
    names.forEach((name) => {
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.innerHTML = `<span style="color:${colourFor(name)}">●</span> ${name} <small class="muted">(${editor.zones[name].length} pts)</small>`;
      const del = document.createElement("button");
      del.textContent = "delete";
      del.addEventListener("click", () => {
        delete editor.zones[name];
        renderZoneList();
        redraw();
      });
      li.appendChild(label);
      li.appendChild(del);
      ul.appendChild(li);
    });
  }

  function startDraftIfNeeded() {
    if (!editor.drafting) {
      editor.drafting = true;
      editor.draftPoints = [];
      $("zone-finish").disabled = false;
      $("zone-cancel").disabled = false;
    }
  }

  function finishDraft() {
    if (!editor.drafting || editor.draftPoints.length < 3) {
      setStatus("Need at least 3 points to close a polygon.", "pill pill-err");
      return;
    }
    const name = ($("zone-name").value || "").trim() || `zone-${Object.keys(editor.zones).length + 1}`;
    const framePoints = editor.draftPoints.map(canvasToFrame).map(([x, y]) => [Math.round(x), Math.round(y)]);
    editor.zones[name] = framePoints;
    editor.drafting = false;
    editor.draftPoints = [];
    $("zone-name").value = "";
    $("zone-finish").disabled = true;
    $("zone-cancel").disabled = true;
    renderZoneList();
    redraw();
    setStatus(`Added '${name}'. Don't forget to Save.`, "muted");
  }

  function cancelDraft() {
    editor.drafting = false;
    editor.draftPoints = [];
    $("zone-finish").disabled = true;
    $("zone-cancel").disabled = true;
    redraw();
  }

  function init(externalState) {
    state = externalState;
    fillCameraSelect();

    $("zone-camera").addEventListener("change", (e) => {
      editor.cameraId = parseInt(e.target.value, 10);
      fillPresetSelect();
    });
    $("zone-preset").addEventListener("change", async (e) => {
      editor.presetId = e.target.value;
      if (editor.image) {
        await loadZones();
        redraw();
      }
    });
    $("zone-snapshot").addEventListener("click", () => captureSnapshot().catch((e) => setStatus(String(e), "pill pill-err")));
    $("zone-finish").addEventListener("click", finishDraft);
    $("zone-cancel").addEventListener("click", cancelDraft);
    $("zone-save").addEventListener("click", () => saveZones().catch((e) => setStatus(String(e), "pill pill-err")));

    const canvas = $("zone-canvas");
    canvas.addEventListener("click", (ev) => {
      if (!editor.image) return;
      const rect = canvas.getBoundingClientRect();
      const x = (ev.clientX - rect.left) * (canvas.width / rect.width);
      const y = (ev.clientY - rect.top) * (canvas.height / rect.height);
      startDraftIfNeeded();
      // close-on-near-first-point shortcut
      if (editor.draftPoints.length >= 3) {
        const [fx, fy] = editor.draftPoints[0];
        if (Math.hypot(fx - x, fy - y) < 10) {
          finishDraft();
          return;
        }
      }
      editor.draftPoints.push([x, y]);
      redraw();
    });

    window.addEventListener("resize", () => {
      if (editor.image) {
        resizeCanvasToImage();
        redraw();
      }
    });
  }

  window.ITIPS = window.ITIPS || {};
  window.ITIPS.zones = { init };
})();
