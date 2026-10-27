// Shared helpers for the phone page and the host page (no build step, no framework).
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtDur(sec) {
  sec = Math.round(sec || 0);
  if (!sec) return "";
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

function fmtKey(k) { return k > 0 ? `+${k}` : String(k); }

class ApiError extends Error {
  constructor(status, detail) { super(detail); this.status = status; }
}

async function api(method, path, body, headers = {}) {
  const opts = { method, headers: { ...headers }, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new ApiError(0, "Can't reach the karaoke server");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new ApiError(res.status, typeof d === "string" ? d : (d && d[0] && d[0].msg) || res.statusText);
  }
  return data;
}

// WebSocket with reconnect + backoff. onMessage(msg); onStatus(connected: bool).
function connectWS(onMessage, onStatus) {
  let backoff = 500;
  let retry = null;          // pending reconnect timer while disconnected
  function open() {
    retry = null;
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onopen = () => { backoff = 500; onStatus && onStatus(true); };
    ws.onmessage = ev => { try { onMessage(JSON.parse(ev.data)); } catch (e) { console.error(e); } };
    ws.onclose = () => {
      onStatus && onStatus(false);
      retry = setTimeout(open, backoff);
      backoff = Math.min(backoff * 2, 10000);
    };
  }
  open();
  // phones sleep: reconnect at once when the page becomes visible again
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    backoff = 500;
    if (retry) {
      clearTimeout(retry);
      open();
    }
  });
}

let toastTimer;
function toast(msg, ms = 2600) {
  let el = $("#toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast"; el.className = "toast"; el.setAttribute("role", "status");
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

const STATUS = {
  waiting: ["In line for processing", "muted"],
  preparing: ["Preparing…", "warn"],
  ready: ["Ready", "ok"],
  playing: ["Now playing", "now"],
  failed: ["Failed", "bad"],
};

function statusBadge(e) {
  let [label, cls] = STATUS[e.status] || [e.status, "muted"];
  if (e.source === "youtube" && e.status === "ready") label = "Ready";
  if (e.status === "preparing" && e.job) {
    if (e.job.state === "separating") label = `Removing vocals… ${Math.round((e.job.progress || 0) * 100)}%`;
    else if (e.job.state === "lyrics") label = "Syncing lyrics…";
    else if (e.job.state === "rendering") label = "Mixing…";
  }
  return `<span class="badge ${cls}">${esc(label)}</span>`;
}

function sourceBadge(source) {
  return source === "youtube" ? `<span class="badge yt">YouTube</span>` : `<span class="badge lib">Library</span>`;
}

function singerNames(e) { return e.singers.map(s => s.name).join(" & "); }

// job_progress events carry the main job's song/key/level; patch matching entries in place.
function applyJobProgress(view, job) {
  if (!view || job.kind !== "main") return false;
  let hit = false;
  for (const e of view.upcoming || []) {
    if (e.song_id === job.song_id && e.key_shift === job.key_shift && e.vocal_level === job.vocal_level && e.job) {
      e.job = { state: job.state, progress: job.progress };
      hit = true;
    }
  }
  return hit;
}
