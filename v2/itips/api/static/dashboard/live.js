// Live tab — grid of MJPEG tiles with deterrence buttons per camera.

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
      src: `/video_feed/${cam.camera_id}`,
      alt: `Camera ${cam.camera_id}`,
    });

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

  function render(state) {
    const grid = document.getElementById("camera-grid");
    grid.innerHTML = "";
    if (!state.cameras.length) {
      grid.appendChild(el("p", { class: "muted" }, "No cameras configured."));
      return;
    }
    state.cameras.forEach((cam) => grid.appendChild(buildTile(cam)));
  }

  function init(state) {
    render(state);
  }

  window.ITIPS = window.ITIPS || {};
  window.ITIPS.live = {
    init,
    onShow: async () => {
      const state = window.ITIPS.state;
      state.cameras = await window.ITIPS.reloadCameras();
      render(state);
    },
  };
})();
