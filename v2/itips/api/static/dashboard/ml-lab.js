// ML Lab tab — drive the Jetson FaceEngine / PlateEngine / BehaviorEngine
// from the dashboard with an uploaded still. Optional `dispatch` toggle
// routes the result through the AlertEngine so the Alerts tab lights up
// exactly as if a real camera event had fired.

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

  function setPill(id, kind, label) {
    const pill = document.getElementById(id);
    pill.className = `pill ${kind}`;
    pill.textContent = label;
  }

  function statusPill(id, label, status) {
    if (!status.wired) {
      setPill(id, "pill-idle", `${label}: not wired`);
    } else if (status.ready) {
      setPill(id, "pill-ok", `${label}: ready`);
    } else {
      setPill(id, "pill-warn", `${label}: warming…`);
    }
  }

  async function refreshStatus() {
    try {
      const res = await fetch("/api/ml/status");
      const body = await res.json();
      statusPill("ml-face-pill", "face", body.face);
      statusPill("ml-plate-pill", "plate", body.plate);
      statusPill("ml-behavior-pill", "behavior", body.behavior);
    } catch (e) {
      ["ml-face-pill", "ml-plate-pill", "ml-behavior-pill"].forEach((id) =>
        setPill(id, "pill-err", "engine: error"));
    }
  }

  async function warmupAll() {
    try {
      const res = await fetch("/api/ml/warmup", { method: "POST" });
      const body = await res.json();
      statusPill("ml-face-pill", "face", body.face);
      statusPill("ml-plate-pill", "plate", body.plate);
      statusPill("ml-behavior-pill", "behavior", body.behavior);
    } catch (e) {
      /* status pill stays */
    }
  }

  async function loadCamerasIntoBehaviorSelect() {
    const sel = document.getElementById("ml-behavior-camera");
    sel.innerHTML = "";
    try {
      const res = await fetch("/api/cameras");
      const body = await res.json();
      (body.cameras || []).forEach((c) => {
        sel.appendChild(el("option", { value: String(c.camera_id) },
          `cam${c.camera_id} · ${c.endpoint}`));
      });
      if (!sel.options.length) {
        sel.appendChild(el("option", { value: "1" }, "cam1 (none configured)"));
      }
    } catch (e) {
      sel.appendChild(el("option", { value: "1" }, "(failed to load)"));
    }
  }

  async function loadOverrides() {
    const ul = document.getElementById("ml-face-overrides");
    ul.innerHTML = "";
    try {
      const [camsRes, capsRes] = await Promise.all([
        fetch("/api/cameras"),
        fetch("/api/health/capabilities"),
      ]);
      const cams = (await camsRes.json()).cameras || [];
      const caps = await capsRes.json();
      if (!cams.length) {
        ul.appendChild(el("li", { class: "muted" }, "No cameras configured."));
        return;
      }
      const overrides = caps.overrides || {};
      cams.forEach((cam) => {
        const ov = overrides[String(cam.camera_id)] || {};
        const forced = ov.face_recognition === true;
        const checkbox = el("input", { type: "checkbox" });
        if (forced) checkbox.checked = true;
        checkbox.addEventListener("change", () => toggleOverride(cam.camera_id, checkbox.checked));
        const native = (caps.cameras || {})[String(cam.camera_id)] || {};
        const nativePill = el("span", { class: "pill" },
          native.face_recognition ? "effective: native" : "effective: jetson");
        nativePill.className = "pill " + (native.face_recognition ? "pill-idle" : "pill-ok");
        const row = el("li", {},
          el("div", { class: "row" },
            el("label", { class: "inline" }, checkbox,
              el("span", {}, ` Force Jetson FR on cam ${cam.camera_id}`)),
            el("span", { class: "muted" }, cam.endpoint),
            el("span", { class: "ts" }, nativePill),
          ),
        );
        ul.appendChild(row);
      });
    } catch (e) {
      ul.appendChild(el("li", { class: "muted" }, "Failed to load: " + e));
    }
  }

  async function toggleOverride(cameraId, on) {
    try {
      const res = await fetch(
        `/api/health/capabilities/${cameraId}/face_recognition/override`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ force_fallback: on ? true : null }),
        },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        alert("Override failed: " + (body.error || res.status));
      }
    } catch (e) {
      alert("Override failed: " + e);
    } finally {
      // Re-render so the "effective" pill reflects the new state.
      loadOverrides();
    }
  }

  async function populateOpenAIScenarios(scenarios) {
    const sel = document.getElementById("ml-openai-scenario");
    if (!sel) return;
    const existing = sel.value;
    sel.innerHTML = "";
    (scenarios || []).forEach((name) => {
      sel.appendChild(el("option", { value: name }, name));
    });
    if (existing && (scenarios || []).includes(existing)) sel.value = existing;
  }

  async function runOpenAIScenario() {
    const scenario = document.getElementById("ml-openai-scenario").value;
    const file = pickFile("ml-openai-image");
    const resultEl = document.getElementById("ml-openai-result");
    if (!scenario) { resultEl.textContent = "Pick a scenario."; return; }
    if (!file) { resultEl.textContent = "Pick an image first."; return; }
    const form = fileToForm(file);
    const cam = document.getElementById("ml-openai-camera").value;
    const zone = document.getElementById("ml-openai-zone").value;
    const conf = document.getElementById("ml-openai-confidence").value;
    if (cam) form.append("camera_id", cam);
    if (zone) form.append("zone_name", zone);
    if (conf) form.append("confidence", conf);
    resultEl.textContent = "running… (vision calls take 2–6s)";
    resultEl.style.color = "var(--muted)";
    try {
      const res = await fetch(`/api/ml/openai/validate/${scenario}`, {
        method: "POST", body: form,
      });
      const body = await res.json();
      resultEl.textContent = JSON.stringify(body, null, 2);
      resultEl.style.color = res.ok ? "var(--text)" : "var(--err)";
      loadOpenAIStatus();
    } catch (e) {
      resultEl.textContent = "fetch failed: " + e;
      resultEl.style.color = "var(--err)";
    }
  }

  async function loadOpenAIStatus() {
    const pill = document.getElementById("ml-openai-pill");
    const modelEl = document.getElementById("ml-openai-model");
    const tokensEl = document.getElementById("ml-openai-tokens");
    const ul = document.getElementById("ml-openai-recent");
    if (!pill) return;
    try {
      const res = await fetch("/api/ml/openai/status");
      const body = await res.json();
      if (!body.wired) {
        pill.className = "pill pill-idle";
        pill.textContent = "openai: not wired";
        modelEl.textContent = body.reason || "";
        tokensEl.textContent = "";
        ul.innerHTML = "";
        ul.appendChild(el("li", { class: "muted" },
          "Set ITIPS_OPENAI_ENABLED=true and ITIPS_OPENAI_API_KEY to enable."));
        return;
      }
      if (body.enabled) {
        pill.className = "pill pill-ok";
        pill.textContent = "openai: ready";
      } else {
        pill.className = "pill pill-warn";
        pill.textContent = "openai: disabled";
      }
      modelEl.textContent = `model=${body.model || "?"}`;
      const used = body.tokens_used_hour ?? 0;
      const cap = body.tokens_cap_hour ?? 0;
      tokensEl.textContent = cap
        ? `${used.toLocaleString()} / ${cap.toLocaleString()} tokens (1h)`
        : "";
      populateOpenAIScenarios(body.scenarios);
      ul.innerHTML = "";
      const recent = body.recent || [];
      if (!recent.length) {
        ul.appendChild(el("li", { class: "muted" }, "No verdicts yet."));
      } else {
        recent.forEach((r) => ul.appendChild(renderVerdict(r)));
      }
    } catch (e) {
      pill.className = "pill pill-err";
      pill.textContent = "openai: fetch failed";
    }
  }

  function renderVerdict(v) {
    const verdictClass =
      v.verdict === "real" ? "pill pill-err" :
      v.verdict === "false_positive" ? "pill pill-ok" :
      "pill pill-warn";
    const ts = v.ts ? new Date(v.ts * 1000).toLocaleTimeString() : "";
    return el("li", {},
      el("div", { class: "row" },
        el("span", { class: "kind" }, v.scenario),
        el("span", { class: verdictClass },
          `${v.verdict}/${v.category}`),
        el("span", { class: "muted" },
          ` conf=${(v.confidence ?? 0).toFixed(2)}`),
        el("span", { class: "muted" },
          v.cached ? " · cached" : ` · ${v.tokens_used ?? 0}t`),
        el("span", { class: "ts" }, ts),
      ),
      el("pre", {}, v.summary || ""),
    );
  }

  async function loadEnrolled() {
    const ul = document.getElementById("ml-face-enrolled");
    ul.innerHTML = "";
    try {
      const res = await fetch("/api/ml/face/enrolled");
      const body = await res.json();
      if (!body.available) {
        ul.appendChild(el("li", { class: "muted" }, "Face engine not wired."));
        return;
      }
      if (!body.people.length) {
        ul.appendChild(el("li", { class: "muted" },
          "Nobody enrolled yet — add workers in the Workers tab."));
        return;
      }
      body.people.forEach((p) => {
        ul.appendChild(el("li", {},
          el("div", { class: "row" },
            el("span", { class: "kind" }, p.full_name),
            el("span", { class: "muted" }, ` · ${p.person_id}`),
            el("span", { class: "ts" }, `${p.dim}-d`),
          ),
        ));
      });
    } catch (e) {
      ul.appendChild(el("li", { class: "muted" }, "Failed to load: " + e));
    }
  }

  // ─── generic upload + invoke ─────────────────────────────────────

  function dispatchFlag() {
    return document.getElementById("ml-dispatch").checked ? "?dispatch=1" : "";
  }

  function setResult(elementId, body, ok) {
    const node = document.getElementById(elementId);
    node.textContent = JSON.stringify(body, null, 2);
    node.style.color = ok ? "var(--text)" : "var(--err)";
  }

  function pickFile(id) {
    const input = document.getElementById(id);
    if (!input.files.length) return null;
    return input.files[0];
  }

  function fileToForm(file, extra = {}) {
    const form = new FormData();
    form.append("image", file);
    for (const [k, v] of Object.entries(extra)) form.append(k, v);
    return form;
  }

  async function runFace() {
    const file = pickFile("ml-face-image");
    if (!file) {
      setResult("ml-face-result", { error: "Pick an image first." }, false);
      return;
    }
    const cam = document.getElementById("ml-face-camera").value || "0";
    const form = fileToForm(file, { camera_id: cam });
    setResult("ml-face-result", { status: "running…" }, true);
    try {
      const res = await fetch("/api/ml/face/recognize" + dispatchFlag(), {
        method: "POST", body: form,
      });
      const body = await res.json();
      setResult("ml-face-result", body, res.ok);
      refreshStatus();
    } catch (e) {
      setResult("ml-face-result", { error: String(e) }, false);
    }
  }

  async function runPlate() {
    const file = pickFile("ml-plate-image");
    if (!file) {
      setResult("ml-plate-result", { error: "Pick an image first." }, false);
      return;
    }
    const cam = document.getElementById("ml-plate-camera").value || "0";
    const form = fileToForm(file, { camera_id: cam });
    setResult("ml-plate-result", { status: "running…" }, true);
    try {
      const res = await fetch("/api/ml/plate/read" + dispatchFlag(), {
        method: "POST", body: form,
      });
      const body = await res.json();
      setResult("ml-plate-result", body, res.ok);
      refreshStatus();
    } catch (e) {
      setResult("ml-plate-result", { error: String(e) }, false);
    }
  }

  async function runBehavior() {
    const file = pickFile("ml-behavior-image");
    if (!file) {
      setResult("ml-behavior-result", { error: "Pick an image first." }, false);
      return;
    }
    const cam = document.getElementById("ml-behavior-camera").value;
    if (!cam) {
      setResult("ml-behavior-result", { error: "Pick a camera." }, false);
      return;
    }
    const form = fileToForm(file);
    setResult("ml-behavior-result", { status: "running…" }, true);
    try {
      const res = await fetch(`/api/ml/behavior/${cam}/analyse${dispatchFlag()}`, {
        method: "POST", body: form,
      });
      const body = await res.json();
      setResult("ml-behavior-result", body, res.ok);
      refreshStatus();
    } catch (e) {
      setResult("ml-behavior-result", { error: String(e) }, false);
    }
  }

  // ─── lifecycle ───────────────────────────────────────────────────

  function init() {
    document.getElementById("ml-warmup").addEventListener("click", warmupAll);
    document.getElementById("ml-refresh").addEventListener("click", () => {
      refreshStatus();
      loadEnrolled();
      loadOverrides();
      loadOpenAIStatus();
    });
    document.getElementById("ml-face-run").addEventListener("click", runFace);
    document.getElementById("ml-plate-run").addEventListener("click", runPlate);
    document.getElementById("ml-behavior-run").addEventListener("click", runBehavior);
    const openaiBtn = document.getElementById("ml-openai-run");
    if (openaiBtn) openaiBtn.addEventListener("click", runOpenAIScenario);
  }

  function onShow() {
    refreshStatus();
    loadCamerasIntoBehaviorSelect();
    loadEnrolled();
    loadOverrides();
    loadOpenAIStatus();
  }

  window.ITIPS = window.ITIPS || {};
  window.ITIPS.mllab = { init, onShow };
})();
