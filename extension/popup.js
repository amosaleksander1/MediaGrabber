// Popup: asks the service worker to do the work and reports what came back.
// No cookie ever passes through this file — the worker talks to the app.

const api = typeof browser !== "undefined" ? browser : chrome;

const statusEl = document.getElementById("status");
const versionEl = document.getElementById("version");
const buttons = [...document.querySelectorAll("button")];

function show(text, kind) {
  statusEl.textContent = text;
  statusEl.className = kind || "";
}

function ask(action) {
  return new Promise((resolve) => {
    api.runtime.sendMessage({ action }, (reply) => {
      resolve(reply || { ok: false, error: "no reply from the extension worker" });
    });
  });
}

async function withBusy(label, fn) {
  buttons.forEach((b) => (b.disabled = true));
  show(label);
  try {
    return await fn();
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}

// Connection check on open, so "is this thing working?" needs no clicking.
(async () => {
  const reply = await ask("ping");
  if (reply.ok) {
    versionEl.textContent = `connected to ${reply.app} v${reply.version}`;
    show("Ready.");
  } else {
    versionEl.textContent = "not connected";
    show(reply.error || "MediaGrabber is not reachable.", "err");
  }
})();

document.getElementById("cookies").addEventListener("click", () =>
  withBusy("Collecting cookies…", async () => {
    const reply = await ask("cookies");
    if (reply.ok) {
      const sites = (reply.domains || []).join(", ") || "no sites";
      show(`Sent ${reply.written} cookie(s).\n${sites}`, "ok");
    } else {
      show(reply.error || "Could not send cookies.", "err");
    }
  })
);

document.getElementById("queue").addEventListener("click", () =>
  withBusy("Sending link…", async () => {
    const reply = await ask("queue");
    if (reply.ok && reply.queued) {
      show("Queued. Run MediaGrabber menu [1] to download.", "ok");
    } else if (reply.ok) {
      show("Already queued.", "ok");
    } else {
      show(reply.error || "Could not queue this page.", "err");
    }
  })
);
