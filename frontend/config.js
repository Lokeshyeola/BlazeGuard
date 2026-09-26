// Demo-local frontend configuration. Deployment can replace this value without editing feature code.
window.BLAZEGUARD_CONFIG = Object.freeze({
  backendUrl: "http://127.0.0.1:8000",
  statusPollIntervalMs: 5000
});

// TODO: Production queue state belongs in the backend/Redis, not sessionStorage.