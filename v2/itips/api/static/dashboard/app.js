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

  // Topbar AX PRO hub pill — visible from every tab. Lives in the
  // shell rather than the Sensors tab so an operator who's looking at
  // Live or Alerts still sees "hub disconnected" the moment it happens.
  async function pollHubPill() {
    const pill = document.getElementById("topbar-hub-pill");
    if (!pill) return;
    try {
      const res = await fetch("/api/sensors/listener/status");
      const body = await res.json();
      if (!body.wired) {
        pill.className = "pill pill-idle";
        pill.textContent = "hub: not wired";
        pill.title = body.reason || "ITIPS_AXPRO_HOST is not set";
        return;
      }
      const hostLine = `host=${body.host}` +
        (body.last_error ? ` · last_error=${body.last_error}` : "");
      if (body.connected) {
        pill.className = body.armed ? "pill pill-ok" : "pill pill-warn";
        pill.textContent = body.armed ? "hub: armed" : "hub: disarmed";
      } else {
        pill.className = "pill pill-err";
        pill.textContent = "hub: disconnected";
      }
      pill.title = hostLine;
    } catch (e) {
      pill.className = "pill pill-err";
      pill.textContent = "hub: ?";
      pill.title = String(e);
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
    pollHubPill();
    setInterval(pollHubPill, 5000);

    if (window.ITIPS.live) window.ITIPS.live.init(state);
    if (window.ITIPS.workers) window.ITIPS.workers.init(state);
    if (window.ITIPS.plates) window.ITIPS.plates.init(state);
    if (window.ITIPS.alerts) window.ITIPS.alerts.init(state);
    if (window.ITIPS.incidents) window.ITIPS.incidents.init(state);
    if (window.ITIPS.health) window.ITIPS.health.init(state);
    if (window.ITIPS.zones) window.ITIPS.zones.init(state);
    if (window.ITIPS.sensors) window.ITIPS.sensors.init(state);
    if (window.ITIPS.mllab) window.ITIPS.mllab.init(state);
    if (window.ITIPS.testing) window.ITIPS.testing.init(state);
    activateTab("live");
  }

  const modules = ["live.js", "workers.js", "plates.js", "alerts.js", "incidents.js", "health.js", "zones.js", "sensors.js", "ml-lab.js", "testing.js"];
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
