// Live tab — MJPEG tiles with lazy connect.
//
// Browsers cap parallel connections per host at 6. Every <img src="/video_feed/N">
// holds one of those slots open until the connection closes — which never
// happens for MJPEG until we explicitly clear src. So we:
//   * leave src="" until the Live tab is actually shown,
//   * clear src when the Live tab is hidden (onHide),
//   * also tear down when the page is hidden via the Visibility API.

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

  const tileImgs = [];   // refs so we can attach + clear src in batch

  async function fire(cameraId, btn) {
    btn.disabled = true;
    try {
      const res = await fetch(`/api/deterrence/${cameraId}/fire`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ light: true, speaker: true }),
      });
      const body = await res.json();
      if (!body.ok) alert(body.error || "deterrence fire failed");
    } catch (e) {
      alert("deterrence fire failed: " + e);
    } finally {
      btn.disabled = false;
    }
  }

  async function standdown(cameraId, btn) {
    btn.disabled = true;
    try {
      const res = await fetch(`/api/deterrence/${cameraId}/standdown`, { method: "POST" });
      const body = await res.json();
      if (!body.ok) alert(body.error || "stand-down failed");
    } catch (e) {
      alert("stand-down failed: " + e);
    } finally {
      btn.disabled = false;
    }
  }

  function buildTile(cam) {
    const img = el("img", {
      // Note: src is set in attachStreams(), not here. Empty src = no connection.
      alt: `Camera ${cam.camera_id}`,
      "data-cam": String(cam.camera_id),
    });
    // Auto-retry once on a dropped stream. The camera occasionally closes
    // MJPEG (heavy main-stream load, brief Wi-Fi blip) — without a retry
    // the tile stays dark forever.
    let retried = false;
    img.addEventListener("error", () => {
      if (retried) return;
      retried = true;
      setTimeout(() => {
        if (window.ITIPS && window.ITIPS.state && window.ITIPS.state.activeTab === "live") {
          img.src = `/live/${cam.camera_id}?subtype=1&t=${Date.now()}`;
        }
      }, 1500);
    });
    tileImgs.push(img);

    const fireBtn = el("button", { class: "primary" }, "Test deterrence");
    fireBtn.addEventListener("click", () => fire(cam.camera_id, fireBtn));

    const standBtn = el("button", {}, "Stand down");
    standBtn.addEventListener("click", () => standdown(cam.camera_id, standBtn));

    const groupLabel = cam.workers_group_id
      ? `face-group ${cam.workers_group_id}`
      : "no face-group";

    return el("article", { class: "cam" },
      el("header", {},
        el("h2", {}, `Camera ${cam.camera_id} · ${cam.endpoint}`),
        el("span", { class: "pill pill-ok" }, groupLabel),
      ),
      el("div", { class: "feed" }, img),
      el("div", { class: "controls" },
        fireBtn,
        standBtn,
        el("span", { class: cam.ptz_connected ? "pill pill-ok" : "pill pill-idle" },
          cam.ptz_connected ? "PTZ ready" : "PTZ idle"),
      ),
    );
  }

  function attachStreams() {
    const ts = Date.now();
    tileImgs.forEach((img) => {
      const camId = img.getAttribute("data-cam");
      if (!camId) return;
      // Continuous MJPEG from the camera via /live/{id}. Auto-falls back
      // from main → substream in the proxy if main isn't MJPEG. Substream
      // by default keeps bandwidth modest on the dashboard view.
      img.src = `/live/${camId}?subtype=1&t=${ts}`;
    });
  }

  function detachStreams() {
    tileImgs.forEach((img) => { img.removeAttribute("src"); });
  }

  function render(state) {
    const grid = document.getElementById("camera-grid");
    grid.innerHTML = "";
    tileImgs.length = 0;
    if (!state.cameras.length) {
      grid.appendChild(el("p", { class: "muted" }, "No cameras configured."));
      return;
    }
    state.cameras.forEach((cam) => grid.appendChild(buildTile(cam)));
  }

  function init(state) {
    render(state);
    // If the page is hidden mid-session, free the slots.
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) detachStreams();
      else if (window.ITIPS && window.ITIPS.state && window.ITIPS.state.activeTab === "live") {
        attachStreams();
      }
    });
  }

  window.ITIPS = window.ITIPS || {};
  window.ITIPS.live = {
    init,
    onShow: async () => {
      const state = window.ITIPS.state;
      state.cameras = await window.ITIPS.reloadCameras();
      render(state);
      attachStreams();
    },
    onHide: () => detachStreams(),
  };
})();
