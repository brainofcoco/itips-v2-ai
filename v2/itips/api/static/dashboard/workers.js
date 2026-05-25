// Workers tab — face allowlist mapped directly into each Dahua camera.
//
// Upload a JPEG + name → POST /api/workers (multipart). The backend fans
// out to every camera's faceRecognitionServer.cgi `addPerson`. We never
// compute embeddings on the Jetson; the WizMind chip does it.

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

  function setStatus(msg, ok = true) {
    const node = document.getElementById("worker-status");
    node.textContent = msg;
    node.style.color = ok ? "var(--muted)" : "var(--err)";
  }

  async function loadWorkers() {
    const res = await fetch("/api/workers");
    const body = await res.json();
    const list = document.getElementById("worker-list");
    list.innerHTML = "";
    if (!body.workers || !body.workers.length) {
      list.appendChild(el("li", { class: "muted" }, "No workers enrolled yet."));
    } else {
      body.workers.forEach((w) => list.appendChild(renderWorker(w)));
    }
    await loadPerCamera(body.available_cameras || []);
  }

  function renderWorker(w) {
    const cameraEntries = Object.entries(w.cameras || {});
    const cameras = cameraEntries.map(([camId, uid]) => `cam${camId}:${uid}`).join(", ")
                    || "no cameras";
    const delBtn = el("button", {}, "Delete");
    delBtn.addEventListener("click", async () => {
      if (!confirm(`Delete worker ${w.full_name} from every camera?`)) return;
      const res = await fetch(`/api/workers/${encodeURIComponent(w.person_id)}`, { method: "DELETE" });
      const body = await res.json();
      if (body.ok) await loadWorkers();
      else alert(body.error || "delete failed");
    });
    const jetsonPill = el("span",
      { class: w.jetson_enrolled ? "pill pill-ok" : "pill pill-idle" },
      w.jetson_enrolled ? "jetson ✓" : "jetson ✗");
    const dahuaPill = el("span",
      { class: cameraEntries.length ? "pill pill-ok" : "pill pill-idle" },
      cameraEntries.length ? `dahua ${cameraEntries.length}cam` : "dahua none");
    return el("li", {},
      el("div", { class: "row" },
        el("span", { class: "kind" }, w.full_name),
        el("span", { class: "muted" }, `· ${w.person_id}`),
        el("span", {}, " "), jetsonPill,
        el("span", {}, " "), dahuaPill,
        el("span", { class: "ts" }, delBtn),
      ),
      el("pre", {}, cameras),
    );
  }

  async function loadPerCamera(cameraIds) {
    const wrap = document.getElementById("worker-cameras");
    wrap.innerHTML = "";
    if (!cameraIds.length) {
      wrap.appendChild(el("p", { class: "muted" }, "No cameras."));
      return;
    }
    for (const camId of cameraIds) {
      const block = el("div", { class: "camera-block" });
      block.appendChild(el("h4", {}, `Camera ${camId}`));
      const tbl = el("table", { class: "thin-table" });
      block.appendChild(tbl);
      wrap.appendChild(block);
      try {
        const res = await fetch(`/api/workers/${camId}`);
        if (!res.ok) {
          tbl.appendChild(el("tr", {}, el("td", {}, "(unavailable)")));
          continue;
        }
        const body = await res.json();
        const head = el("tr", {},
          el("th", {}, "UID"),
          el("th", {}, "Name"),
          el("th", {}, "Sex"),
        );
        tbl.appendChild(head);
        (body.people || []).forEach((p) => {
          tbl.appendChild(el("tr", {},
            el("td", {}, p.uid),
            el("td", {}, p.name),
            el("td", {}, p.sex || ""),
          ));
        });
      } catch (e) {
        tbl.appendChild(el("tr", {}, el("td", {}, String(e))));
      }
    }
  }

  async function addWorker() {
    const name = document.getElementById("worker-name").value.trim();
    const personId = document.getElementById("worker-person-id").value.trim();
    const sex = document.getElementById("worker-sex").value;
    const fileInput = document.getElementById("worker-image");
    if (!name) { setStatus("Name required.", false); return; }
    if (!fileInput.files.length) { setStatus("Select an image first.", false); return; }
    setStatus("Pushing to every camera…");
    const form = new FormData();
    form.append("full_name", name);
    if (personId) form.append("person_id", personId);
    if (sex) form.append("sex", sex);
    form.append("image", fileInput.files[0]);
    try {
      const res = await fetch("/api/workers", { method: "POST", body: form });
      const body = await res.json();
      if (body.ok) {
        const camCount = Object.keys(body.cameras || {}).length;
        const fail = (body.failures || []).join(", ");
        // Jetson and Dahua are two independent enrollment paths. Either
        // succeeding is enough — most setups with the FR override
        // turned on will deliberately have 0 Dahua cameras enrolled.
        const parts = [];
        if (body.ml_enrolled) parts.push("Jetson ✓");
        else if (body.ml_error) parts.push(`Jetson ✗ (${body.ml_error})`);
        parts.push(`Dahua: ${camCount} camera(s)`);
        if (fail) parts.push(`Dahua failures: ${fail}`);
        const overallOk = body.ml_enrolled || camCount > 0;
        setStatus(
          `Enrolled ${name} — ${parts.join(" · ")}`,
          overallOk,
        );
        document.getElementById("worker-name").value = "";
        document.getElementById("worker-person-id").value = "";
        document.getElementById("worker-image").value = "";
        await loadWorkers();
      } else {
        setStatus(body.error || "enroll failed", false);
      }
    } catch (e) {
      setStatus("enroll failed: " + e, false);
    }
  }

  function init() {
    document.getElementById("worker-add").addEventListener("click", addWorker);
    document.getElementById("worker-refresh").addEventListener("click", loadWorkers);
  }

  window.ITIPS = window.ITIPS || {};
  window.ITIPS.workers = {
    init,
    onShow: () => loadWorkers(),
  };
})();
