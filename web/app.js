// Phone page (SPEC section 7): join with a name, search + queue songs, see the queue live, send reactions.
"use strict";

const GUEST_KEY = "karaoke.guest";
const KEYS = [-3, -2, -1, 0, 1, 2, 3];

let guest = loadGuest();       // {id, name}
let view = null;               // queue view from the WebSocket / GET /api/queue
let guests = [];               // for the duet picker
let results = { q: "", library: [], youtube: null, goodMatch: false, submitted: false, ytError: "", popular: false };
let optionsOpen = null;        // song id whose options are expanded
const songOpts = {};           // song id -> {key, partner}
let altsOpen = false;
let searchSeq = 0;

function loadGuest() {
  try { return JSON.parse(localStorage.getItem(GUEST_KEY)) || null; } catch (e) { return null; }
}
function saveGuest(g) {
  guest = g;
  try { localStorage.setItem(GUEST_KEY, JSON.stringify(g)); } catch (e) { /* private mode: lives for this visit */ }
}

// API call as this guest; if the server forgot us (fresh database), join again under the same name and retry.
async function gapi(method, path, body) {
  try {
    return await api(method, path, body, { "X-Guest-Id": guest.id });
  } catch (e) {
    if (e.status === 401 && guest && guest.name) {
      saveGuest(await api("POST", "/api/guest", { name: guest.name }));
      return api(method, path, body, { "X-Guest-Id": guest.id });
    }
    throw e;
  }
}

// --- join -----------------------------------------------------------------------------------------------

function showJoin(prefill = "") {
  $("#mainView").hidden = true;
  $("#joinView").hidden = false;
  $("#joinName").value = prefill;
  $("#joinName").focus();
}

$("#joinForm").addEventListener("submit", async ev => {
  ev.preventDefault();
  const name = $("#joinName").value.trim();
  if (!name) return;
  try {
    saveGuest(await api("POST", "/api/guest", { name }));
    start();
  } catch (e) {
    $("#joinErr").textContent = e.message;
  }
});

$("#rename").addEventListener("click", () => {
  if (confirm("Join with a different name? Songs you already queued stay in the queue.")) showJoin();
});

// --- tabs -----------------------------------------------------------------------------------------------

function selectTab(name) {
  const search = name === "search";
  $("#tabSearch").setAttribute("aria-selected", search);
  $("#tabQueue").setAttribute("aria-selected", !search);
  $("#searchPanel").hidden = !search;
  $("#queuePanel").hidden = search;
}
$("#tabSearch").addEventListener("click", () => selectTab("search"));
$("#tabQueue").addEventListener("click", () => selectTab("queue"));

// --- search ---------------------------------------------------------------------------------------------

let typingTimer;
$("#q").addEventListener("input", () => {
  clearTimeout(typingTimer);
  typingTimer = setTimeout(() => librarySearch($("#q").value), 150);
});

$("#searchForm").addEventListener("submit", ev => {
  ev.preventDefault();
  clearTimeout(typingTimer);
  $("#q").blur();
  submitSearch($("#q").value, false);
});

async function librarySearch(q) {
  const seq = ++searchSeq;
  q = q.trim();
  try {
    if (!q) {
      const data = await api("GET", "/api/library?sort=most_played&limit=15");
      if (seq !== searchSeq) return;
      results = { q: "", library: data.songs, youtube: null, goodMatch: false, submitted: false, ytError: "", popular: true };
    } else {
      const data = await api("GET", `/api/search?q=${encodeURIComponent(q)}`);
      if (seq !== searchSeq) return;
      results = { q, library: data.library, youtube: null, goodMatch: data.good_match, submitted: false, ytError: "", popular: false };
    }
  } catch (e) {
    if (seq !== searchSeq) return;
    results = { ...results, library: [], ytError: e.message };
  }
  altsOpen = false;
  renderResults();
}

// Explicit submit: YouTube is searched only now, and only when the library has no good match (or on "anyway").
async function submitSearch(q, force) {
  q = q.trim();
  if (!q) return;
  const seq = ++searchSeq;
  results = { ...results, q, submitted: true, ytLoading: true, ytError: "" };
  renderResults();
  try {
    const data = await gapi("GET", `/api/search?q=${encodeURIComponent(q)}&youtube=1${force ? "&force=1" : ""}`);
    if (seq !== searchSeq) return;
    results = { q, library: data.library, youtube: data.youtube, goodMatch: data.good_match, submitted: true, ytError: "", popular: false, forced: force };
  } catch (e) {
    if (seq !== searchSeq) return;
    results = { ...results, ytLoading: false, ytError: e.message };
  }
  altsOpen = false;
  renderResults();
}

function opts(id) { return songOpts[id] || (songOpts[id] = { key: 0, partner: "" }); }

function libraryCard(s) {
  const o = opts(s.id);
  const open = optionsOpen === s.id;
  const others = guests.filter(g => g.id !== guest.id);
  const partnerOpts = [`<option value="">Solo</option>`]
    .concat(others.map(g => `<option value="${esc(g.id)}" ${o.partner === g.id ? "selected" : ""}>Duet with ${esc(g.name)}</option>`))
    .concat([`<option value="__new">Duet with someone else…</option>`]).join("");
  const chosen = [o.key ? `key ${fmtKey(o.key)}` : "", o.partner ? "duet" : ""].filter(Boolean).join(", ");
  return `<div class="card" data-song="${esc(s.id)}">
    <div class="song">
      <div class="meta">
        <div class="title">${esc(s.title)}</div>
        <div class="sub">${esc(s.artist || "Unknown artist")}${s.duration ? " · " + fmtDur(s.duration) : ""} ${sourceBadge("library")}</div>
      </div>
      <button class="btn" type="button" data-act="sing-lib">Sing</button>
    </div>
    <button class="link" type="button" data-act="toggle-opts" aria-expanded="${open}">${open ? "Hide options" : "Key & duet"}${!open && chosen ? ` (${esc(chosen)})` : ""}</button>
    <div class="options" ${open ? "" : "hidden"}>
      <label>Key shift
        <div class="stepper">
          <button type="button" data-act="key-down" aria-label="Lower key" ${o.key <= -3 ? "disabled" : ""}>−</button>
          <output>${o.key ? fmtKey(o.key) : "Original"}</output>
          <button type="button" data-act="key-up" aria-label="Raise key" ${o.key >= 3 ? "disabled" : ""}>+</button>
        </div>
      </label>
      <label>Singers <select data-act="partner">${partnerOpts}</select></label>
    </div>
  </div>`;
}

function ytRow(v, isPick) {
  const sub = [v.channel, fmtDur(v.duration)].filter(Boolean).map(esc).join(" · ");
  if (isPick) {
    return `<div class="song">
      <div class="meta"><div class="title">${esc(v.title)}</div><div class="sub">${sub} ${sourceBadge("youtube")}</div></div>
      <button class="btn" type="button" data-act="sing-yt" data-vid="${esc(v.video_id)}">Sing</button>
    </div>`;
  }
  return `<div class="alt">
    <div class="meta"><div class="title">${esc(v.title)}</div><div class="sub">${sub}</div></div>
    <button class="btn small ghost" type="button" data-act="sing-yt" data-vid="${esc(v.video_id)}">Sing</button>
  </div>`;
}

function renderResults() {
  const r = results;
  let html = "";
  if (r.popular) {
    html += r.library.length
      ? `<div class="section-title">Most sung</div>` + r.library.map(libraryCard).join("")
      : `<div class="empty">Type a song or artist to search.</div>`;
    $("#results").innerHTML = html;
    return;
  }
  if (r.library.length) {
    html += `<div class="section-title">In the library</div>` + r.library.slice(0, 12).map(libraryCard).join("");
  } else if (r.q && !r.submitted) {
    html += `<div class="hint">No library match yet. Press Search to look on YouTube.</div>`;
  }
  if (r.submitted) {
    if (r.ytLoading) {
      html += `<div class="hint">Searching…</div>`;
    } else if (r.ytError) {
      html += `<div class="card"><div class="err">${esc(r.ytError)}</div></div>`;
    } else if (r.youtube) {
      const vids = r.youtube.results;
      html += `<div class="section-title">From YouTube</div>`;
      if (!vids.length) {
        html += `<div class="card">Nothing found for “${esc(r.q)}”. It's on the wishlist for the host now.</div>`;
      } else {
        html += `<div class="card">${ytRow(vids[0], true)}`;
        if (vids.length > 1) {
          html += `<button class="link" type="button" data-act="toggle-alts" aria-expanded="${altsOpen}">${altsOpen ? "Hide alternatives" : "Not this one?"}</button>`;
        }
        if (altsOpen || vids.length === 1) {
          html += `<div class="alts">${vids.slice(1).map(v => ytRow(v, false)).join("")}
            <button class="link" type="button" data-act="wish">None of these: add to the wishlist</button></div>`;
        }
        html += `</div>`;
      }
    } else if (r.goodMatch && !r.forced) {
      html += `<button class="link" type="button" data-act="force-yt">Not the right song? Search YouTube</button>`;
    }
  }
  $("#results").innerHTML = html;
}

$("#results").addEventListener("click", async ev => {
  const btn = ev.target.closest("[data-act]");
  if (!btn || btn.tagName === "SELECT") return;
  const card = btn.closest("[data-song]");
  const id = card && card.dataset.song;
  switch (btn.dataset.act) {
    case "toggle-opts":
      optionsOpen = optionsOpen === id ? null : id;
      if (optionsOpen) loadGuests();
      renderResults();
      break;
    case "key-down": opts(id).key = Math.max(-3, opts(id).key - 1); renderResults(); break;
    case "key-up": opts(id).key = Math.min(3, opts(id).key + 1); renderResults(); break;
    case "sing-lib": {
      const o = opts(id);
      const body = { song_id: id, key_shift: o.key };
      if (o.partner) body.singers = [guest.id, o.partner];
      await queueSong(btn, body);
      break;
    }
    case "sing-yt": {
      const v = results.youtube.results.find(x => x.video_id === btn.dataset.vid);
      await queueSong(btn, { video_id: v.video_id, title: v.title, artist: "", duration: v.duration });
      break;
    }
    case "toggle-alts": altsOpen = !altsOpen; renderResults(); break;
    case "wish":
      try {
        await gapi("POST", "/api/wishlist", { query: results.q });
        toast("Added to the wishlist for the host");
        results = { ...results, youtube: null, submitted: false };
        renderResults();
      } catch (e) { toast(e.message); }
      break;
    case "force-yt": submitSearch(results.q, true); break;
  }
});

$("#results").addEventListener("change", async ev => {
  if (ev.target.dataset.act !== "partner") return;
  const id = ev.target.closest("[data-song]").dataset.song;
  let value = ev.target.value;
  if (value === "__new") {
    const name = (prompt("Your duet partner's name") || "").trim();
    value = "";
    if (name) {
      try {
        const g = await api("POST", "/api/guest", { name });
        guests.push(g);
        value = g.id;
      } catch (e) { toast(e.message); }
    }
  }
  opts(id).partner = value;
  renderResults();
});

async function loadGuests() {
  try {
    guests = (await api("GET", "/api/guests")).guests;
    // one row per name (someone may have joined twice); our own row always wins
    const seen = new Map();
    for (const g of guests) if (!seen.has(g.name.toLowerCase()) || g.id === guest.id) seen.set(g.name.toLowerCase(), g);
    guests = [...seen.values()];
    if (optionsOpen) renderResults();
  } catch (e) { /* the picker just shows Solo */ }
}

async function queueSong(btn, body) {
  btn.disabled = true;
  try {
    const e = await gapi("POST", "/api/queue", body);
    view = await api("GET", "/api/queue");
    renderQueue();
    const pos = view.upcoming.findIndex(x => x.id === e.id);
    toast(`Queued “${e.title}”${pos >= 0 ? ` · #${pos + 1} in line` : ""}`);
    optionsOpen = null;
    renderResults();
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
  }
}

// --- queue ----------------------------------------------------------------------------------------------

function isMine(e) { return e.added_by === guest.id || e.singers.some(s => s.id === guest.id); }

function renderQueue() {
  if (!view) return;
  const up = view.upcoming;
  $("#queueCount").textContent = up.length ? ` · ${up.length}` : "";
  let html = "";
  if (view.playing) {
    const p = view.playing;
    html += `<div class="section-title">Now playing</div>
      <div class="card ${isMine(p) ? "mine" : ""}"><div class="song"><div class="meta">
        <div class="title">${esc(p.title)}</div>
        <div class="sub">${esc(singerNames(p))} ${statusBadge(p)}</div>
      </div></div></div>`;
  }
  html += `<div class="section-title">Up next</div>`;
  if (!up.length) {
    html += `<div class="empty">The queue is empty. Search for a song!</div>`;
  }
  up.forEach((e, i) => {
    const prog = e.status === "preparing" && e.job && e.job.state === "separating"
      ? `<div class="progress"><div style="width:${Math.round((e.job.progress || 0) * 100)}%"></div></div>` : "";
    const details = [e.artist, e.key_shift ? `key ${fmtKey(e.key_shift)}` : ""].filter(Boolean).map(esc).join(" · ");
    html += `<div class="card ${isMine(e) ? "mine" : ""}" data-entry="${e.id}">
      <div class="song">
        <span class="num">${i + 1}</span>
        <div class="meta">
          <div class="title">${esc(e.title)}</div>
          <div class="sub">${esc(singerNames(e))}${details ? " · " + details : ""}</div>
          <div class="sub">${sourceBadge(e.source)} ${statusBadge(e)}</div>
          ${prog}
        </div>
        ${e.added_by === guest.id ? `<button class="btn small danger" type="button" data-act="remove">Remove</button>` : ""}
      </div>
    </div>`;
  });
  $("#queue").innerHTML = html;
}

$("#queue").addEventListener("click", async ev => {
  const btn = ev.target.closest("[data-act=remove]");
  if (!btn) return;
  const id = btn.closest("[data-entry]").dataset.entry;
  const e = view.upcoming.find(x => String(x.id) === id);
  if (!confirm(`Remove “${e ? e.title : "this song"}” from the queue?`)) return;
  btn.disabled = true;
  try {
    await gapi("DELETE", `/api/queue/${id}`);
  } catch (err) {
    toast(err.message);
    btn.disabled = false;
  }
});

// --- reactions ------------------------------------------------------------------------------------------

$("#reactions").addEventListener("click", async ev => {
  const btn = ev.target.closest("button");
  if (!btn) return;
  const all = [...$("#reactions").querySelectorAll("button")];
  all.forEach(b => { b.disabled = true; });
  setTimeout(() => all.forEach(b => { b.disabled = false; }), 1000);   // server allows 1 per second
  try {
    await gapi("POST", "/api/reaction", { emoji: btn.textContent.trim() });
  } catch (e) {
    if (e.status !== 429) toast(e.message);
  }
});

// --- live updates ---------------------------------------------------------------------------------------

function onMessage(msg) {
  if (msg.type === "hello" || msg.type === "queue_changed") {
    view = msg;
    renderQueue();
  } else if (msg.type === "job_progress") {
    if (applyJobProgress(view, msg.job)) renderQueue();
  } else if (msg.type === "library_changed" && !results.submitted) {
    librarySearch($("#q").value);
  }
}

function onStatus(ok) {
  $("#conn").classList.toggle("off", !ok);
  $("#conn").title = ok ? "Connected" : "Reconnecting…";
}

let started = false;
function start() {
  $("#joinView").hidden = true;
  $("#mainView").hidden = false;
  $("#me").textContent = guest.name;
  renderQueue();
  if (started) return;
  started = true;
  connectWS(onMessage, onStatus);
  librarySearch("");
  loadGuests();
}

if (guest && guest.id) start(); else showJoin();
