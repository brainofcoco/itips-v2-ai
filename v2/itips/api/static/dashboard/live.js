// Live tab — grid of MJPEG tiles with a preset switcher per camera.

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

  function buildTile(cam) {
    const select = el("select");
    cam.presets.forEach((p) => {
      const opt = el("option", { value: p }, p);
      if (p === cam.active_preset) opt.setAttribute("selected", "selected");
      select.appendChild(opt);
    });
    select.addEventListener("change", async () => {
      const target = select.value;
      select.disabled = true;
      try {
        const res = await fetch(`/api/cameras/${cam.camera_id}/preset`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ preset_id: target }),
        });
        const body = await res.json();
        if (!body.ok) {
          alert(body.error || "preset switch failed");
          select.value = cam.active_preset;
        } else {
          cam.active_preset = body.active_preset;
        }
      } catch (e) {
        alert("preset switch failed: " + e);
        select.value = cam.active_preset;
      } finally {
        select.disabled = false;
      }
    });

    const img = el("img", {
      src: `/video_feed/${cam.camera_id}`,
      alt: `Camera ${cam.camera_id}`,
    });

    return el("article", { class: "cam" },
      el("header", {},
        el("h2", {}, `Camera ${cam.camera_id}`),
        el("span", { class: cam.ptz_configured ? "pill pill-ok" : "pill pill-idle" },
          cam.ptz_connected ? "PTZ" : (cam.ptz_configured ? "VPTZ" : "fixed"))
      ),
      el("div", { class: "feed" }, img),
      el("div", { class: "controls" },
        el("label", { class: "muted" }, "Preset"),
        select,
      ),
    );
  }

  async function render(state) {
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
