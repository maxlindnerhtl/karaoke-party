// Host page (SPEC section 8): PIN login, reorder/skip/remove, processing status, wishlist, stars, settings.
"use strict";

let view = null;
let jobs = new Map();          // job id -> job
let songs = [];                // whole library, for titles and the starred filter
let songById = new Map();
let wishes = [];

// Host calls use the host_pin cookie set by /api/host/login.
async function hapi(method, path, body) {
  try {
    return await api(method, path, body);
  } catch (e) {
    if (e.status === 401) showLogin();
    throw e;
  }
}

// --- login ----------------------------------------------------------------------------------------------

function showLogin() {
  $("#hostView").hidden = true;
  $("#loginView").hidden = false;
  $("#pin").focus();
}

$("#loginForm").addEventListener("submit", async ev => {
  ev.preventDefault();
  try {
    await api("POST", "/api/host/login", { pin: $("#pin").value });
    $("#pin").value = "";
    $("#loginErr").textContent = "";
    start();
  } catch (e) {
    $("#loginErr").textContent = e.status === 401 ? "Wrong PIN" : e.message;
  }
});

$("#logout").addEventListener("click", async () => {
  await api("POST", "/api/host/logout");
  showLogin();
});

// --- queue ----------------------------------------------------------------------------------------------

function renderPlaying() {
  const p = view && view.playing;
  if (!p) {
    $("#playing").innerHTML = `<div class="card hint" style="margin:0 0 10px">Nothing is playing.</div>`;
    return;
  }
  const pb = view.playback;
  $("#playing").innerHTML = `<div class="card" data-entry="${p.id}"><div class="song">
    <div class="meta">
      <div class="title">${esc(p.title)}</div>
      <div class="sub">${esc(singerNames(p))}${p.artist ? " · " + esc(p.artist) : ""}</div>
      <div class="sub">${sourceBadge(p.source)} ${statusBadge(p)} ${pb.state === "paused" ? `<span class="badge muted">Paused</span>` : ""}
        <span id="pos">${fmtDur(pb.position)}</span>${p.duration ? " / " + fmtDur(p.duration) : ""}</div>
    </div>
    <div class="actions"><button class="btn small danger" type="button" data-act="skip">Skip</button></div>
  </div></div>`;
}

function renderQueue() {
  if (!view) return;
  renderPlaying();
  const up = view.upcoming;
  $("#queueCount").textContent = up.length ? `(${up.length})` : "";
  if (!up.length) {
    $("#queue").innerHTML = `<div class="card empty">The queue is empty.</div>`;
    return;
  }
  $("#queue").innerHTML = up.map((e, i) => {
    const details = [e.artist, e.key_shift ? `key ${fmtKey(e.key_shift)}` : "", e.source === "library" ? `vocals ${e.vocal_level} %` : ""]
      .filter(Boolean).map(esc).join(" · ");
    return `<div class="card" data-entry="${e.id}" data-index="${i}" draggable="true">
      <div class="song">
        <span class="num">${i + 1}</span>
        <div class="meta">
          <div class="title">${esc(e.title)}</div>
          <div class="sub">${esc(singerNames(e))}${details ? " · " + details : ""}</div>
          <div class="sub">${sourceBadge(e.source)} ${statusBadge(e)}</div>
          ${e.status === "failed" && e.error ? `<div class="err">${esc(e.error)}</div>` : ""}
        </div>
        <div class="actions">
          <button class="icon" type="button" data-act="up" aria-label="Move up" ${i === 0 ? "disabled" : ""}>▲</button>
          <button class="icon" type="button" data-act="down" aria-label="Move down" ${i === up.length - 1 ? "disabled" : ""}>▼</button>
        </div>
      </div>
      <div class="row" style="justify-content:flex-end;margin-top:8px">
        ${i > 0 ? `<button class="btn small ghost" type="button" data-act="top">To top</button>` : ""}
        ${e.status === "failed" ? `<button class="btn small ghost" type="button" data-act="retry">Retry</button>` : ""}
        <button class="btn small ghost" type="button" data-act="skip">Skip</button>
        <button class="btn small danger" type="button" data-act="remove">Remove</button>
      </div>
    </div>`;
  }).join("");
}

async function move(id, index) {
  try {
    view = { ...view, ...(await hapi("POST", `/api/queue/${id}/move`, { index })) };
    renderQueue();
  } catch (e) { toast(e.message); }
}

async function onQueueClick(ev) {
  const btn = ev.target.closest("[data-act]");
  if (!btn) return;
  const card = btn.closest("[data-entry]");
  const id = Number(card.dataset.entry);
  const index = Number(card.dataset.index);
  const e = view.playing && view.playing.id === id ? view.playing : view.upcoming.find(x => x.id === id);
  try {
    switch (btn.dataset.act) {
      case "up": await move(id, index - 1); break;
      case "down": await move(id, index + 1); break;
      case "top": await move(id, 0); break;
      case "skip":
        if (confirm(`Skip “${e.title}”?`)) await hapi("POST", `/api/queue/${id}/skip`);
        break;
      case "remove":
        if (confirm(`Remove “${e.title}” from the queue?`)) await hapi("DELETE", `/api/queue/${id}`);
        break;
      case "retry": await hapi("POST", `/api/queue/${id}/retry`); break;
    }
  } catch (err) { toast(err.message); }
}
$("#queue").addEventListener("click", onQueueClick);
$("#playing").addEventListener("click", onQueueClick);

// drag to reorder (mouse; on phones use the arrows)
let dragId = null;
$("#queue").addEventListener("dragstart", ev => {
  const card = ev.target.closest("[data-entry]");
  if (!card) return;
  dragId = Number(card.dataset.entry);
  card.classList.add("dragging");
  ev.dataTransfer.effectAllowed = "move";
  ev.dataTransfer.setData("text/plain", String(dragId));
});
$("#queue").addEventListener("dragend", () => {
  dragId = null;
  document.querySelectorAll(".dragging, .drop-before").forEach(el => el.classList.remove("dragging", "drop-before"));
});
$("#queue").addEventListener("dragover", ev => {
  const card = ev.target.closest("[data-entry]");
  if (dragId === null || !card) return;
  ev.preventDefault();
  document.querySelectorAll(".drop-before").forEach(el => el.classList.remove("drop-before"));
  card.classList.add("drop-before");
});
$("#queue").addEventListener("drop", ev => {
  const card = ev.target.closest("[data-entry]");
  if (dragId === null || !card) return;
  ev.preventDefault();
  const from = view.upcoming.findIndex(x => x.id === dragId);
  let to = Number(card.dataset.index);
  if (from < to) to -= 1;   // dropped before `card`, which shifts up once the dragged entry leaves its place
  if (from !== to && Number(card.dataset.entry) !== dragId) move(dragId, to);
});

// --- processing -----------------------------------------------------------------------------------------

const JOB_LABEL = { queued: "Queued", separating: "Removing vocals", lyrics: "Syncing lyrics", rendering: "Rendering", failed: "Failed", ready: "Done" };

function songLabel(id) {
  const s = songById.get(id);
  return s ? `${s.artist ? s.artist + " – " : ""}${s.title}` : id.slice(0, 8);
}

function renderJobs() {
  const all = [...jobs.values()];
  const active = all.filter(j => ["separating", "lyrics", "rendering"].includes(j.state));
  const queued = all.filter(j => j.state === "queued");
  const failed = all.filter(j => j.state === "failed").sort((a, b) => b.updated_at - a.updated_at).slice(0, 5);
  const line = j => `<div><b>${esc(songLabel(j.song_id))}</b>
    <span class="sub">· ${j.kind === "main" ? "main" : "background"} · key ${fmtKey(j.key_shift)} · vocals ${j.vocal_level} %</span>
    <span class="badge ${j.state === "failed" ? "bad" : j.state === "queued" ? "muted" : "warn"}">${JOB_LABEL[j.state]}${
      j.state === "separating" ? " " + Math.round(j.progress * 100) + "%" : ""}</span>
    ${j.error ? `<div class="err">${esc(j.error)}</div>` : ""}</div>`;
  let html = active.map(line).join("");
  const qMain = queued.filter(j => j.kind === "main");
  html += qMain.map(line).join("");
  const bg = queued.length - qMain.length;
  if (bg) html += `<div class="sub">${bg} background render${bg > 1 ? "s" : ""} waiting (other vocal levels)</div>`;
  html += failed.map(line).join("");
  $("#jobs").innerHTML = html || `<div class="sub">Idle. Everything queued is processed.</div>`;
}

async function loadJobs() {
  try {
    const r = await hapi("GET", "/api/jobs");
    jobs = new Map(r.jobs.map(j => [j.id, j]));
    renderJobs();
    // processing without a CUDA GPU works, but takes several minutes per song
    $("#cpuWarn").hidden = !(r.device && r.device.device === "cpu");
  } catch (e) { /* shown on the next event */ }
}

// --- wishlist -------------------------------------------------------------------------------------------

function renderWishlist() {
  $("#wishlist").innerHTML = wishes.length ? wishes.map(w => `<div class="row" data-wish="${w.id}">
      <div style="flex:1;min-width:0"><b>${esc(w.query)}</b>
        <div class="sub">${w.reason === "no_results" ? "YouTube found nothing" : "YouTube results rejected"}${
          w.guest ? " · " + esc(w.guest) : ""} · ${new Date(w.created_at * 1000).toLocaleString()}</div></div>
      <button class="icon" type="button" data-act="del" aria-label="Remove from wishlist">✕</button>
    </div>`).join("") : `<div class="sub">Empty. Songs nobody could find show up here.</div>`;
}

async function loadWishlist() {
  try {
    wishes = (await hapi("GET", "/api/wishlist")).wishlist;
    renderWishlist();
  } catch (e) { /* ignore */ }
}

$("#wishlist").addEventListener("click", async ev => {
  const btn = ev.target.closest("[data-act=del]");
  if (!btn) return;
  try { await hapi("DELETE", `/api/wishlist/${btn.closest("[data-wish]").dataset.wish}`); } catch (e) { toast(e.message); }
});

// --- settings -------------------------------------------------------------------------------------------

function fillSettings(s) {
  $("#setVocal").value = String(s.default_vocal_level);
  $("#setNightlyOff").checked = !s.nightly_batch_time;
  $("#setNightly").value = s.nightly_batch_time || "03:00";
  $("#setNightly").disabled = !s.nightly_batch_time;
  $("#setOffset").value = s.global_offset_ms;
}

$("#setNightlyOff").addEventListener("change", () => { $("#setNightly").disabled = $("#setNightlyOff").checked; });

$("#settingsForm").addEventListener("submit", async ev => {
  ev.preventDefault();
  try {
    const s = await hapi("POST", "/api/host/settings", {
      default_vocal_level: Number($("#setVocal").value),
      nightly_batch_time: $("#setNightlyOff").checked ? "" : $("#setNightly").value,
      global_offset_ms: Math.round(Number($("#setOffset").value) || 0),
    });
    fillSettings(s);
    $("#settingsMsg").textContent = "Saved";
    setTimeout(() => { $("#settingsMsg").textContent = ""; }, 2000);
  } catch (e) { $("#settingsMsg").textContent = e.message; }
});

// --- library + stars ------------------------------------------------------------------------------------

async function loadSongs() {
  try {
    songs = (await api("GET", "/api/library?sort=title&limit=100000")).songs;
    songById = new Map(songs.map(s => [s.id, s]));
    renderLibrary();
    renderJobs();
  } catch (e) { /* ignore */ }
}

let libSeq = 0;
async function renderLibrary() {
  const q = $("#libQ").value.trim();
  const seq = ++libSeq;
  let list = songs;
  if (q) {
    try { list = (await api("GET", `/api/library?q=${encodeURIComponent(q)}&limit=50`)).songs; } catch (e) { list = []; }
    if (seq !== libSeq) return;
  }
  if ($("#libStarred").checked) list = list.filter(s => (songById.get(s.id) || s).starred);
  const shown = list.slice(0, 50);
  $("#library").innerHTML = shown.map(s => {
    const on = !!(songById.get(s.id) || s).starred;
    return `<div class="row" data-song="${esc(s.id)}">
      <button class="star ${on ? "on" : ""}" type="button" data-act="star" aria-pressed="${on}" aria-label="Star">${on ? "★" : "☆"}</button>
      <div style="flex:1;min-width:0"><b>${esc(s.title)}</b> <span class="sub">${esc(s.artist)} · played ${s.play_count}×</span></div>
    </div>`;
  }).join("") + (list.length > shown.length ? `<div class="sub">${list.length - shown.length} more: search to narrow down</div>` : "")
    || `<div class="sub">No songs.</div>`;
}

let libTimer;
$("#libQ").addEventListener("input", () => { clearTimeout(libTimer); libTimer = setTimeout(renderLibrary, 150); });
$("#libStarred").addEventListener("change", renderLibrary);

$("#library").addEventListener("click", async ev => {
  const btn = ev.target.closest("[data-act=star]");
  if (!btn) return;
  const id = btn.closest("[data-song]").dataset.song;
  const s = songById.get(id);
  try {
    const r = await hapi("POST", `/api/library/${id}/star`, { starred: !(s && s.starred) });
    if (s) s.starred = r.starred ? 1 : 0;
    renderLibrary();
  } catch (e) { toast(e.message); }
});

$("#rescan").addEventListener("click", async () => {
  try {
    const r = await hapi("POST", "/api/library/rescan");
    toast(`Rescan: ${r.added} added, ${r.updated} updated, ${r.missing} missing`);
    loadSongs();
  } catch (e) { toast(e.message); }
});

$("#statsBtn").addEventListener("click", async () => {
  try {
    await hapi("POST", "/api/host/show_stats");
    toast("Stats sent to the TV");
  } catch (e) { toast(e.message); }
});

// --- live updates ---------------------------------------------------------------------------------------

function onMessage(msg) {
  switch (msg.type) {
    case "hello":
    case "queue_changed":
      view = msg; renderQueue(); break;
    case "job_progress":
      jobs.set(msg.job.id, msg.job);
      renderJobs();
      if (applyJobProgress(view, msg.job)) renderQueue();
      break;
    case "position":
      if (view && view.playing && view.playing.id === msg.entry_id && $("#pos")) $("#pos").textContent = fmtDur(msg.position);
      break;
    case "wishlist_changed": loadWishlist(); break;
    case "library_changed": loadSongs(); break;
    case "settings_changed": fillSettings(msg.settings); break;
  }
}

function onStatus(ok) {
  $("#conn").classList.toggle("off", !ok);
  $("#conn").title = ok ? "Connected" : "Reconnecting…";
  if (ok && started) { loadJobs(); loadWishlist(); }   // catch up on what we missed while offline
}

let started = false;
async function start() {
  $("#loginView").hidden = true;
  $("#hostView").hidden = false;
  if (started) return;
  started = true;
  connectWS(onMessage, onStatus);
  fillSettings(await api("GET", "/api/settings"));
  loadSongs();
  loadJobs();
  loadWishlist();
}

api("GET", "/api/host/check").then(start, showLogin);
