/** BlazeGuard live monitoring, kept independent from the university portal flow. */
(function initBlazeGuardMonitor() {
  const frontendConfig = window.BLAZEGUARD_CONFIG || {
    backendUrl: "http://127.0.0.1:8000",
    statusPollIntervalMs: 5000
  };
  let requestController = null;
  let simulatorTimer = null;
  let simulatorActive = false;
  let selectedLoad = 1000;
  let currentDecision = "NORMAL";

  document.addEventListener("DOMContentLoaded", () => {
    initLoadSimulator();
    pollSystemStatus();
    window.setInterval(pollSystemStatus, frontendConfig.statusPollIntervalMs);
  });

  window.BlazeGuardMonitor = {
    getDecision: () => currentDecision,
    isSimulatorActive: () => simulatorActive
  };

  async function pollSystemStatus() {
    if (!document.getElementById("blazeguardMonitor")) return;
    if (simulatorActive) return;
    if (requestController) requestController.abort();
    requestController = new AbortController();
    const timeout = window.setTimeout(() => requestController.abort(), 4500);

    try {
      const response = await fetch(`${frontendConfig.backendUrl}/system-status?eta_seconds=0`, { signal: requestController.signal });
      if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);
      const payload = await response.json();
      if (!payload.metrics || !["NORMAL", "WARNING", "CRITICAL", "DELAY"].includes(payload.decision)) {
        throw new Error("Backend returned an invalid monitoring payload");
      }
      renderLiveStatus(payload);
    } catch (error) {
      if (error.name !== "AbortError") renderOfflineState();
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function renderLiveStatus(payload) {
    const status = document.getElementById("blazeguardStatus");
    document.getElementById("blazeguardConnectionState").textContent = "Live / Active";
    document.getElementById("blazeguardCpu").textContent = formatPercent(payload.metrics.cpu_percent);
    document.getElementById("blazeguardRam").textContent = formatPercent(payload.metrics.ram_percent);
    document.getElementById("blazeguardEta").textContent = `${formatNumber(payload.metrics.eta_seconds)} sec`;
    document.getElementById("blazeguardStatusText").textContent = payload.decision;
    document.getElementById("blazeguardMessage").textContent = "Decision engine response received from FastAPI.";
    document.getElementById("blazeguardUpdated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    status.className = `blazeguard-status blazeguard-status-${payload.decision.toLowerCase()}`;
    publishDecision(payload.decision);
  }

  function renderOfflineState() {
    const status = document.getElementById("blazeguardStatus");
    document.getElementById("blazeguardConnectionState").textContent = "BlazeGuard Offline";
    document.getElementById("blazeguardStatusText").textContent = "OFFLINE";
    document.getElementById("blazeguardMessage").textContent = "Unable to connect to monitoring service.";
    document.getElementById("blazeguardUpdated").textContent = "Retrying automatically...";
    status.className = "blazeguard-status blazeguard-status-offline";
    publishDecision("OFFLINE");
  }

  function publishDecision(decision) {
    const changed = currentDecision !== decision;
    currentDecision = decision;
    if (changed) window.dispatchEvent(new CustomEvent("blazeguard:decision", { detail: { decision } }));
  }

  function initLoadSimulator() {
    document.querySelectorAll(".blazeguard-load-option").forEach((button) => {
      button.addEventListener("click", () => {
        selectedLoad = Number(button.dataset.load);
        document.querySelectorAll(".blazeguard-load-option").forEach((option) => option.classList.remove("is-selected"));
        button.classList.add("is-selected");
        document.getElementById("blazeguardLoadCount").textContent = `${selectedLoad} requests`;
      });
    });

    document.getElementById("blazeguardStartLoad").addEventListener("click", startLoad);
    document.getElementById("blazeguardStopLoad").addEventListener("click", stopLoad);
    document.getElementById("blazeguardResetLoad").addEventListener("click", resetLoad);
  }

  function startLoad() {
    if (simulatorActive) return;
    simulatorActive = true;
    updateSimulatorControls(true);
    pushSyntheticDecision();
    simulatorTimer = window.setInterval(pushSyntheticDecision, 1500);
  }

  async function pushSyntheticDecision() {
    try {
      const response = await fetch(`${frontendConfig.backendUrl}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cpu_percent: 95, ram_percent: 92, eta_seconds: 0 })
      });
      if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);
      const payload = await response.json();
      renderSyntheticStatus(payload.decision);
    } catch (error) {
      renderOfflineState();
    }
  }

  function renderSyntheticStatus(decision) {
    const status = document.getElementById("blazeguardStatus");
    document.getElementById("blazeguardConnectionState").textContent = "Live / Active";
    document.getElementById("blazeguardStatusText").textContent = decision;
    document.getElementById("blazeguardMessage").textContent = `Synthetic load: ${selectedLoad} requests through decision engine.`;
    document.getElementById("blazeguardUpdated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    status.className = `blazeguard-status blazeguard-status-${decision.toLowerCase()}`;
    publishDecision(decision);
  }

  function stopLoad() {
    simulatorActive = false;
    if (simulatorTimer) window.clearInterval(simulatorTimer);
    simulatorTimer = null;
    updateSimulatorControls(false);
    document.getElementById("blazeguardSimulatorStatus").textContent = "Load stopped; checking real system recovery...";
    pollSystemStatus();
  }

  function resetLoad() {
    stopLoad();
    selectedLoad = 1000;
    document.querySelectorAll(".blazeguard-load-option").forEach((option) => option.classList.toggle("is-selected", option.dataset.load === "1000"));
    document.getElementById("blazeguardLoadCount").textContent = "0 requests";
    document.getElementById("blazeguardSimulatorStatus").textContent = "Ready to generate synthetic requests";
  }

  function updateSimulatorControls(active) {
    document.getElementById("blazeguardStartLoad").disabled = active;
    document.getElementById("blazeguardStopLoad").disabled = !active;
    document.querySelectorAll(".blazeguard-load-option").forEach((button) => { button.disabled = active; });
    document.getElementById("blazeguardSimulatorStatus").textContent = active
      ? `Generating ${selectedLoad} synthetic requests...`
      : "Ready to generate synthetic requests";
  }

  function formatPercent(value) { return `${formatNumber(value)}%`; }
  function formatNumber(value) { return Number.isFinite(Number(value)) ? Number(value).toFixed(1) : "--.-"; }
})();