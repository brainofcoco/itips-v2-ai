// Workers tab — face allowlist enrolled into the local face engine.
//
// Upload a JPEG + name → POST /api/workers (multipart). The backend
// computes an InsightFace embedding and stores it; the Dahua cameras'
// onboard face DB is no longer used.

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
  }

  function renderWorker(w) {
    const delBtn = el("button", {}, "Delete");
    delBtn.addEventListener("click", async () => {
      if (!confirm(`Delete worker ${w.full_name}?`)) return;
      const res = await fetch(`/api/workers/${encodeURIComponent(w.person_id)}`, { method: "DELETE" });
      const body = await res.json();
      if (body.ok) await loadWorkers();
      else alert(body.error || "delete failed");
    });
    const pill = el("span",
      { class: w.jetson_enrolled ? "pill pill-ok" : "pill pill-idle" },
      w.jetson_enrolled ? "enrolled ✓" : "embedding missing");
    return el("li", {},
      el("div", { class: "row" },
        el("span", { class: "kind" }, w.full_name),
        el("span", { class: "muted" }, `· ${w.person_id}`),
        el("span", {}, " "), pill,
        el("span", { class: "ts" }, delBtn),
      ),
    );
  }

  async function addWorker() {
    const name = document.getElementById("worker-name").value.trim();
    const personId = document.getElementById("worker-person-id").value.trim();
    const fileInput = document.getElementById("worker-image");
    if (!name) { setStatus("Name required.", false); return; }
    if (!fileInput.files.length) { setStatus("Select an image first.", false); return; }
    setStatus("Enrolling…");
    const form = new FormData();
    form.append("full_name", name);
    if (personId) form.append("person_id", personId);
    form.append("image", fileInput.files[0]);
    try {
      const res = await fetch("/api/workers", { method: "POST", body: form });
      const body = await res.json();
      if (body.ok) {
        setStatus(`Enrolled ${name} (${body.person_id}).`, true);
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
