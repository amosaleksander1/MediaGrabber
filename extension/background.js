// MediaGrabber bridge — service worker.
//
// This extension downloads nothing. It reads cookies for the handful of sites
// MediaGrabber logs into and hands them, or the current tab's link, to the
// MediaGrabber app running on this computer over native messaging.
//
// Why it exists: reading a browser's cookie jar from outside means defeating
// App-Bound Encryption on Windows or the Keychain on macOS. In here the cookies
// are simply available, HttpOnly ones included.

const HOST = "com.mediagrabber.bridge";

// Mirrors LOGIN_DOMAINS in mediagrabber/cookies.py. Keep the two in step: a
// domain here that the app does not know about is harmless, but one missing
// here means that site's login silently never reaches the app.
const DOMAINS = [
  "instagram.com",
  "tiktok.com",
  "x.com",
  "twitter.com",
  "threads.net",
  "threads.com",
  "reddit.com",
];

// Chrome refuses messages over 1 MB. A logged-in profile can hold a lot of
// cookies for these domains, so they go over in batches well under that.
const BATCH_SIZE = 150;

const api = typeof browser !== "undefined" ? browser : chrome;

function send(message) {
  return new Promise((resolve, reject) => {
    api.runtime.sendNativeMessage(HOST, message, (reply) => {
      const err = api.runtime.lastError;
      if (err) {
        // The usual cause is that the app has not registered the native host
        // yet — menu [13] in MediaGrabber.
        reject(new Error(err.message || "native host unavailable"));
        return;
      }
      resolve(reply);
    });
  });
}

async function collectCookies() {
  const all = [];
  for (const domain of DOMAINS) {
    const found = await api.cookies.getAll({ domain });
    all.push(...found);
  }
  // De-duplicate: getAll({domain}) matches subdomains, so the same cookie can
  // come back under more than one query.
  const seen = new Set();
  return all.filter((c) => {
    const key = `${c.domain}|${c.path}|${c.name}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

async function pushCookies() {
  const cookies = await collectCookies();
  if (cookies.length === 0) {
    return { ok: false, error: "no cookies found — are you logged in?" };
  }

  let written = 0;
  const domains = new Set();
  for (let i = 0; i < cookies.length; i += BATCH_SIZE) {
    const batch = cookies.slice(i, i + BATCH_SIZE);
    // The app truncates on the first batch and appends afterwards, so a stale
    // cookies.txt never survives a refresh.
    const reply = await send({
      type: "cookies",
      cookies: batch,
      append: i > 0,
    });
    if (!reply || !reply.ok) {
      return reply || { ok: false, error: "no reply from MediaGrabber" };
    }
    written += reply.written || 0;
    (reply.domains || []).forEach((d) => domains.add(d));
  }
  return { ok: true, written, domains: [...domains] };
}

async function queueCurrentTab(tab) {
  if (!tab || !tab.url) return { ok: false, error: "no active tab" };
  return send({ type: "queue", url: tab.url });
}

api.runtime.onInstalled.addListener(() => {
  api.contextMenus.create({
    id: "mediagrabber-send",
    title: "Send this page to MediaGrabber",
    contexts: ["page", "link", "image", "video"],
  });
});

api.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "mediagrabber-send") return;
  // Prefer an explicitly clicked link over the page itself.
  const url = info.linkUrl || info.srcUrl || (tab && tab.url);
  try {
    await send({ type: "queue", url });
  } catch (e) {
    console.error("MediaGrabber:", e.message);
  }
});

// The popup drives everything; it asks for work and renders the result.
api.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  const run = async () => {
    try {
      if (msg.action === "ping") return await send({ type: "hello" });
      if (msg.action === "cookies") return await pushCookies();
      if (msg.action === "queue") {
        const [tab] = await api.tabs.query({ active: true, currentWindow: true });
        return await queueCurrentTab(tab);
      }
      return { ok: false, error: `unknown action: ${msg.action}` };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  };
  run().then(sendResponse);
  return true; // keep the channel open for the async reply
});
