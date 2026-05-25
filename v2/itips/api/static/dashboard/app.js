// Dashboard shell: status banner + tab routing + bootstrap each tab module.
//
// Each tab module exposes init() and optionally onShow(). Modules are
// loaded as plain <script> via dynamic insertion to avoid a build step.

(function () {
  const state = {
    cameras: [],
    site: null,
    activeTab: "live",
  };

  async function loadStatus() {
    try {
      const res = await fetch("/status");
      const body = await res.json();
      state.site = body;
      const label = body.site_id
        ? `site ${body.site_id} · device ${body.device_id || "—"} · ${body.mode}`
        : `mode ${body.mode}`;
      document.getElementById("site-label").textContent = label;
    } catch (e) {
      document.getElementById("site-label").textContent = "(status unavailable)";
    }
  }

  async function loadCameras() {
    const res = await fetch("/api/cameras");
    const body = await res.json();
    state.cameras = body.cameras || [];
    return state.cameras;
  }

  function activateTab(name) {
    const prev = state.activeTab;
    state.activeTab = name;
    document.querySelectorAll(".tabs button").forEach((b) => {
      b.classList.toggle("active", b.dataset.tab === name);
    });
    document.querySelectorAll("[data-tabpanel]").forEach((p) => {
      p.classList.toggle("active", p.dataset.tabpanel === name);
    });
    if (prev && prev !== name && window.ITIPS && window.ITIPS[prev] && window.ITIPS[prev].onHide) {
      window.ITIPS[prev].onHide();
    }
    if (window.ITIPS && window.ITIPS[name] && window.ITIPS[name].onShow) {
      window.ITIPS[name].onShow();
    }
  }

  document.querySelectorAll(".tabs button").forEach((b) => {
    b.addEventListener("click", () => activateTab(b.dataset.tab));
  });

  async function boot() {
    window.ITIPS = window.ITIPS || {};
    await loadStatus();
    await loadCameras();
    window.ITIPS.state = state;
    window.ITIPS.reloadCameras = loadCameras;

    if (window.ITIPS.live) window.ITIPS.live.init(state);
    if (window.ITIPS.workers) window.ITIPS.workers.init(state);
    if (window.ITIPS.plates) window.ITIPS.plates.init(state);
    if (window.ITIPS.alerts) window.ITIPS.alerts.init(state);
    if (window.ITIPS.incidents) window.ITIPS.incidents.init(state);
    if (window.ITIPS.health) window.ITIPS.health.init(state);
    if (window.ITIPS.zones) window.ITIPS.zones.init(state);
    if (window.ITIPS.testing) window.ITIPS.testing.init(state);
    activateTab("live");
  }

  const modules = ["live.js", "workers.js", "plates.js", "alerts.js", "incidents.js", "health.js", "zones.js", "testing.js"];
  let loaded = 0;
  modules.forEach((src) => {
    const s = document.createElement("script");
    s.src = `/dashboard/${src}`;
    s.onload = () => {
      loaded += 1;
      if (loaded === modules.length) boot();
    };
    s.onerror = () => console.error("Failed to load", src);
    document.body.appendChild(s);
  });
})();
