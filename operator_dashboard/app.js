"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const API_BASE = "http://127.0.0.1:8000";
  const POLL_MS = 1000;
  const VISUAL_TOKEN_LIMIT = 32;
  const capacitySlider = $("capacity-slider");
  const trafficSlider = $("traffic-slider");
  const startButton = $("start-button");
  const stopButton = $("stop-button");
  const resetButton = $("reset-button");

  // The dedicated operator credential exists in memory only; the admin token is never requested or stored.
  let operatorToken = "";
  let connected = false;
  let running = false;
  let stateName = "OFFLINE";
  let pollTimer = null;
  let settingsTimer = null;
  let polling = false;

  // Stage 2 keeps the approved layout while replacing Stage 1's virtual-only copy.
  function setLiveQueueCopy() {
    const replacements = [
      [".metric-card.accent-blue .metric-foot span:nth-of-type(2)", "session arrivals observed"],
      [".metric-card.accent-amber .metric-foot span:nth-of-type(2)", "active FIFO queue"],
      [".metric-card.accent-red .metric-foot span:nth-of-type(2)", "rejected by admission"],
      ["#capacity-help", "Target service rate for the serial demo worker; admission policy remains unchanged."],
      ["#traffic-help", "Requested demo arrivals per second; BlazeGuard enforces a lower server-side limit."],
      [".flow-track", "Requests move through the shared BlazeGuard FIFO queue. Marked demo rows complete locally; real rows retain normal forwarding."],
      [".how-panel div:nth-child(2) > p:last-child", "The simulator creates bounded, marked request records in the local BlazeGuard queue. Demo rows complete locally and are never forwarded to the protected Result Portal."],
      [".how-panel .local-chip", "LOCAL OPERATOR API"],
      ["footer > span:last-child", "DEMO ROWS COMPLETE LOCALLY  ·  NO PORTAL FORWARDING"],
      [".intro-row .eyebrow", "REQUEST OVERVIEW  ·  LIVE BLAZEGUARD STATE"],
    ];
    replacements.forEach(([selector, copy]) => {
      const element = document.querySelector(selector);
      if (element) element.textContent = copy;
    });
    const flowTrack = document.querySelector(".flow-track");
    if (flowTrack) flowTrack.setAttribute("aria-label", "Requests flow through the shared BlazeGuard queue; demo rows complete locally, real rows are forwarded normally.");
  }

  function requestOperatorToken() {
    if (operatorToken) return true;
    const value = window.prompt("Enter the local BlazeGuard Operator credential:");
    if (!value || !value.trim()) return false;
    operatorToken = value.trim();
    return true;
  }

  async function api(path, body) {
    const response = await fetch(`${API_BASE}/api/v1/operator${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        Authorization: `Bearer ${operatorToken}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      cache: "no-store",
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) {
      const error = new Error(payload.detail || `Operator API returned HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function clearVisualState() {
    ["metric-incoming", "metric-allowed", "metric-waiting", "metric-completed", "metric-rejected", "queue-depth", "estimated-wait", "service-rate", "incoming-stage-count", "waiting-stage-count", "processing-stage-count", "completed-stage-count", "rejected-stage-count", "incoming-rate", "wait-pill"].forEach((id) => {
      $(id).textContent = "—";
    });
    ["incoming-tokens", "waiting-tokens", "processing-tokens", "completed-tokens"].forEach((id) => {
      $(id).replaceChildren();
      $(id).dataset.count = "-";
    });
  }

  function setConnectionError(message) {
    connected = false;
    running = false;
    stateName = "OFFLINE";
    $("run-state").textContent = "OFFLINE";
    $("state-indicator").classList.remove("running");
    $("run-state").setAttribute("title", message);
    document.querySelector(".intro-row .subhead").textContent = message;
    startButton.disabled = false;
    stopButton.disabled = true;
    clearVisualState();
  }

  function renderTokens(id, count, color) {
    const well = $(id);
    const amount = Math.min(VISUAL_TOKEN_LIMIT, Math.max(0, Math.ceil(Number(count) || 0)));
    if (well.dataset.count === String(amount)) return;
    well.dataset.count = String(amount);
    const fragment = document.createDocumentFragment();
    for (let index = 0; index < amount; index += 1) {
      const token = document.createElement("i");
      token.className = "token";
      token.style.setProperty("--token-color", color);
      token.setAttribute("aria-hidden", "true");
      fragment.append(token);
    }
    well.replaceChildren(fragment);
  }

  function formatCount(number) { return Math.max(0, Math.floor(Number(number) || 0)).toLocaleString("en-US"); }
  function formatSeconds(value) { return value == null ? "—" : Number(value).toFixed(1); }
  function formatDuration(iso) {
    if (!iso) return "00:00";
    const elapsed = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
    return `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(elapsed % 60).padStart(2, "0")}`;
  }

  function applyState(data) {
    connected = true;
    running = Boolean(data.running);
    stateName = data.state || (running ? "RUNNING" : "STOPPED");
    $("run-state").textContent = stateName;
    $("state-indicator").classList.toggle("running", running);
    $("run-state").removeAttribute("title");
    document.querySelector(".intro-row .subhead").textContent = "Live request and queue state from BlazeGuard.";
    if (data.limit_reason) {
      const detail = data.limit_reason === "CUMULATIVE_DEMO_DATA_LIMIT"
        ? `Demo generation stopped at the ${formatCount(data.max_total_requests)} lifetime demo-row limit.`
        : "Demo generation stopped at the outstanding demo-row limit; queued rows are draining.";
      document.querySelector(".intro-row .subhead").textContent = detail;
    }

    $("metric-incoming").textContent = formatCount(data.incoming);
    $("metric-allowed").textContent = formatCount(data.allowed);
    $("metric-waiting").textContent = formatCount(data.waiting);
    $("metric-completed").textContent = formatCount(data.completed);
    $("metric-rejected").textContent = formatCount(data.rejected);
    $("incoming-rate").textContent = `${data.effective_incoming_rate || 0} / sec effective`;
    $("wait-pill").textContent = `${formatSeconds(data.estimated_wait_seconds)} sec wait`;
    $("incoming-stage-count").textContent = `${formatCount(data.incoming)} received`;
    $("waiting-stage-count").textContent = `${formatCount(data.waiting)} waiting`;
    $("processing-stage-count").textContent = `${formatCount(data.processing)} in service`;
    $("completed-stage-count").textContent = `${formatCount(data.completed)} completed`;
    $("rejected-stage-count").textContent = formatCount(data.rejected);
    $("queue-depth").innerHTML = `${formatCount(data.queue_depth)} <small>requests</small>`;
    $("estimated-wait").innerHTML = `${formatSeconds(data.estimated_wait_seconds)} <small>sec</small>`;
    $("service-rate").innerHTML = `${Number(data.effective_service_rate || 0).toFixed(1)} <small>req / sec</small>`;
    $("elapsed").textContent = formatDuration(data.session_started_at);

    if (data.requested_capacity) capacitySlider.value = String(data.requested_capacity);
    if (data.requested_incoming_rate) trafficSlider.value = String(data.requested_incoming_rate);
    setSliderFill(capacitySlider);
    setSliderFill(trafficSlider);
    $("capacity-value").textContent = capacitySlider.value;
    $("traffic-value").textContent = trafficSlider.value;
    renderTokens("incoming-tokens", Math.min(data.incoming, 8), "#4b9cff");
    renderTokens("waiting-tokens", data.waiting, "#f4b94f");
    renderTokens("processing-tokens", data.processing, "#49cfdf");
    renderTokens("completed-tokens", Math.min(data.completed, 8), "#4bd4a0");
    startButton.disabled = running || stateName === "DRAINING";
    stopButton.disabled = !running;
  }

  function setSliderFill(slider) {
    const percent = ((Number(slider.value) - Number(slider.min)) / (Number(slider.max) - Number(slider.min))) * 100;
    slider.style.background = `linear-gradient(90deg,#4b9cff ${percent}%,#2e4053 ${percent}%)`;
  }

  async function refresh() {
    if (!operatorToken || polling) return;
    polling = true;
    try {
      applyState(await api("/state"));
    } catch (error) {
      if (error.status === 401) operatorToken = "";
      setConnectionError(error.message || "Unable to connect to the BlazeGuard Operator API.");
    } finally {
      polling = false;
    }
  }

  function beginPolling() {
    if (pollTimer) window.clearInterval(pollTimer);
    refresh();
    pollTimer = window.setInterval(refresh, POLL_MS);
  }

  async function connectAndRead() {
    if (!requestOperatorToken()) {
      setConnectionError("Operator API is offline. Enter the local Operator credential to connect.");
      return null;
    }
    try {
      const data = await api("/state");
      applyState(data);
      beginPolling();
      return data;
    } catch (error) {
      if (error.status === 401) operatorToken = "";
      setConnectionError(error.message || "Unable to connect to the BlazeGuard Operator API.");
      return null;
    }
  }

  async function startTraffic() {
    const current = await connectAndRead();
    if (!current || current.running) return;
    try {
      applyState(await api("/simulator/start", {
        incoming_rate: Number(trafficSlider.value),
        capacity: Number(capacitySlider.value),
      }));
    } catch (error) {
      await refresh();
      document.querySelector(".intro-row .subhead").textContent = error.message;
    }
  }

  async function stopTraffic() {
    if (!operatorToken && !(await connectAndRead())) return;
    try { applyState(await api("/simulator/stop", {})); }
    catch (error) { setConnectionError(error.message); }
  }

  async function resetTraffic() {
    if (!operatorToken && !(await connectAndRead())) return;
    try { applyState(await api("/simulator/reset", {})); }
    catch (error) { setConnectionError(error.message); }
  }

  async function updateSettings() {
    if (!operatorToken || !connected || !(running || stateName === "DRAINING")) return;
    try {
      applyState(await api("/simulator/settings", {
        incoming_rate: Number(trafficSlider.value),
        capacity: Number(capacitySlider.value),
      }));
    } catch (error) {
      setConnectionError(error.message);
    }
  }

  function queueSettingsUpdate() {
    if (settingsTimer) window.clearTimeout(settingsTimer);
    settingsTimer = window.setTimeout(updateSettings, 180);
  }

  startButton.addEventListener("click", startTraffic);
  stopButton.addEventListener("click", stopTraffic);
  resetButton.addEventListener("click", resetTraffic);
  capacitySlider.addEventListener("input", () => {
    $("capacity-value").textContent = capacitySlider.value;
    setSliderFill(capacitySlider);
    queueSettingsUpdate();
  });
  trafficSlider.addEventListener("input", () => {
    $("traffic-value").textContent = trafficSlider.value;
    $("incoming-rate").textContent = `${trafficSlider.value} / sec requested`;
    setSliderFill(trafficSlider);
    queueSettingsUpdate();
  });

  setLiveQueueCopy();
  setSliderFill(capacitySlider);
  setSliderFill(trafficSlider);
  setConnectionError("Operator API is offline. Press START TRAFFIC to connect with the local Operator credential.");
})();
