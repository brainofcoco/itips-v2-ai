// Test Console — live event tap, camera quick-actions, PTZ jog,
// synthetic event injection, and an inbound-API form tester.

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

  let evtSource = null;

  // ── Live event tap ──────────────────────────────────────────────
  function openEventTap() {
    const list = document.getElementById("tap-list");
    const statusPill = document.getElementById("tap-status");
    closeEventTap();
    statusPill.textContent = "Connecting…";
    statusPill.className = "pill pill-idle";
    evtSource = new EventSource("/api/test/events/stream");
    evtSource.onopen = () => {
      statusPill.textContent = "Connected";
      statusPill.className = "pill pill-ok";
    };
    evtSource.onerror = () => {
      statusPill.textContent = "Disconnected";
      statusPill.className = "pill pill-err";
    };
    evtSource.onmessage = (msg) => {
      let body;
      try { body = JSON.parse(msg.data); } catch (e) { return; }
      list.appendChild(renderTap(body));
      // Cap visible rows so the DOM doesn't grow unbounded on a chatty site.
      while (list.children.length > 300) list.removeChild(list.firstChild);
      if (document.getElementById("tap-autoscroll").checked) {
        list.scrollTop = list.scrollHeight;
      }
    };
  }

  function closeEventTap() {
    if (evtSource) { evtSource.close(); evtSource = null; }
  }

  function renderTap(ev) {
    const ts = new Date((ev.ts || 0) * 1000).toLocaleTimeString();
    const codeClass = isInteresting(ev.code) ? "kind" : "muted";
    return el("li", {},
      el("div", { class: "row" },
        el("span", { class: codeClass }, `${ev.code}/${ev.action}`),
        el("span", { class: "muted" }, ` · cam${ev.camera_id}`),
        ev.has_jpeg ? el("span", { class: "pill pill-ok" }, "JPEG") : document.createTextNode(""),
        el("span", { class: "ts" }, ts),
      ),
      Object.keys(ev.data || {}).length
        ? el("pre", {}, JSON.stringify(ev.data, null, 1))
        : document.createTextNode(""),
    );
  }

  function isInteresting(code) {
    return [
      "FaceRecognition", "FaceDetection",
      "CrossLineDetection", "CrossRegionDetection", "WanderDetection",
      "TrafficCarMeasurement", "CarDrivingInOut",
      "FireDetection", "SmokeDetection",
    ].includes(code);
  }

  // ── Camera quick-actions ────────────────────────────────────────
  function renderCamActions(cameras) {
    const wrap = document.getElementById("cam-actions");
    wrap.innerHTML = "";
    cameras.forEach((cam) => {
      const snapshotBtn = el("button", {}, "Snapshot");
      snapshotBtn.addEventListener("click", () =>
        window.open(`/api/snapshot/${cam.camera_id}?t=${Date.now()}`, "_blank"));

      const fireBtn = el("button", { class: "primary" }, "Fire strobe + speaker");
      fireBtn.addEventListener("click", async () => {
        const res = await fetch(`/api/deterrence/${cam.camera_id}/fire`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ light: true, speaker: true }),
        });
        const body = await res.json();
        if (!body.ok) alert(body.error || "fire failed");
      });

      const standBtn = el("button", {}, "Stand down");
      standBtn.addEventListener("click", async () => {
        const res = await fetch(`/api/deterrence/${cam.camera_id}/standdown`, { method: "POST" });
        const body = await res.json();
        if (!body.ok) alert(body.error || "stand-down failed");
      });

      wrap.appendChild(el("div", { class: "cam-action-row" },
        el("strong", {}, `Camera ${cam.camera_id}`),
        el("span", { class: "muted" }, ` · ${cam.endpoint}`),
        snapshotBtn, fireBtn, standBtn,
      ));
    });
  }

  // ── PTZ jog pad ────────────────────────────────────────────────
  function bindJogPad() {
    const speedInput = document.getElementById("jog-speed");
    const camSelect = document.getElementById("jog-camera");
    document.querySelectorAll(".jog-pad button, [data-dir]").forEach((btn) => {
      const dir = btn.getAttribute("data-dir");
      if (!dir) return;
      // Press → start; release/leave → stop. Tap = quick start+stop.
      const start = async () => {
        const camId = camSelect.value;
        if (!camId) return;
        await fetch(`/api/test/ptz/${camId}/${dir}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: "start", speed: parseInt(speedInput.value, 10) || 4 }),
        });
      };
      const stop = async () => {
        const camId = camSelect.value;
        if (!camId) return;
        await fetch(`/api/test/ptz/${camId}/${dir}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: "stop" }),
        });
      };
      btn.addEventListener("mousedown", start);
      btn.addEventListener("mouseup", stop);
      btn.addEventListener("mouseleave", stop);
      btn.addEventListener("touchstart", (e) => { e.preventDefault(); start(); });
      btn.addEventListener("touchend", (e) => { e.preventDefault(); stop(); });
    });
  }

  // ── Event simulator ────────────────────────────────────────────
  function bindSimulator() {
    const camSelect = document.getElementById("sim-camera");
    const status = document.getElementById("sim-status");
    document.querySelectorAll("[data-sim]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const camId = parseInt(camSelect.value, 10);
        const eventType = btn.getAttribute("data-sim");
        status.textContent = `Firing ${eventType} on cam ${camId}…`;
        try {
          const res = await fetch(`/api/test/simulate/${eventType}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ camera_id: camId }),
          });
          const body = await res.json();
          status.textContent = body.ok
            ? `OK: ${eventType} injected on cam ${camId} — check Alerts/Incidents tabs.`
            : `Error: ${body.error || JSON.stringify(body)}`;
        } catch (e) {
          status.textContent = "Error: " + e;
        }
      });
    });
  }

  // ── Inbound API tester ─────────────────────────────────────────
  function setInboundResponse(body, status) {
    const node = document.getElementById("inbound-response");
    node.textContent = JSON.stringify({ status, body }, null, 2);
  }

  async function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const b64 = String(reader.result).split(",")[1] || "";
        resolve(b64);
      };
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }

  function bindInbound() {
    document.querySelectorAll("[data-inbound]").forEach((btn) => {
      btn.addEventListener("click", () => fireInbound(btn.getAttribute("data-inbound")));
    });
  }

  async function fireInbound(endpoint) {
    let payload = {};
    if (endpoint === "personnel/sync") {
      const action = document.getElementById("b1-action").value;
      const personId = document.getElementById("b1-person-id").value.trim();
      const fullName = document.getElementById("b1-full-name").value.trim();
      const file = document.getElementById("b1-image").files[0];
      payload = { action, person_id: personId, full_name: fullName };
      if (file) payload.image_b64 = await fileToBase64(file);
    } else if (endpoint === "maintenance/window") {
      payload = {
        action: document.getElementById("b3-action").value,
        window_id: document.getElementById("b3-window-id").value.trim(),
        person_id: document.getElementById("b3-person-id").value.trim(),
      };
    } else if (endpoint === "commands") {
      let parsed = {};
      try {
        const raw = document.getElementById("b4-params").value.trim();
        parsed = raw ? JSON.parse(raw) : {};
      } catch (e) {
        alert("parameters must be valid JSON");
        return;
      }
      payload = {
        command_type: document.getElementById("b4-command").value,
        parameters: {
          camera_id: parseInt(document.getElementById("b4-camera-id").value, 10),
          ...parsed,
        },
      };
    }
    try {
      const res = await fetch(`/api/test/inbound/${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await res.json();
      setInboundResponse(body.response, body.status);
    } catch (e) {
      setInboundResponse({ error: String(e) }, "fetch failed");
    }
  }

  // ── Boot ───────────────────────────────────────────────────────
  function populateCameraSelectors(cameras) {
    [document.getElementById("jog-camera"), document.getElementById("sim-camera")].forEach((sel) => {
      sel.innerHTML = "";
      cameras.forEach((cam) => {
        const opt = el("option", { value: String(cam.camera_id) }, `Camera ${cam.camera_id}`);
        sel.appendChild(opt);
      });
    });
  }

  function init(state) {
    renderCamActions(state.cameras || []);
    populateCameraSelectors(state.cameras || []);
    bindJogPad();
    bindSimulator();
    bindInbound();
    document.getElementById("tap-clear").addEventListener("click", () => {
      document.getElementById("tap-list").innerHTML = "";
    });
  }

  window.ITIPS = window.ITIPS || {};
  window.ITIPS.testing = {
    init,
    onShow: async () => {
      const state = window.ITIPS.state;
      state.cameras = await window.ITIPS.reloadCameras();
      renderCamActions(state.cameras);
      populateCameraSelectors(state.cameras);
      openEventTap();
    },
    onHide: () => closeEventTap(),
  };
})();
