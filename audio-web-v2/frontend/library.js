// Library page: never-empty search, import wizard, batch editing, inspector, saved searches, collections, player.
const API = window.location.protocol === "file:" ? "http://127.0.0.1:8766" : "";
const $ = (id) => document.getElementById(id);
const LIKED = "♥ Liked";
const KIND_ORDER = [["type", "Type"], ["instrument", "Instrument"], ["genre", "Genre"], ["mood", "Mood"], ["year", "Year"], ["other", "Other"]];

const S = {
  q: "", pinned: [], excluded: [], type: "", bpmMin: "", bpmMax: "", key: "", rating: "", collection: "", sort: "relevance",
  page: 1, seed: null, similarTo: null, tracks: [], liked: new Set(), collections: [], saved: [], playingId: null, reqId: 0,
  selected: new Set(), anchor: null, expanded: {}, last: null, inspectId: null, stats: null,
  mimicBlob: null, view: "home", stack: true, sent: "", similarSet: null, simBy: "auto", simGroups: new Set(["timbre", "spectrum", "pitch", "energy", "rhythm"]), analysis: null,
};
const SIM_GROUPS = [["timbre", "Timbre"], ["spectrum", "Brightness"], ["pitch", "Pitch / key"], ["energy", "Energy"], ["rhythm", "Rhythm"]];
function leaveSimilar() { S.similarTo = null; S.mimicBlob = null; S.similarSet = null; }
function simParams() { const p = new URLSearchParams({ by: S.simBy, groups: [...S.simGroups].join(","), page: S.page, per_page: 40 }); return p; }
async function mimicRequest() {
  const fd = new FormData(); fd.append("file", S.mimicBlob, S.mimicBlob.name || "clip.webm"); fd.append("groups", [...S.simGroups].join(","));
  const r = await fetch(API + "/api/mimic", { method: "POST", body: fd });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

// ---------- helpers ----------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (s) => (s == null || isNaN(s) ? "–" : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`);
const fmtBytes = (b) => (b > 1e9 ? (b / 1e9).toFixed(1) + " GB" : (b / 1e6).toFixed(0) + " MB");
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const splitList = (s) => String(s || "").split(/[,;\n]/).map((x) => x.trim()).filter(Boolean);
async function api(path, opts) {
  const r = await fetch(API + path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}
const jpost = (path, body, method = "POST") => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
function toast(msg, action) {
  const t = $("toast2"); t.textContent = msg;
  if (action) { const b = document.createElement("button"); b.textContent = action.label; b.onclick = () => { t.hidden = true; action.fn(); }; t.appendChild(b); }
  t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), action ? 9000 : 2600);
}

// ---------- search ----------
function params() {
  const p = new URLSearchParams();
  p.set("q", S.q);
  S.pinned.forEach((t) => p.append("tag", t)); S.excluded.forEach((t) => p.append("exclude", t));
  if (S.type) p.set("type", S.type);
  if (S.bpmMin) p.set("bpm_min", S.bpmMin);
  if (S.bpmMax) p.set("bpm_max", S.bpmMax);
  if (S.key) p.set("key", S.key);
  if (S.rating) p.set("rating_min", S.rating);
  if (S.collection) p.set("collection", S.collection);
  if (S.sent) p.set("sent", S.sent);
  p.set("stack", S.stack ? "1" : "0");
  p.set("sort", S.sort); p.set("page", S.page); p.set("per_page", 40);
  if (S.seed != null) p.set("seed", S.seed);
  return p;
}

async function runSearch({ append = false, keepPage = false } = {}) {
  if (typeof isIdle === "function") {                       // Discover home vs browsing (extras.js)
    if (!isIdle()) S.view = "browse";
    syncViewPills();
    if (S.view === "home") return showHome();
    hideHome();
  }
  const id = ++S.reqId;
  if (!append && !keepPage) S.page = 1;
  let data;
  try {
    data = S.mimicBlob ? await mimicRequest() : S.similarSet ? await api(`/api/similar-set?ids=${S.similarSet.join(",")}`) : S.similarTo ? await api(`/api/similar/${S.similarTo}?${simParams()}`) : await api(`/api/search?${params()}`);
  } catch (e) {
    $("notes").innerHTML = `<p>${esc(e.message)}</p>`;
    if (S.mimicBlob) S.mimicBlob = null;
    return;
  }
  if (id !== S.reqId) return; // a newer search superseded this one
  S.tracks = append ? S.tracks.concat(data.tracks) : data.tracks;
  if (!append) { const ids = new Set(S.tracks.map((t) => t.id)); S.selected.forEach((i) => !ids.has(i) && S.selected.delete(i)); }
  render(data);
}

function render(d) {
  S.last = d;
  const empty = d.mode === "empty-library";
  $("onboard").hidden = !empty;
  document.querySelector(".bar").hidden = empty;
  const chips = d.chips.map((c, i) => `<span class="chip ${esc(c.kind)}">${esc(c.label)}<button data-chip="${i}" title="remove" aria-label="remove ${esc(c.label)}">×</button></span>`).join("");
  $("chips").innerHTML = chips ? `<span class="lbl">Searching for</span>${chips}` : "";
  $("chips").querySelectorAll("button").forEach((b) => (b.onclick = () => removeChip(d.chips[+b.dataset.chip])));
  $("notes").innerHTML = empty ? "" : d.notes.map((n) => `<p>${esc(n)}</p>`).join("") +
    (d.needs_analysis && !(S.analysis?.job?.state === "running") ? `<p>Sound matching needs your tracks analyzed first. <button id="note-analyze">Analyze now</button></p>` : "");
  $("note-analyze")?.addEventListener("click", () => startAnalysis());
  renderSimBar(d);
  $("alts").innerHTML = d.alternatives.length ? `<span class="lbl">Try instead</span>` + d.alternatives.map((a, i) => `<button class="chip alt" data-alt="${i}">${esc(a.label)}</button>`).join("") : "";
  $("alts").querySelectorAll("button").forEach((b) => (b.onclick = () => removeChip({ kind: d.alternatives[+b.dataset.alt].kind, label: d.alternatives[+b.dataset.alt].drop })));
  const label = { discover: "Discover", exact: `${d.exact_count} exact`, closest: "Closest matches", similar: "Similar tracks" }[d.mode] || "";
  $("count").textContent = empty ? "" : `${d.total.toLocaleString()} tracks · ${label}${d.stacked ? ` · ${d.stacked} stacked as versions` : ""}`;
  renderFacets(d.tag_summary);
  renderRows();
  renderBatchBar();
  $("more").hidden = S.tracks.length >= d.total;
}

function renderSimBar(d) {
  const bar = $("simbar");
  if (d.mode !== "similar") { bar.hidden = true; return; }
  const audio = d.by === "audio" || d.by === "clip";
  bar.hidden = false;
  bar.innerHTML = (d.by === "clip" ? "" : `<span>Match by</span><span class="seg2"><button data-by="auto" class="${S.simBy !== "tags" ? "on" : ""}">Sound</button><button data-by="tags" class="${S.simBy === "tags" ? "on" : ""}">Tags · tempo · key</button></span>`) +
    (audio || S.simBy !== "tags" ? `<span>·</span>` + SIM_GROUPS.map(([g, n]) => `<label><input type="checkbox" data-g="${g}" ${S.simGroups.has(g) ? "checked" : ""}>${n}</label>`).join("") : "");
  bar.querySelectorAll("[data-by]").forEach((b) => (b.onclick = () => { S.simBy = b.dataset.by; runSearch(); }));
  bar.querySelectorAll("[data-g]").forEach((c) => (c.onchange = () => { c.checked ? S.simGroups.add(c.dataset.g) : S.simGroups.delete(c.dataset.g); if (!S.simGroups.size) { S.simGroups.add(c.dataset.g); c.checked = true; } runSearch(); }));
}

function renderFacets(summary) {
  const groups = {};
  summary.slice(0, 60).forEach((f) => (groups[f.kind || "other"] ||= []).push(f));
  const btn = (f) => {
    const cls = S.pinned.includes(f.tag) ? "on" : S.excluded.includes(f.tag) ? "ex" : "";
    return `<button class="facet ${cls}" data-tag="${esc(f.tag)}" title="click to filter · shift-click to exclude">${esc(f.tag)}<span>${f.count}</span></button>`;
  };
  $("facets").innerHTML = KIND_ORDER.filter(([k]) => groups[k]).map(([k, name]) => {
    const list = groups[k], cap = S.expanded[k] ? 99 : 6;
    return `<div class="kind">${name}</div>${list.slice(0, cap).map(btn).join("")}` +
      (list.length > cap ? `<button class="morelink" data-more="${k}">+${list.length - cap} more</button>` : "");
  }).join("");
  $("facets").querySelectorAll(".facet").forEach((b) => (b.onclick = (e) => toggleTag(b.dataset.tag, e.shiftKey)));
  $("facets").querySelectorAll(".morelink").forEach((b) => (b.onclick = () => { S.expanded[b.dataset.more] = true; renderFacets(summary); }));
}

function stars(n) { return n ? `<span class="stars" title="${n} star">${"★".repeat(n)}</span>` : ""; }
function renderRows() {
  const d = S.last, out = [];
  let divided = false;
  S.tracks.forEach((t, i) => {
    if (d.mode === "exact" && !t.match.exact && !divided && i > 0) { out.push(`<div class="divider">Closest others</div>`); divided = true; }
    const manual = t.bpm_source === "manual", est = t.bpm_source === "filename" ? " est" : "";
    const edited = t.overridden.length ? ` · <span class="edited" title="you edited: ${esc(t.overridden.join(", "))}">edited</span>` : "";
    const sub = [t.sample_type, (t.format || "").toUpperCase()].filter(Boolean).map(esc).join(" · ");
    const pct = t.match.pct != null && !t.match.exact ? `${t.match.pct}%` : "";
    const missed = t.match.missed.length ? `missing: ${t.match.missed.join(", ")}` : "";
    const sel = S.selected.has(t.id);
    out.push(`<div class="row-t ${t.id === S.playingId ? "playing" : ""} ${sel ? "sel" : ""}" data-id="${t.id}" data-i="${i}">
      <input type="checkbox" data-act="select" ${sel ? "checked" : ""} aria-label="Select ${esc(t.filename)}">
      <button class="play" data-act="play" aria-label="Play">${t.id === S.playingId && !audio.paused ? "❚❚" : "▶"}</button>
      <div class="name" data-act="inspect" title="${esc(t.path)}">${esc(t.filename)}<small>${sub} ${stars(t.rating)}${edited}${t.match.why ? ` <span class="why">· ${esc(t.match.why)}</span>` : ""}${rowBadges(t)}</small></div>
      <div class="tg">${t.tags.filter((g) => !/^\d{4}$/.test(g)).slice(0, 5).map((g) => `<button data-tag="${esc(g)}" class="${S.pinned.includes(g) ? "on" : ""}" ${t.auto_tags.includes(g) ? 'title="added automatically — remove it from the track to reject it"' : ""}>${t.auto_tags.includes(g) ? "✦ " : ""}${esc(g)}</button>`).join("")}</div>
      <div class="num${est}" title="${manual ? "set by you" : est ? "estimated from the file name" : ""}">${t.bpm ? Math.round(t.bpm) + " bpm" : "–"}</div>
      <div class="num">${esc(t.camelot_key || "–")}</div>
      <div class="num">${fmtTime(t.duration_sec)}</div>
      <div class="acts"><span class="pct" title="${esc(missed)}">${pct}</span>
        <button data-act="like" class="${S.liked.has(t.id) ? "liked" : ""}" title="Like">${S.liked.has(t.id) ? "♥" : "♡"}</button>
        <button data-act="coll" title="Add to collection">＋</button>
        <button data-act="similar" title="More like this">≈</button></div></div>`);
  });
  $("results").innerHTML = out.join("");
}

// ---------- chips / filters ----------
function removeChip(c) {
  if (c.kind === "similar" || c.kind === "clip") leaveSimilar();
  else if (c.kind === "tag" || c.kind === "word") {
    S.pinned = S.pinned.filter((t) => t !== c.label);
    const typed = [c.label, ...((S.last?.corrections || []).filter((k) => k.to === c.label).map((k) => k.from))];
    for (const w of typed) S.q = S.q.replace(new RegExp(`\\b${w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i"), " ");
  } else if (c.kind === "bpm") {
    S.bpmMin = S.bpmMax = ""; $("bpm-min").value = $("bpm-max").value = "";
    S.q = S.q.replace(/\d{2,3}\s*(?:-|–|to)\s*\d{2,3}\s*(?:bpm)?|\d{2,3}\s*bpm|\b\d{2,3}\b/i, " ");
  } else if (c.kind === "key") {
    S.key = ""; $("key").value = "";
    S.q = S.q.replace(/\b(1[0-2]|[1-9])\s?[ab]\b|\b[a-g]\s?(?:#|sharp|flat|b)?\s?(?:major|minor|maj|min)\b/i, " ");
  } else if (c.kind === "type") { S.type = ""; syncType(); S.q = S.q.replace(/\b(loop|one[ -]?shot|full)\b/i, " "); }
  else if (c.kind === "rating") { S.rating = ""; $("rating").value = ""; }
  else if (c.kind === "sent") { S.sent = ""; $("sent").value = ""; }
  S.q = S.q.replace(/\s+/g, " ").trim(); $("q").value = S.q;
  runSearch();
}
function toggleTag(tag, exclude) {
  const list = exclude ? S.excluded : S.pinned, other = exclude ? S.pinned : S.excluded;
  const i = list.indexOf(tag);
  if (i >= 0) list.splice(i, 1); else { list.push(tag); const j = other.indexOf(tag); if (j >= 0) other.splice(j, 1); }
  leaveSimilar(); runSearch();
}
function syncType() { document.querySelectorAll("#type-seg button").forEach((b) => b.classList.toggle("on", b.dataset.type === S.type)); }
document.querySelectorAll("#type-seg button").forEach((b) => (b.onclick = () => { S.type = b.dataset.type; syncType(); runSearch(); }));
["bpm-min", "bpm-max"].forEach((id) => $(id).addEventListener("input", debounce(() => { S.bpmMin = $("bpm-min").value; S.bpmMax = $("bpm-max").value; leaveSimilar(); runSearch(); }, 350)));
$("key").onchange = () => { S.key = $("key").value; leaveSimilar(); runSearch(); };
$("rating").onchange = () => { S.rating = $("rating").value; leaveSimilar(); runSearch(); };
$("sort").onchange = () => { S.sort = $("sort").value; runSearch(); };
$("stack").onchange = () => { S.stack = $("stack").checked; runSearch(); };
$("sent").onchange = () => { S.sent = $("sent").value; leaveSimilar(); runSearch(); };
$("more").onclick = () => { S.page += 1; runSearch({ append: true }); };

// ---------- row interactions + selection ----------
$("results").addEventListener("click", (e) => {
  const row = e.target.closest(".row-t"); if (!row) return;
  const id = +row.dataset.id, t = S.tracks.find((x) => x.id === id);
  const tagBtn = e.target.closest(".tg button");
  if (tagBtn) return toggleTag(tagBtn.dataset.tag, e.shiftKey);
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (act === "select") return select(+row.dataset.i, e.target.checked, e.shiftKey);
  if (act === "play") play(t);
  else if (act === "inspect") openInspector(id);
  else if (act === "versions") openInspector(id);
  else if (act === "like") like(t);
  else if (act === "coll") collectMenu([id], e.target);
  else if (act === "similar") { S.mimicBlob = null; S.similarTo = id; S.q = ""; $("q").value = ""; runSearch(); window.scrollTo({ top: 0, behavior: "smooth" }); }
});
function select(i, on, range) {
  const [a, b] = range && S.anchor != null ? [Math.min(S.anchor, i), Math.max(S.anchor, i)] : [i, i];
  for (let k = a; k <= b; k++) on ? S.selected.add(S.tracks[k].id) : S.selected.delete(S.tracks[k].id);
  S.anchor = i; renderRows(); renderBatchBar();
}
function renderBatchBar() {
  const n = S.selected.size; $("batchbar").hidden = n === 0;
  $("sel-count").textContent = `${n} selected`;
  $("b-all").hidden = S.selected.size >= S.tracks.length;
}
$("b-all").onclick = () => { S.tracks.forEach((t) => S.selected.add(t.id)); renderRows(); renderBatchBar(); };
$("b-clear").onclick = () => { S.selected.clear(); renderRows(); renderBatchBar(); };
$("b-coll").onclick = (e) => collectMenu([...S.selected], e.target);
$("b-analyze").onclick = () => startAnalysis([...S.selected]);
$("b-sent").onclick = (e) => pickPerson(e.target, (p) => recordSends([...S.selected], p));
$("b-group").onclick = async () => { try { await jpost("/api/versions/merge", { ids: [...S.selected] }); toast(`Grouped ${S.selected.size} tracks as versions of one`); S.selected.clear(); afterEdit(); } catch (e) { toast(e.message); } };

// ---------- batch edit dialog ----------
$("b-edit").onclick = () => { $("batch-title").textContent = `Edit ${S.selected.size} tracks`; $("batch-form").reset(); $("batch-err").textContent = ""; $("batch-dlg").showModal(); };
$("batch-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = new FormData($("batch-form")), set = {};
  for (const k of ["sample_type", "bpm", "key"]) if (f.get(k)) set[k] = f.get(k);
  if (f.get("rating") !== "") set.rating = +f.get("rating");
  const body = { ids: [...S.selected], add_tags: splitList(f.get("add")), remove_tags: splitList(f.get("remove")), set, clear: [], collection: f.get("collection").trim() || null };
  if (!body.add_tags.length && !body.remove_tags.length && !Object.keys(set).length && !body.collection) { $("batch-err").textContent = "Fill in at least one field."; return; }
  try {
    const r = await jpost("/api/batch", body);
    $("batch-dlg").close();
    toast(`Edited ${r.changed} of ${r.tracks} tracks`, { label: "Undo", fn: () => undo(r.batch_id) });
    afterEdit();
  } catch (err) { $("batch-err").textContent = err.message; }
};
async function undo(batchId) {
  try { await jpost(`/api/undo/${batchId}`, {}); toast("Undone"); afterEdit(); } catch (e) { toast(e.message); }
}
async function afterEdit() { await Promise.all([runSearch({ keepPage: true }), loadCollections(), loadTagList(), S.inspectId ? openInspector(S.inspectId, true) : 0]); }
document.querySelectorAll("dialog [data-close]").forEach((b) => (b.onclick = () => b.closest("dialog").close()));

// ---------- inspector (single-track edit) ----------
async function openInspector(id, silent) {
  let d;
  try { d = await api(`/api/tracks/${id}`); } catch (e) { return toast(e.message); }
  S.inspectId = id;
  const el = $("inspector"); el.hidden = false;
  const det = d.detected, usr = d.user, eff = d.effective;
  const hint = (field, fmt) => (usr[field] != null ? `<div class="detected">detected: ${det[field] != null ? esc(fmt(det[field])) : "—"}<button data-reset="${field}">reset</button></div>` :
    det[field] != null ? `<div class="detected">detected${d.bpm_source === "filename" && field === "bpm" ? " from file name" : ""}</div>` : `<div class="detected">not detected yet</div>`);
  el.innerHTML = `<button class="x" id="insp-close" style="float:right" aria-label="Close">×</button>
    <h2>${esc(d.filename)}</h2><div class="path">${esc(d.path)}</div>
    <div class="two"><div><label>BPM</label><input id="i-bpm" type="number" min="30" max="300" step="0.1" value="${eff.bpm ?? ""}">${hint("bpm", (v) => v + " bpm")}</div>
      <div><label>Key</label><input id="i-key" value="${esc(eff.key ?? "")}" placeholder="8A / A minor">${hint("key", String)}</div></div>
    <label>Type</label><select id="i-type"><option value="">—</option>${["loop", "one-shot", "track"].map((x) => `<option ${eff.sample_type === x ? "selected" : ""}>${x}</option>`).join("")}</select>${hint("sample_type", String)}
    <label>Rating</label><div class="starpick" id="i-stars">${[1, 2, 3, 4, 5].map((n) => `<button data-n="${n}" class="${(eff.rating || 0) >= n ? "on" : ""}">★</button>`).join("")}</div>
    <label>Tags</label><div class="tagedit" id="i-tags">${d.tags.map((t) => `<span class="chip tag" ${d.auto_tags?.[t] ? 'title="added automatically (' + esc(d.auto_tags[t].replace("audio-mood", "from the audio").replace("genre-suggest", "accepted genre suggestion")) + ') — remove to reject for good"' : ""}>${d.auto_tags?.[t] ? "✦ " : ""}${esc(t)}<button data-rm="${esc(t)}" aria-label="remove ${esc(t)}">×</button></span>`).join("")}</div>
    <input id="i-addtag" list="tag-list" placeholder="add a tag, press Enter" style="margin-top:8px">
    <label>Notes</label><textarea id="i-notes" rows="3" placeholder="Only you see this">${esc(eff.notes ?? "")}</textarea>
    <label>Collections</label><div class="muted">${d.collections.length ? d.collections.map(esc).join(", ") : "none"}</div>
    <label>Edit history</label>${d.history.length ? d.history.map((h) => `<div class="hist ${h.undone ? "undone" : ""}">${esc(h.label)} · ${esc(h.at.slice(0, 16).replace("T", " "))}</div>`).join("") : `<div class="muted">No edits yet. Your files are never changed — edits live in the library.</div>`}`;
  const save = async (set, extra = {}) => {
    try { const r = await jpost("/api/batch", { ids: [id], set, ...extra }); toast("Saved", { label: "Undo", fn: () => undo(r.batch_id) }); afterEdit(); } catch (e) { toast(e.message); openInspector(id, true); }
  };
  $("insp-close").onclick = closeInspector;
  const onChange = (fieldId, field) => ($(fieldId).onchange = () => save({ [field]: $(fieldId).value }));
  onChange("i-bpm", "bpm"); onChange("i-key", "key"); onChange("i-type", "sample_type");
  $("i-notes").onchange = () => save({ notes: $("i-notes").value });
  el.querySelectorAll("[data-reset]").forEach((b) => (b.onclick = async () => { try { const r = await jpost("/api/batch", { ids: [id], clear: [b.dataset.reset] }); toast("Back to detected value", { label: "Undo", fn: () => undo(r.batch_id) }); afterEdit(); } catch (e) { toast(e.message); } }));
  $("i-stars").onclick = (e) => { const n = +e.target.dataset.n; if (n) save({ rating: (eff.rating === n ? 0 : n) }); };
  $("i-tags").onclick = async (e) => { const t = e.target.dataset.rm; if (t) { const r = await jpost("/api/batch", { ids: [id], remove_tags: [t] }); toast(`Removed “${t}”`, { label: "Undo", fn: () => undo(r.batch_id) }); afterEdit(); } };
  $("i-addtag").onkeydown = async (e) => { if (e.key === "Enter" && e.target.value.trim()) { const r = await jpost("/api/batch", { ids: [id], add_tags: splitList(e.target.value) }); toast("Tag added", { label: "Undo", fn: () => undo(r.batch_id) }); afterEdit(); } };
  if (typeof renderInspectorExtras === "function") renderInspectorExtras(d, el);
  if (!silent) el.scrollTop = 0;
}
function closeInspector() { $("inspector").hidden = true; S.inspectId = null; }
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("inspector").hidden && !document.querySelector("dialog[open]") && document.activeElement?.id !== "q") closeInspector(); });

// ---------- search box + autocomplete ----------
let sugItems = [], sugSel = -1;
const suggest = debounce(async () => {
  const text = $("q").value;
  try {
    const d = await api(`/api/suggest?q=${encodeURIComponent(text)}`);
    if ($("q").value !== text) return;
    sugItems = d.items; sugSel = -1;
    $("suggest").innerHTML = sugItems.map((s, i) => `<div class="sg" role="option" data-i="${i}"><span class="ty">${esc(s.type)}</span><span class="la">${esc(s.label)}</span><span class="de">${esc(s.detail || "")}</span></div>`).join("");
    $("suggest").hidden = !sugItems.length;
  } catch { $("suggest").hidden = true; }
}, 120);
function pick(i) {
  const s = sugItems[i]; if (!s) return;
  $("suggest").hidden = true;
  if (s.type === "track") { S.mimicBlob = null; S.similarTo = s.id; S.q = ""; $("q").value = ""; }
  else if (s.type === "surprise") { leaveSimilar(); S.q = ""; $("q").value = ""; S.seed = Math.floor(Math.random() * 1e6); }
  else if (s.type === "hint") return;
  else { const parts = $("q").value.split(/\s+/); parts[parts.length - 1] = s.insert; S.q = $("q").value = parts.join(" ").trim() + " "; leaveSimilar(); }
  runSearch();
}
const runSearchDebounced = debounce(() => runSearch(), 280);
$("q").addEventListener("input", () => { S.q = $("q").value; leaveSimilar(); suggest(); runSearchDebounced(); });
$("q").addEventListener("focus", suggest);
$("q").addEventListener("keydown", (e) => {
  const open = !$("suggest").hidden;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    if (!open) return suggest();
    e.preventDefault(); sugSel = (sugSel + (e.key === "ArrowDown" ? 1 : -1) + sugItems.length) % sugItems.length;
    document.querySelectorAll(".sg").forEach((el, i) => el.classList.toggle("sel", i === sugSel));
  } else if (e.key === "Enter") { if (open && sugSel >= 0) { e.preventDefault(); pick(sugSel); } else { $("suggest").hidden = true; runSearch(); } }
  else if (e.key === "Escape" && open) { e.preventDefault(); $("suggest").hidden = true; }
});
$("suggest").addEventListener("mousedown", (e) => { const el = e.target.closest(".sg"); if (el) { e.preventDefault(); pick(+el.dataset.i); } });
document.addEventListener("click", (e) => { if (!e.target.closest(".searchwrap")) $("suggest").hidden = true; });

// ---------- collections, likes, saved searches ----------
async function loadCollections() {
  S.collections = await api("/api/collections");
  $("coll-list").innerHTML = S.collections.filter((c) => c.name !== LIKED).map((c) => `<option value="${esc(c.name)}">`).join("");
  $("collections").innerHTML = [{ name: "", track_count: null, label: "Whole library" }, ...S.collections.map((c) => ({ ...c, label: c.name }))].map((c) =>
    `<button class="facet ${S.collection === c.name ? "on" : ""}" data-coll="${esc(c.name)}">${esc(c.label)}<span>${c.track_count ?? ""}</span></button>`).join("");
  $("collections").querySelectorAll("button").forEach((b) => (b.onclick = () => { S.collection = b.dataset.coll; leaveSimilar(); loadCollections(); runSearch(); }));
}
async function loadLiked() {
  try { const d = await api(`/api/search?collection=${encodeURIComponent(LIKED)}&per_page=200&sort=name`); S.liked = d.notes.some((n) => /empty/.test(n)) ? new Set() : new Set(d.tracks.map((t) => t.id)); } catch { /* ignore */ }
}
async function like(t) {
  try {
    if (S.liked.has(t.id)) { await api(`/api/collections/${encodeURIComponent(LIKED)}/tracks/${t.id}`, { method: "DELETE" }); S.liked.delete(t.id); }
    else { await jpost(`/api/collections/${encodeURIComponent(LIKED)}/tracks`, { ids: [t.id] }); S.liked.add(t.id); }
    loadCollections(); renderRows();
  } catch (e) { toast(e.message); }
}
function collectMenu(ids, anchor) {
  document.querySelector(".pop")?.remove();
  const pop = document.createElement("div"); pop.className = "pop";
  const r = anchor.getBoundingClientRect(); pop.style.left = Math.min(r.left, innerWidth - 220) + "px"; pop.style.top = Math.min(r.bottom + 4, innerHeight - 240) + "px";
  pop.innerHTML = S.collections.filter((c) => c.name !== LIKED).map((c) => `<button data-n="${esc(c.name)}">${esc(c.name)}</button>`).join("") + `<button data-new="1">＋ New collection…</button>`;
  pop.onclick = async (e) => {
    const b = e.target.closest("button"); if (!b) return; pop.remove();
    let name = b.dataset.n;
    if (b.dataset.new) { name = (prompt("Collection name") || "").trim(); if (!name) return; }
    try { const r = await jpost(`/api/collections/${encodeURIComponent(name)}/tracks`, { ids }); toast(r.added ? `Added ${r.added} to “${name}”` : `Already in “${name}”`); loadCollections(); } catch (err) { toast(err.message); }
  };
  document.body.appendChild(pop);
  setTimeout(() => document.addEventListener("click", () => pop.remove(), { once: true }), 0);
}
$("new-coll").onclick = async () => { const name = (prompt("New collection name") || "").trim(); if (!name) return; try { await jpost("/api/collections", { name }); loadCollections(); } catch (e) { toast(e.message); } };

async function loadSaved() {
  S.saved = await api("/api/saved-searches");
  $("saved").innerHTML = S.saved.length ? S.saved.map((s) => `<div class="saved"><button class="facet" data-id="${s.id}">${esc(s.name)}</button><button class="del" data-del="${s.id}" title="delete">×</button></div>`).join("") : `<p class="hint">Save any search to come back to it in one click.</p>`;
  $("saved").querySelectorAll(".facet").forEach((b) => (b.onclick = () => applySaved(S.saved.find((s) => s.id == b.dataset.id))));
  $("saved").querySelectorAll(".del").forEach((b) => (b.onclick = async () => { await api(`/api/saved-searches/${b.dataset.del}`, { method: "DELETE" }); loadSaved(); }));
}
function applySaved(s) {
  const q = s.query; Object.assign(S, { mimicBlob: null, similarSet: null, view: "browse", sent: q.sent || "", q: q.q || "", pinned: q.pinned || [], excluded: q.excluded || [], type: q.type || "", bpmMin: q.bpmMin || "", bpmMax: q.bpmMax || "", key: q.key || "", rating: q.rating || "", collection: q.collection || "", sort: q.sort || "relevance", similarTo: null });
  $("q").value = S.q; $("bpm-min").value = S.bpmMin; $("bpm-max").value = S.bpmMax; $("key").value = S.key; $("rating").value = S.rating; $("sent").value = S.sent; $("sort").value = S.sort; syncType(); loadCollections(); runSearch();
}
$("save-search").onclick = async () => {
  const name = (prompt("Name this search", S.q || "My search") || "").trim(); if (!name) return;
  const { q, pinned, excluded, type, bpmMin, bpmMax, key, rating, collection, sort, sent } = S;
  try { await jpost("/api/saved-searches", { name, query: { q, pinned, excluded, type, bpmMin, bpmMax, key, rating, collection, sort, sent } }); toast(`Saved “${name}”`); loadSaved(); } catch (e) { toast(e.message); }
};

// ---------- tag manager ----------
async function loadTagList() { const tags = await api("/api/tags"); S.tagList = tags; $("tag-list").innerHTML = tags.slice(0, 400).map((t) => `<option value="${esc(t.tag)}">`).join(""); return tags; }
function renderTagMgr() {
  const f = $("tagmgr-filter").value.toLowerCase().trim();
  $("tagmgr-list").innerHTML = (S.tagList || []).filter((t) => !f || t.tag.includes(f)).slice(0, 200).map((t) =>
    `<div class="tmrow"><span>${esc(t.tag)}</span><small>${t.count}</small><button data-ren="${esc(t.tag)}">rename</button><button data-del="${esc(t.tag)}">delete</button></div>`).join("") || `<p class="muted">No tags match.</p>`;
}
$("manage-tags").onclick = async () => { await loadTagList(); $("tagmgr-filter").value = ""; renderTagMgr(); $("tagmgr-dlg").showModal(); };
$("tagmgr-filter").oninput = renderTagMgr;
$("tagmgr-list").onclick = async (e) => {
  const ren = e.target.dataset.ren, del = e.target.dataset.del;
  try {
    if (ren) { const n = (prompt(`Rename “${ren}” to (an existing tag merges them):`, ren) || "").trim(); if (!n || n === ren) return; const r = await jpost("/api/tags/rename", { old: ren, new: n }); toast(`Renamed on ${r.tracks} tracks`, { label: "Undo", fn: () => undo(r.batch_id) }); }
    else if (del) { if (!confirm(`Remove the tag “${del}” from every track? You can undo this right after.`)) return; const r = await jpost("/api/tags/delete", { tag: del }); toast(`Removed from ${r.tracks} tracks`, { label: "Undo", fn: () => undo(r.batch_id) }); }
    else return;
    await afterEdit(); renderTagMgr();
  } catch (err) { toast(err.message); }
};

// ---------- import wizard ----------
const imp = { path: "", scan: null, job: null, timer: null };
const impBody = () => $("imp-body");
$("open-import").onclick = $("onboard-import").onclick = () => { $("import-dlg").showModal(); stepChoose(imp.path || ""); };
$("import-dlg").addEventListener("close", () => clearInterval(imp.timer));

async function stepChoose(path) {
  $("imp-title").textContent = "Import audio · 1 of 3 · Choose a folder";
  let d;
  try { d = await api(`/api/fs${path ? "?path=" + encodeURIComponent(path) : ""}`); } catch (e) { toast(e.message); d = await api("/api/fs"); }
  imp.path = d.path;
  const shortcuts = (d.shortcuts || []).map((p) => `<button data-go="${esc(p)}">${esc(p === d.shortcuts[0] ? "Home" : p.split("/").pop())}</button>`).join("");
  impBody().innerHTML = `<p class="muted">Pick the folder that holds your audio. Subfolders are included. Nothing is copied, moved or changed.</p>
    <div class="crumbs">${shortcuts}</div>
    <input class="full" id="imp-path" value="${esc(d.path)}" aria-label="Folder path">
    <div class="dirs">${d.parent ? `<button data-go="${esc(d.parent)}">⬑ ..</button>` : ""}${d.dirs.map((n) => `<button data-go="${esc(d.path.replace(/\/$/, "") + "/" + n)}">📁 ${esc(n)}</button>`).join("") || `<div class="ex" style="padding:10px">No subfolders.</div>`}</div>
    <p class="ex">${d.audio_here ? `${d.audio_here} audio file(s) directly in this folder.` : "No audio files directly in this folder."}${d.error ? " " + esc(d.error) : ""}</p>
    <p class="error" id="imp-err"></p>
    <div class="dlg-foot"><button type="button" data-close>Cancel</button><button class="btn-primary" id="imp-scan">Scan this folder →</button></div>`;
  impBody().querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => stepChoose(b.dataset.go)));
  impBody().querySelector("[data-close]").onclick = () => $("import-dlg").close();
  $("imp-path").onkeydown = (e) => e.key === "Enter" && stepChoose(e.target.value.trim());
  $("imp-scan").onclick = () => stepPreview($("imp-path").value.trim());
}
async function stepPreview(path) {
  $("imp-title").textContent = "Import audio · 2 of 3 · Preview";
  impBody().innerHTML = `<p class="muted">Scanning…</p>`;
  let s;
  try { s = await jpost("/api/import/scan", { path }); } catch (e) { await stepChoose(imp.path); $("imp-err").textContent = e.message; return; }
  imp.scan = s;
  const fmts = Object.entries(s.by_format).map(([k, v]) => `${v} ${k}`).join(" · ");
  impBody().innerHTML = `<div class="ex">${esc(s.root)}</div>
    <div class="cards"><div class="card2"><b>${s.total.toLocaleString()}</b><span>audio files found</span></div><div class="card2"><b>${s.new.toLocaleString()}</b><span>new to your library</span></div><div class="card2"><b>${fmtBytes(s.bytes)}</b><span>total size</span></div></div>
    <p class="ex">${esc(fmts) || "No audio files found."}</p>
    ${s.top_folders.length ? `<p class="ex"><b>Biggest folders:</b> ${s.top_folders.map(([n, c]) => `${esc(n)} (${c})`).join(" · ")}</p>` : ""}
    ${s.examples.length ? `<p class="ex"><b>Examples:</b> ${s.examples.map(esc).join(" · ")}</p>` : ""}
    ${s.already_in_library ? `<p class="ex">${s.already_in_library.toLocaleString()} already in your library — they'll be skipped.</p>` : ""}
    <label class="ex"><input type="checkbox" id="imp-deep"> Also analyze the sound of every track afterwards (key, tempo, "sounds like" matching). Runs in the background, can be paused, ~10 minutes per 2,000 tracks</label>
    <p class="error" id="imp-err"></p>
    <div class="dlg-foot"><button type="button" id="imp-back">← Back</button><button class="btn-primary" id="imp-go" ${s.total ? "" : "disabled"}>${s.new ? `Import ${s.new.toLocaleString()} files` : "Re-scan & tag"}</button></div>`;
  $("imp-back").onclick = () => stepChoose(imp.path);
  $("imp-go").onclick = async () => {
    try { imp.job = await jpost("/api/import/start", { path: s.root, deep: $("imp-deep").checked }); stepProgress(); } catch (e) { $("imp-err").textContent = e.message; }
  };
}
function stepProgress() {
  $("imp-title").textContent = "Import audio · 3 of 3 · Importing";
  const draw = (j) => {
    const pct = j.total ? Math.round((j.done / j.total) * 100) : 0;
    const phase = { scanning: "Looking for files…", importing: "Reading files…", tagging: "Tagging & organizing…" }[j.state] || "";
    impBody().innerHTML = `<p class="muted">${phase}</p><div class="progress"><i style="width:${j.state === "tagging" ? 100 : pct}%"></i></div>
      <p class="ex">${j.done.toLocaleString()} of ${j.total.toLocaleString()} · ${j.counts.added} added · ${j.counts.skipped} skipped · ${j.counts.failed} couldn't be read · ${j.elapsed}s</p>
      <div class="dlg-foot"><button type="button" id="imp-cancel">Cancel</button></div>`;
    $("imp-cancel").onclick = () => api(`/api/import/${j.id}/cancel`, { method: "POST" });
  };
  draw(imp.job);
  clearInterval(imp.timer);
  imp.timer = setInterval(async () => {
    try { imp.job = await api(`/api/import/${imp.job.id}`); } catch { return; }
    if (["done", "cancelled", "error"].includes(imp.job.state)) { clearInterval(imp.timer); stepDone(); } else draw(imp.job);
  }, 700);
}
async function stepDone() {
  const j = imp.job;
  $("imp-title").textContent = j.state === "done" ? "Import complete" : j.state === "cancelled" ? "Import cancelled" : "Import failed";
  const tags = (await api("/api/tags").catch(() => [])).filter((t) => !/^\d{4}$/.test(t.tag)).slice(0, 8);
  impBody().innerHTML = `${j.error ? `<p class="error">${esc(j.error)}</p>` : ""}
    <div class="cards"><div class="card2"><b>${j.counts.added.toLocaleString()}</b><span>added</span></div><div class="card2"><b>${(j.tag_stats.tags_added || 0).toLocaleString()}</b><span>tags created</span></div><div class="card2"><b>${(j.tag_stats.bpm_filled || 0).toLocaleString()}</b><span>tempos found in names</span></div></div>
    ${j.counts.failed ? `<p class="ex">${j.counts.failed} file(s) couldn't be read (corrupt or unsupported) and were skipped.</p>` : ""}
    <p class="ex">Tags come from file names and folders. Refine them any time — click a track's name to edit it, or tick several to batch-edit.</p>
    ${tags.length ? `<div class="crumbs">${tags.map((t) => `<button data-t="${esc(t.tag)}">${esc(t.tag)} · ${t.count}</button>`).join("")}</div>` : ""}
    <div class="dlg-foot"><button class="btn-primary" id="imp-explore">Start exploring</button></div>`;
  impBody().querySelectorAll("[data-t]").forEach((b) => (b.onclick = () => { $("import-dlg").close(); S.pinned = [b.dataset.t]; runSearch(); }));
  $("imp-explore").onclick = () => { $("import-dlg").close(); };
  S.view = "home"; refreshAll();   // first import: land on Discover, not the flat list
}

// ---------- player ----------
const audio = new Audio(); audio.preload = "none";
function play(t) {
  $("p-err").textContent = "";
  if (S.playingId === t.id) { audio.paused ? audio.play() : audio.pause(); return; }
  S.playingId = t.id; audio.src = `${API}/api/audio/${t.id}`;
  $("player").hidden = false; $("p-title").textContent = t.filename;
  audio.play().catch(() => {});
}
audio.addEventListener("error", () => { $("p-err").textContent = "Can't play this file — is the drive it lives on connected?"; });
["play", "pause", "ended"].forEach((ev) => audio.addEventListener(ev, () => {
  $("p-btn").textContent = audio.paused ? "▶" : "❚❚";
  document.querySelectorAll(".row-t").forEach((r) => { const on = +r.dataset.id === S.playingId; r.classList.toggle("playing", on); r.querySelector(".play").textContent = on && !audio.paused ? "❚❚" : "▶"; });
}));
audio.addEventListener("timeupdate", () => { if (audio.duration) $("p-seek").value = (audio.currentTime / audio.duration) * 1000; $("p-time").textContent = fmtTime(audio.currentTime); });
$("p-seek").oninput = () => { if (audio.duration) audio.currentTime = ($("p-seek").value / 1000) * audio.duration; };
$("p-btn").onclick = () => (audio.paused ? audio.play() : audio.pause());
document.addEventListener("keydown", (e) => { if (e.code === "Space" && document.activeElement === document.body && S.playingId) { e.preventDefault(); audio.paused ? audio.play() : audio.pause(); } });


// ---------- sound analysis (resumable background job) ----------
let analysisTimer = null;
async function loadAnalysis() {
  try { S.analysis = await api("/api/analysis"); } catch { return; }
  renderAnalysis();
  const running = S.analysis.job?.state === "running";
  clearTimeout(analysisTimer);
  if (running) analysisTimer = setTimeout(loadAnalysis, 1500);
  else if (S.analysisWasRunning) { S.analysisWasRunning = false; toast("Sound analysis finished"); loadStats(); runSearch({ keepPage: true }); }
  S.analysisWasRunning = running;
}
function renderAnalysis() {
  const a = S.analysis, j = a.job, el = $("analysis");
  const done = a.analyzed + a.failed, pct = a.total ? Math.round((done / a.total) * 100) : 0;
  const mins = (n) => (n * 0.3 / 60 < 1 ? "under a minute" : `~${Math.ceil(n * 0.3 / 60)} min`);
  if (j?.state === "running") {
    const eta = j.eta_seconds != null ? ` · ${Math.floor(j.eta_seconds / 60)}:${String(j.eta_seconds % 60).padStart(2, "0")} left` : "";
    el.innerHTML = `<b>Analyzing sound…</b><div class="bar2"><i style="width:${pct}%"></i></div><span class="muted">${a.analyzed.toLocaleString()} of ${a.total.toLocaleString()}${eta}</span><br><button class="sec" id="an-pause">Pause</button>`;
    $("an-pause").onclick = async () => { await jpost("/api/analysis/cancel", {}); loadAnalysis(); };
  } else if (!a.total) {
    el.innerHTML = `<span class="muted">Import audio first.</span>`;
  } else if (a.pending === 0) {
    el.innerHTML = a.failed === 0
      ? `<b>All ${a.total.toLocaleString()} tracks analyzed ✓</b><div class="muted">Key, tempo and “sounds like” matching are ready.</div>`
      : `<b>${a.analyzed.toLocaleString()} analyzed${a.analyzed ? " ✓" : ""}</b><div class="muted">${a.failed.toLocaleString()} file${a.failed > 1 ? "s" : ""} couldn't be analyzed (silent, corrupt or unsupported audio)${a.analyzed ? "; the rest are ready for “sounds like” matching" : ""}.</div>`;
  } else {
    el.innerHTML = `<b>${a.analyzed.toLocaleString()} of ${a.total.toLocaleString()} analyzed</b><div class="bar2"><i style="width:${pct}%"></i></div>
      <div class="muted">${j?.state === "cancelled" ? "Paused. " : ""}Unlocks “sounds like” matching, key detection and search by sound. ${mins(a.pending)} for the rest.</div>
      <button id="an-start">${a.analyzed ? "Resume" : "Analyze"} ${a.pending.toLocaleString()} tracks</button>${j && j.missing ? `<div class="muted">${j.missing} file(s) weren't reachable — is a drive disconnected?</div>` : ""}`;
    $("an-start").onclick = () => startAnalysis();
  }
}
async function startAnalysis(ids) {
  if (!Array.isArray(ids)) ids = undefined;
  try { S.analysis = await jpost("/api/analysis/start", ids ? { ids } : {}); S.analysisWasRunning = true; renderAnalysis(); loadAnalysis(); if (ids) toast(`Analyzing ${ids.length} track${ids.length > 1 ? "s" : ""}…`); } catch (e) { toast(e.message); }
}

// ---------- search by sound (Mimic) ----------
let rec = null;
$("open-mimic").onclick = () => { $("mimic-err").textContent = ""; $("rec-status").textContent = "Your browser will ask for microphone access."; $("mimic-dlg").showModal(); };
async function runMimic(blob) {
  $("mimic-err").textContent = "";
  S.mimicBlob = blob; S.similarTo = null; S.q = ""; $("q").value = ""; S.page = 1;
  try { const d = await mimicRequest(); $("mimic-dlg").close(); S.tracks = d.tracks; render(d); window.scrollTo({ top: 0, behavior: "smooth" }); }
  catch (e) { S.mimicBlob = null; $("mimic-err").textContent = e.message; }
}
$("rec-btn").onclick = async () => {
  if (rec) { rec.stop(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const chunks = [], mr = new MediaRecorder(stream);
    rec = mr; mr.ondataavailable = (e) => chunks.push(e.data);
    mr.onstop = () => { clearTimeout(rec.timer); stream.getTracks().forEach((t) => t.stop()); $("rec-btn").classList.remove("on"); $("rec-btn").textContent = "●"; rec = null;
      const blob = new Blob(chunks, { type: mr.mimeType || "audio/webm" }); blob.name = "recording.webm"; $("rec-status").textContent = "Searching…"; runMimic(blob); };
    mr.start(); $("rec-btn").classList.add("on"); $("rec-btn").textContent = "■"; $("rec-status").textContent = "Recording… click to stop";
    mr.timer = setTimeout(() => mr.state === "recording" && mr.stop(), 8000);
  } catch (e) { $("mimic-err").textContent = "Couldn't use the microphone (" + (e.name || "blocked") + "). You can drop in an audio clip below instead."; }
};
const dropEl = $("drop-clip"), fileEl = $("clip-file");
dropEl.onclick = () => fileEl.click();
fileEl.onchange = () => fileEl.files[0] && runMimic(fileEl.files[0]);
["dragover", "dragenter"].forEach((ev) => dropEl.addEventListener(ev, (e) => { e.preventDefault(); dropEl.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => dropEl.addEventListener(ev, (e) => { e.preventDefault(); dropEl.classList.remove("over"); }));
dropEl.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) runMimic(f); });
$("mimic-dlg").addEventListener("close", () => { if (rec) rec.stop(); });

// ---------- boot ----------
async function loadStats() {
  try {
    const st = S.stats = await api("/api/stats");
    $("stats").textContent = `${st.tracks.toLocaleString()} tracks · ${st.tagged.toLocaleString()} tagged · ${st.with_bpm.toLocaleString()} with BPM`;
    const h = $("key-hint"); h.hidden = !(st.tracks && st.with_key / st.tracks < 0.5);
    h.textContent = "Key isn't detected for most tracks yet. You can set keys by hand (click a track's name) or import with audio analysis on.";
  } catch { /* server hint shown by first search */ }
}
async function refreshAll() { await Promise.all([loadStats(), loadCollections(), loadTagList(), loadSaved(), loadAnalysis()]); runSearch(); }
(async function init() {
  const keySel = $("key");
  for (let n = 1; n <= 12; n++) for (const l of ["A", "B"]) keySel.add(new Option(`${n}${l}`, `${n}${l}`));
  await Promise.all([loadStats(), loadCollections(), loadLiked(), loadTagList(), loadSaved(), loadAnalysis()]);
  runSearch();
})();
