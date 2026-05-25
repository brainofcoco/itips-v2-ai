// Plates tab — TrafficRedList / TrafficBlackList mapped per camera.

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
    const node = document.getElementById("plate-status");
    node.textContent = msg;
    node.style.color = ok ? "var(--muted)" : "var(--err)";
  }

  async function loadPlates() {
    const listType = document.getElementById("plate-list-type").value;
    const res = await fetch(`/api/plates?list_type=${encodeURIComponent(listType)}`);
    const body = await res.json();
    const wrap = document.getElementById("plate-cameras");
    wrap.innerHTML = "";
    if (!body.cameras || !Object.keys(body.cameras).length) {
      wrap.appendChild(el("p", { class: "muted" }, "No cameras."));
      return;
    }
    for (const [camId, payload] of Object.entries(body.cameras)) {
      const block = el("div", { class: "camera-block" });
      block.appendChild(el("h4", {}, `Camera ${camId} · ${listType}`));
      if (payload && payload.supported === false) {
        block.appendChild(el("p", { class: "muted" },
          `(${payload.reason || "not available on this camera"})`));
        wrap.appendChild(block);
        continue;
      }
      const rows = (payload && payload.rows) || [];
      if (payload && payload.error) {
        block.appendChild(el("p", { class: "muted" },
          `(transient error: ${payload.error})`));
      } else if (!rows.length) {
        block.appendChild(el("p", { class: "muted" }, "(empty)"));
      } else {
        const tbl = el("table", { class: "thin-table" });
        tbl.appendChild(el("tr", {},
          el("th", {}, "RecNo"),
          el("th", {}, "Plate"),
          el("th", {}, "Owner"),
          el("th", {}, "Color"),
          el("th", {}, ""),
        ));
        rows.forEach((r) => {
          const del = el("button", {}, "✕");
          del.addEventListener("click", async () => {
            const res = await fetch(
              `/api/plates/${camId}/${r.rec_no}?list_type=${encodeURIComponent(listType)}`,
              { method: "DELETE" },
            );
            const body = await res.json();
            if (body.ok) loadPlates();
            else alert(body.error || "delete failed");
          });
          tbl.appendChild(el("tr", {},
            el("td", {}, String(r.rec_no)),
            el("td", {}, r.plate_number),
            el("td", {}, r.master_of_car || ""),
            el("td", {}, r.plate_color || ""),
            el("td", {}, del),
          ));
        });
        block.appendChild(tbl);
      }
      wrap.appendChild(block);
    }
  }

  async function addPlate() {
    const plate = document.getElementById("plate-number").value.trim().toUpperCase();
    const master = document.getElementById("plate-master").value.trim();
    const listType = document.getElementById("plate-list-type").value;
    const openGate = document.getElementById("plate-open-gate").checked;
    if (!plate) { setStatus("Plate number required.", false); return; }
    setStatus("Pushing to every camera…");
    try {
      const res = await fetch("/api/plates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          plate_number: plate,
          list_type: listType,
          master_of_car: master || undefined,
          open_gate: openGate,
        }),
      });
      const body = await res.json();
      if (body.ok) {
        const camIds = Object.keys(body.cameras || {});
        setStatus(`Added ${plate} on ${camIds.length} camera(s)`);
        document.getElementById("plate-number").value = "";
        document.getElementById("plate-master").value = "";
        document.getElementById("plate-open-gate").checked = false;
        await loadPlates();
      } else {
        setStatus(body.error || "add failed", false);
      }
    } catch (e) {
      setStatus("add failed: " + e, false);
    }
  }

  function init() {
    document.getElementById("plate-add").addEventListener("click", addPlate);
    document.getElementById("plate-refresh").addEventListener("click", loadPlates);
    document.getElementById("plate-list-type").addEventListener("change", loadPlates);
  }

  window.ITIPS = window.ITIPS || {};
  window.ITIPS.plates = {
    init,
    onShow: () => loadPlates(),
  };
})();
