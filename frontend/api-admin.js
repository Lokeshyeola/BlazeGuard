(() => {
  const base = (window.BLAZEGUARD_CONFIG?.backendUrl || "http://127.0.0.1:8000").replace(/\/$/, "");
  const form = document.getElementById("auth-form");
  const tokenField = document.getElementById("admin-token");
  const authMessage = document.getElementById("auth-message");
  const panel = document.getElementById("management");
  const message = document.getElementById("management-message");
  const rows = document.getElementById("key-list");
  const reveal = document.getElementById("new-key");
  const keyText = document.getElementById("generated-key");
  let adminToken = "";
  let busy = false;

  async function call(path, options = {}) {
    const response = await fetch(base + path, { ...options, headers: {
      ...(adminToken ? { Authorization: "Bearer " + adminToken } : {}),
      ...options.headers
    }});
    if (!response.ok) {
      let detail = "Request failed. Check the server and administrator access.";
      try { const body = await response.json(); if (typeof body.detail === "string") detail = body.detail; } catch (_) {}
      throw new Error(detail);
    }
    return response.json();
  }
  function report(target, error) { target.textContent = error instanceof Error ? error.message : "Request failed."; }
  function busyState(value) { busy = value; document.querySelectorAll("#management button").forEach(button => { button.disabled = value; }); }

  async function refresh() {
    const data = await call("/api/v1/api-keys");
    rows.replaceChildren();
    if (!data.keys.length) {
      const row = document.createElement("tr"), cell = document.createElement("td");
      cell.colSpan = 4; cell.textContent = "No API keys have been generated."; row.append(cell); rows.append(row); return;
    }
    for (const key of data.keys) {
      const row = document.createElement("tr");
      const id = document.createElement("td"); id.textContent = key.key_id;
      const created = document.createElement("td"); created.textContent = new Date(key.created_at).toLocaleString();
      const state = document.createElement("td"), badge = document.createElement("span");
      badge.className = "badge " + key.status; badge.textContent = key.status; state.append(badge);
      const action = document.createElement("td");
      if (key.status === "active") {
        const button = document.createElement("button"); button.className = "revoke"; button.type = "button"; button.textContent = "Revoke";
        button.addEventListener("click", () => revoke(key.key_id)); action.append(button);
      } else action.textContent = key.revoked_at ? "Revoked " + new Date(key.revoked_at).toLocaleString() : "—";
      row.append(id, created, state, action); rows.append(row);
    }
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    const candidate = tokenField.value;
    authMessage.textContent = "";
    try {
      const response = await fetch(base + "/api/v1/api-keys", { headers: { Authorization: "Bearer " + candidate } });
      if (!response.ok) throw new Error("Administrator authentication failed or is not configured.");
      adminToken = candidate; tokenField.value = ""; panel.hidden = false; await refresh();
    } catch (error) { adminToken = ""; report(authMessage, error); }
  });

  document.getElementById("generate-key").addEventListener("click", async () => {
    if (busy) return; busyState(true); message.textContent = "";
    try {
      const result = await call("/api/v1/api-keys", { method: "POST" });
      keyText.textContent = result.api_key; reveal.hidden = false; await refresh();
    } catch (error) { report(message, error); } finally { busyState(false); }
  });
  document.getElementById("copy-key").addEventListener("click", async event => {
    try { await navigator.clipboard.writeText(keyText.textContent); event.currentTarget.textContent = "Copied"; setTimeout(() => { event.currentTarget.textContent = "Copy key"; }, 1500); }
    catch (_) { message.textContent = "Clipboard access failed. Select and copy the displayed key."; }
  });
  async function revoke(id) {
    if (busy) return; busyState(true); message.textContent = "";
    try { await call("/api/v1/api-keys/" + encodeURIComponent(id), { method: "DELETE" }); await refresh(); }
    catch (error) { report(message, error); } finally { busyState(false); }
  }
  document.getElementById("disconnect").addEventListener("click", () => {
    adminToken = ""; panel.hidden = true; reveal.hidden = true; keyText.textContent = ""; rows.replaceChildren(); authMessage.textContent = "Administrator session disconnected.";
  });
})();
