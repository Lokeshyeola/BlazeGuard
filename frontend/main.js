/**
 * Maharashtra Digital University - Result Portal & Virtual Queue Engine
 * SIH 2026 Project: BlazeGuard
 */

// ==========================================
// 1. CONFIGURATION (DEMO SIMULATION SETTINGS)
// ==========================================
const DEMO_WAIT_TIME = 15; // Complete virtual demo wait in ~15 seconds
const POSITION_STEPS = [1245, 1000, 750, 500, 250, 100, 25, 1];
const BACKEND_URL = "http://127.0.0.1:8000";
const STATUS_POLL_INTERVAL = 5000;

// Fixed demonstration subject marks dataset
const DEMO_MARKS = [
  { code: "BXE101", name: "Basic Electronics", max: 100, obtained: 82, grade: "A" },
  { code: "PPS102", name: "Programming", max: 100, obtained: 78, grade: "A" },
  { code: "MATH103", name: "Mathematics", max: 100, obtained: 85, grade: "A" },
  { code: "CHEM104", name: "Chemistry", max: 100, obtained: 74, grade: "B+" },
  { code: "DS105", name: "Data Structures", max: 100, obtained: 88, grade: "A+" }
];

let queueIntervalTimer = null;
let statusPollTimer = null;
let statusRequestController = null;

// ==========================================
// 2. INITIALIZATION & REFRESH RECOVERY
// ==========================================
document.addEventListener("DOMContentLoaded", () => {
  initNavigation();
  initFormHandling();
  initResultActions();
  restoreSession(); // Checks and restores queue/result if refreshed
});

function initNavigation() {
  const navToggle = document.getElementById("navToggle");
  const siteNav = document.getElementById("siteNav");
  if (navToggle && siteNav) {
    navToggle.addEventListener("click", () => siteNav.classList.toggle("open"));
  }

  const navHomeLink = document.getElementById("navHomeLink");
  if (navHomeLink) {
    navHomeLink.addEventListener("click", (e) => {
      e.preventDefault();
      resetPortal();
    });
  }
}

function initResultActions() {
  const printBtn = document.getElementById("printResultBtn");
  if (printBtn) {
    printBtn.addEventListener("click", () => window.print());
  }

  const searchAgainBtn = document.getElementById("searchAgainBtn");
  if (searchAgainBtn) {
    searchAgainBtn.addEventListener("click", resetPortal);
  }
}

// ==========================================
// 3. FORM VALIDATION & SUBMISSION
// ==========================================
function initFormHandling() {
  const form = document.getElementById("resultSearchForm");
  if (!form) return;

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    clearErrors();

    const seatNumber = document.getElementById("seatNumber").value.trim();
    const motherName = document.getElementById("motherName").value.trim();
    const dob = document.getElementById("dob").value;
    const examSession = document.getElementById("examSession").value;
    const examType = document.getElementById("examType").value;

    let isValid = true;

    if (!examSession) {
      showError("examSession", "Please select an examination session.");
      isValid = false;
    }

    if (!examType) {
      showError("examType", "Please select an examination type.");
      isValid = false;
    }

    if (!seatNumber) {
      showError("seatNumber", "Seat number cannot be empty.");
      isValid = false;
    } else if (!/^[a-zA-Z0-9]{4,12}$/.test(seatNumber)) {
      showError("seatNumber", "Enter a valid seat number (4-12 alphanumeric characters).");
      isValid = false;
    }

    if (!motherName) {
      showError("motherName", "Mother's name cannot be empty.");
      isValid = false;
    } else if (!/^[a-zA-Z\s]{2,30}$/.test(motherName)) {
      showError("motherName", "Please enter a valid mother's name (letters only).");
      isValid = false;
    }

    if (!dob) {
      showError("dob", "Please select Date of Birth.");
      isValid = false;
    }

    if (!isValid) return;

    // Build the dynamic student object from actual inputs
    const studentData = {
      session: examSession,
      examType: examType,
      seatNumber: seatNumber,
      motherName: motherName,
      dob: formatDate(dob),
      prn: `DEMO2026${seatNumber.slice(-4)}`
    };

    // Begin In-Place Smart Virtual Queue Flow
    initiateQueue(studentData);
  });
}

function showError(fieldId, msg) {
  const input = document.getElementById(fieldId);
  const error = document.getElementById(`${fieldId}Error`);
  if (input) input.classList.add("has-error");
  if (error) error.textContent = `⚠ ${msg}`;
}

function clearErrors() {
  document.querySelectorAll(".has-error").forEach(el => el.classList.remove("has-error"));
  document.querySelectorAll(".error-message").forEach(el => el.textContent = "");
}

function formatDate(rawDateStr) {
  if (!rawDateStr) return "--";
  const parts = rawDateStr.split("-");
  if (parts.length === 3) {
    return `${parts[2]}/${parts[1]}/${parts[0]}`; // Convert YYYY-MM-DD to DD/MM/YYYY
  }
  return rawDateStr;
}

// ==========================================
// 4. SMART VIRTUAL QUEUE STATE MACHINE
// ==========================================

function initiateQueue(studentData) {
  const randomTicket = Math.floor(10000 + Math.random() * 90000);
  const queueId = `DEMO-${randomTicket}`;

  const sessionState = {
    studentData: studentData,
    queueId: queueId,
    queuePosition: 1245,
    queueStatus: "WAITING",
    startTime: Date.now()
  };

  // Save state for refresh resilience
  sessionStorage.setItem("mdu_portal_session", JSON.stringify(sessionState));

  showQueueScreen(sessionState);
  startBackendStatusPolling(sessionState);
  startQueueDemo(sessionState, 0);
}

function showQueueScreen(sessionState) {
  document.getElementById("searchView").style.display = "none";
  document.getElementById("resultView").style.display = "none";
  document.getElementById("queueView").style.display = "block";

  document.getElementById("queueIdDisplay").textContent = sessionState.queueId;
  document.getElementById("queueSeatDisplay").textContent = sessionState.studentData.seatNumber;
}

function startBackendStatusPolling(sessionState) {
  stopBackendStatusPolling();
  updateBackendStatus(sessionState, 0);
  statusPollTimer = setInterval(() => {
    const elapsed = Math.floor((Date.now() - sessionState.startTime) / 1000);
    updateBackendStatus(sessionState, Math.min(elapsed, DEMO_WAIT_TIME));
  }, STATUS_POLL_INTERVAL);
}

function stopBackendStatusPolling() {
  if (statusPollTimer) clearInterval(statusPollTimer);
  statusPollTimer = null;
  if (statusRequestController) statusRequestController.abort();
  statusRequestController = null;
}

async function updateBackendStatus(sessionState, elapsedSeconds) {
  if (statusRequestController) statusRequestController.abort();
  const controller = new AbortController();
  statusRequestController = controller;
  const timeout = setTimeout(() => controller.abort(), 5000);
  const etaSeconds = Math.max(DEMO_WAIT_TIME - elapsedSeconds, 0);

  try {
    const response = await fetch(
      `${BACKEND_URL}/system-status?eta_seconds=${etaSeconds}`,
      { signal: controller.signal }
    );
    if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);

    const payload = await response.json();
    if (!["NORMAL", "WARNING", "CRITICAL", "DELAY"].includes(payload.decision)) {
      throw new Error("Backend returned an invalid decision");
    }

    document.getElementById("systemStatusDisplay").textContent = payload.decision;
    document.getElementById("systemStatusDetails").textContent =
      `Live CPU ${payload.metrics.cpu_percent}% | RAM ${payload.metrics.ram_percent}%`;
  } catch (error) {
    if (error.name === "AbortError") return;
    document.getElementById("systemStatusDisplay").textContent = "UNAVAILABLE";
    document.getElementById("systemStatusDetails").textContent =
      "BlazeGuard backend is unavailable. The demonstration queue is still running.";
  } finally {
    clearTimeout(timeout);
  }
}

function startQueueDemo(sessionState, initialElapsed) {
  let elapsed = initialElapsed;
  const totalSteps = POSITION_STEPS.length;
  const stepInterval = DEMO_WAIT_TIME / totalSteps;

  if (queueIntervalTimer) clearInterval(queueIntervalTimer);

  function tick() {
    elapsed++;

    const progressRatio = Math.min(elapsed / DEMO_WAIT_TIME, 1);
    const progressPercent = Math.round(progressRatio * 100);

    const stepIndex = Math.min(Math.floor(elapsed / stepInterval), totalSteps - 1);
    const currentPosition = POSITION_STEPS[stepIndex];

    updateQueuePosition(currentPosition, progressPercent);

    if (elapsed >= DEMO_WAIT_TIME) {
      clearInterval(queueIntervalTimer);
      showTurnArrival(sessionState.studentData);
    }
  }

  // Draw initial position
  const initialIndex = Math.min(Math.floor(elapsed / stepInterval), totalSteps - 1);
  updateQueuePosition(POSITION_STEPS[initialIndex], Math.round((elapsed / DEMO_WAIT_TIME) * 100));

  queueIntervalTimer = setInterval(tick, 1000);
}

function updateQueuePosition(positionNum, percent) {
  document.getElementById("queuePositionDisplay").textContent = `#${positionNum}`;
  document.getElementById("queueEtaDisplay").textContent = "~5 minutes";
  document.getElementById("queueProgressBar").style.width = `${percent}%`;
  document.getElementById("queueProgressPercent").textContent = `${percent}%`;
}

async function showTurnArrival(studentData) {
  // Update Status: YOUR TURN HAS ARRIVED
  const heading = document.getElementById("queueHeading");
  heading.textContent = "YOUR TURN HAS ARRIVED";
  heading.style.color = "#22543d";
  document.getElementById("queueSubtitle").textContent = "Your request is now admitted to the result service.";

  document.getElementById("queuePositionDisplay").textContent = "#1";
  document.getElementById("queueEtaDisplay").textContent = "Now";
  document.getElementById("queueProgressBar").style.width = "100%";
  document.getElementById("queueProgressPercent").textContent = "100%";

  const statusPill = document.getElementById("queueStatusDisplay");
  statusPill.textContent = "READY";
  statusPill.className = "status-pill status-pill-ready";

  document.getElementById("queueProgressText").textContent = "Admission granted. Safe capacity verified.";

  // Brief pause (1.5s) to allow student to see "Turn Arrived"
  await new Promise(r => setTimeout(r, 1500));

  // Show Processing Overlay
  showProcessing(studentData);
}

async function showProcessing(studentData) {
  stopBackendStatusPolling();
  const processingBox = document.getElementById("queueProcessingState");
  processingBox.style.display = "block";
  processingBox.scrollIntoView({ behavior: "smooth" });

  // 2-second realistic mark sheet decryption delay
  await new Promise(r => setTimeout(r, 2000));

  // Transition from Queue -> Final Result View on the SAME page
  showDemoResult(studentData);
}

// ==========================================
// 5. FINAL RESULT RENDERING (IN-PLACE)
// ==========================================

function showDemoResult(studentData) {
  // Hide search and queue containers
  document.getElementById("searchView").style.display = "none";
  document.getElementById("queueView").style.display = "none";
  document.getElementById("portalIntroSection").style.display = "none";

  // Display Result Container
  const resultView = document.getElementById("resultView");
  resultView.style.display = "block";

  // Populate Student's Entered Input Dynamically
  document.getElementById("resExamSession").textContent = studentData.session;
  document.getElementById("resExamType").textContent = studentData.examType;
  document.getElementById("resSeatNumber").textContent = studentData.seatNumber;
  document.getElementById("resMotherName").textContent = studentData.motherName;
  document.getElementById("resDob").textContent = studentData.dob;
  document.getElementById("resPrn").textContent = studentData.prn;

  // Populate Marks Table
  const tableBody = document.getElementById("marksTableBody");
  tableBody.innerHTML = "";

  let totalMax = 0;
  let totalObtained = 0;

  DEMO_MARKS.forEach((subject) => {
    totalMax += subject.max;
    totalObtained += subject.obtained;

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>${subject.code}</strong></td>
      <td>${subject.name}</td>
      <td class="center">${subject.max}</td>
      <td class="center"><strong>${subject.obtained}</strong></td>
      <td class="center">${subject.grade}</td>
    `;
    tableBody.appendChild(tr);
  });

  // Calculate Aggregates
  const percentage = (totalObtained / totalMax) * 100;
  document.getElementById("resTotalMarks").textContent = `${totalObtained} / ${totalMax}`;
  document.getElementById("resPercentage").textContent = `${percentage.toFixed(2)}%`;

  // Update session state as COMPLETED
  sessionStorage.setItem("mdu_portal_session", JSON.stringify({
    status: "RESULT_DISPLAYED",
    studentData: studentData
  }));

  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ==========================================
// 6. REFRESH RESTORATION & PORTAL RESET
// ==========================================

function restoreSession() {
  const rawSaved = sessionStorage.getItem("mdu_portal_session");
  if (!rawSaved) return;

  const sessionState = JSON.parse(rawSaved);

  if (sessionState.status === "RESULT_DISPLAYED") {
    showDemoResult(sessionState.studentData);
    return;
  }

  const elapsedSeconds = Math.floor((Date.now() - sessionState.startTime) / 1000);
  showQueueScreen(sessionState);
  startBackendStatusPolling(sessionState);

  if (elapsedSeconds >= DEMO_WAIT_TIME) {
    showTurnArrival(sessionState.studentData);
  } else {
    startQueueDemo(sessionState, elapsedSeconds);
  }
}

function resetPortal() {
  if (queueIntervalTimer) clearInterval(queueIntervalTimer);
  stopBackendStatusPolling();
  sessionStorage.removeItem("mdu_portal_session");

  // Reset form
  const form = document.getElementById("resultSearchForm");
  if (form) form.reset();
  clearErrors();

  // Reset Queue UI elements
  const heading = document.getElementById("queueHeading");
  heading.textContent = "YOU ARE IN QUEUE";
  heading.style.color = "#1a365d";
  document.getElementById("queueSubtitle").textContent = "Your request has been successfully received.";

  const statusPill = document.getElementById("queueStatusDisplay");
  statusPill.textContent = "WAITING";
  statusPill.className = "status-pill status-pill-waiting";

  document.getElementById("queueProcessingState").style.display = "none";
  document.getElementById("systemStatusDisplay").textContent = "CHECKING";
  document.getElementById("systemStatusDetails").textContent = "Connecting to the live backend...";

  // Switch back to Search Form
  document.getElementById("queueView").style.display = "none";
  document.getElementById("resultView").style.display = "none";
  document.getElementById("portalIntroSection").style.display = "block";
  document.getElementById("searchView").style.display = "block";

  window.scrollTo({ top: 0, behavior: "smooth" });
}