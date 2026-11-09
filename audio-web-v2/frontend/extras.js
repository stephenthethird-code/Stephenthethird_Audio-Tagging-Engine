// Library extras (loads after library.js, shares its globals): Discover home, genres, people & send tracker, version stacks.

// ---------- Discover home vs browsing ----------
function isIdle() {
  return !S.q && !S.pinned.length && !S.excluded.length && !S.type && !S.bpmMin && !S.bpmMax && !S.key && !S.rating && !S.collection &&
    !S.sent && !S.similarTo && !S.similarSet && !S.mimicBlob && S.sort === "relevance";
}
function syncViewPills() {
  document.querySelectorAll("#viewpills button").forEach((b) => b.classList.toggle("on", b.dataset.view === S.view));
  document.querySelector(".main").classList.toggle("home-mode", S.view === "home" && isIdle());
}
function hideHome() { $("home").hidden = true; document.querySelector(".main").classList.remove("home-mode"); }
function resetAll() {
  Object.assign(S, { q: "", pinned: [], excluded: [], type: "", bpmMin: "", bpmMax: "", key: "", rating: "", collection: "", sent: "", sort: "relevance", seed: null });
  leaveSimilar();
  ["q", "bpm-min", "bpm-max", "key", "rating", "sent", "sort"].forEach((id) => ($(id).value = id === "sort" ? "relevance" : ""));
  syncType(); loadCollections();
}
document.querySelectorAll("#viewpills button").forEach((b) => (b.onclick = () => {
  if (b.dataset.view === "home") resetAll();
  S.view = b.dataset.view; runSearch();
}));

async function showHome(seed) {
  S.selected.clear(); renderBatchBar();
  $("chips").innerHTML = ""; $("notes").innerHTML = ""; $("alts").innerHTML = ""; $("simbar").hidden = true;
  let d;
  try { d = await api("/api/shelves" + (seed ? `?seed=${seed}` : "")); } catch (e) { $("notes").innerHTML = `<p>${esc(e.message)}</p>`; return; }
  if (d.empty) { S.view = "browse"; return runSearch(); }                 // empty library -> normal onboarding
  syncViewPills();
  $("home").hidden = false; $("onboard").hidden = true;
  $("home").innerHTML = `<div class="home-top"><span class="muted">Built from your own library</span><button class="linkish" id="home-shuffle">↻ Shuffle</button></div>` + d.shelves.map(shelfHtml).join("");
  window.__shelves = Object.fromEntries(d.shelves.map((s) => [s.id, s]));
  $("home-shuffle").onclick = () => showHome(Math.floor(Math.random() * 1e6));
  api("/api/search?per_page=1").then((x) => renderFacets(x.tag_summary)).catch(() => {});      // keep the sidebar tags populated
}
function shelfHtml(s) {
  const head = `<div class="shelf-head"><h2>${esc(s.title)}</h2><span class="sub">${esc(s.subtitle || "")}</span>${s.query || s.action?.similar_set ? `<button class="linkish seeall" data-see="${esc(s.id)}">See all →</button>` : ""}</div>`;
  if (s.type === "tiles") return `<section class="shelf" data-shelf="${esc(s.id)}">${head}<div class="tiles">${s.items.map((t) => `<button class="tile" data-tile="${esc(t.tag)}">${esc(t.tag)}<span>${t.count}</span></button>`).join("")}</div></section>`;
  if (s.type === "cta") return `<section class="shelf cta" data-shelf="${esc(s.id)}">${head}<button class="btn-soft" data-cta="${esc(s.id)}">${esc(s.button)}</button></section>`;
  return `<section class="shelf" data-shelf="${esc(s.id)}">${head}<div class="sgrid">${s.items.map((t) => `<div class="stile" data-id="${t.id}">
      <button class="play" data-sact="play" aria-label="Play">▶</button>
      <div class="sname" data-sact="inspect" title="${esc(t.path)}">${esc(t.filename)}<small>${[t.bpm ? Math.round(t.bpm) + " bpm" : "", t.camelot_key || "", t.sample_type || ""].filter(Boolean).map(esc).join(" · ")}${rowBadges(t)}${t.match?.why ? ` · <span class="why">${esc(t.match.why)}</span>` : ""}</small></div>
      <button class="mini" data-sact="similar" title="More like this">≈</button></div>`).join("")}</div></section>`;
}
$("home").addEventListener("click", (e) => {
  const tile = e.target.closest("[data-tile]");
  if (tile) { leaveSimilar(); S.pinned = [tile.dataset.tile]; S.view = "browse"; return runSearch(); }
  const see = e.target.closest("[data-see]");
  if (see) {
    const s = window.__shelves[see.dataset.see];
    leaveSimilar();
    if (s.action?.similar_set) S.similarSet = s.action.similar_set;
    else { S.sent = s.query.sent || ""; S.sort = s.query.sort || "relevance"; $("sent").value = S.sent; $("sort").value = S.sort; }
    S.view = "browse"; return runSearch();
  }
  const cta = e.target.closest("[data-cta]");
  if (cta) { const s = window.__shelves[cta.dataset.cta]; return s.action.open === "genres" ? openGenres() : openVersions(); }
  const row = e.target.closest(".stile"), act = e.target.closest("[data-sact]")?.dataset.sact;
  if (!row || !act) return;
  const id = +row.dataset.id, t = Object.values(window.__shelves).flatMap((s) => s.items || []).find((x) => x.id === id);
  if (act === "play") play(t);
  else if (act === "inspect") openInspector(id);
  else if (act === "similar") { S.view = "browse"; leaveSimilar(); S.similarTo = id; runSearch(); }
});

// ---------- row badges ----------
function rowBadges(t) {
  let h = "";
  if (t.group && t.group.size > 1)
    h += ` · <button class="vbadge" data-act="versions" data-sact="inspect" title="${t.group.size} files are ${t.group.kind === "duplicate" ? "the same audio" : "versions of this track"}. Click to review.">⧉ ${t.group.size} versions</button>`;
  if (t.sent_count) h += ` · <span class="sentbadge" title="Sent to ${t.sent_count} ${t.sent_count > 1 ? "people" : "person"} (any version)">✉ ${t.sent_count}</span>`;
  return h;
}

// ---------- send tracker ----------
async function recordSends(ids, person, note) {
  try {
    const r = await jpost("/api/sends", { track_ids: ids, person_id: person.id, note: note || null });
    const msg = r.send_ids.length ? `Marked ${r.send_ids.length} track${r.send_ids.length > 1 ? "s" : ""} sent to ${person.name}` : `${person.name} already has ${r.skipped.length > 1 ? "those" : "that"}`;
    toast(msg + (r.skipped.length && r.send_ids.length ? ` (${r.skipped.length} already sent)` : ""), r.send_ids.length ? { label: "Undo", fn: async () => { await jpost("/api/sends/delete", { ids: r.send_ids }); afterEdit(); } } : null);
    afterEdit();
  } catch (e) { toast(e.message); }
}
function pickPerson(anchor, cb) {
  document.querySelector(".pop")?.remove();
  const pop = document.createElement("div"); pop.className = "pop"; pop.style.minWidth = "280px";
  const r = anchor.getBoundingClientRect(); pop.style.left = Math.max(8, Math.min(r.left, innerWidth - 300)) + "px"; pop.style.bottom = Math.max(8, innerHeight - r.top + 6) + "px";
  pop.innerHTML = `<input class="full" placeholder="Search your contacts…" style="margin:0 0 6px"><div class="plist"></div>`;
  document.body.appendChild(pop);
  const input = pop.querySelector("input"), list = pop.querySelector(".plist");
  const load = debounce(async () => {
    const d = await api(`/api/people?q=${encodeURIComponent(input.value)}&limit=8`);
    list.innerHTML = d.people.map((p) => `<button data-id="${p.id}">${esc(p.name)}<small>${p.tier ? "T" + p.tier : ""} ${esc(p.taste.slice(0, 3).join(", "))}</small></button>`).join("") || `<div class="muted" style="padding:6px">No contacts match.</div>`;
    list.querySelectorAll("button").forEach((b) => (b.onclick = () => { pop.remove(); cb(d.people.find((p) => p.id == b.dataset.id)); }));
  }, 150);
  input.oninput = load; load(); input.focus();
  setTimeout(() => document.addEventListener("click", (e) => { if (!pop.contains(e.target)) pop.remove(); }, { once: true }), 0);
}

// ---------- inspector extras: versions + send ----------
async function renderInspectorExtras(d, el) {
  const box = document.createElement("div"); el.appendChild(box);
  if (d.group_id) {
    const g = await api(`/api/versions/group/${d.group_id}`).catch(() => null);
    if (g) box.insertAdjacentHTML("beforeend", `<label>Versions · ${g.kind === "duplicate" ? "same audio" : "same title"} <span class="muted">(${g.members.length} files)</span></label>` +
      g.members.map((m) => `<div class="vrow ${m.id === d.id ? "cur" : ""}"><div class="vmain"><span class="vstar ${m.is_primary ? "on" : ""}" title="${m.is_primary ? "shown on top of the stack" : ""}">★</span>
        <a href="#" data-open="${m.id}">${esc(m.filename)}</a></div><div class="vmeta">${esc((m.format || "").toUpperCase())}${m.kbps ? " · " + m.kbps + " kbps" : ""} · ${fmtTime(m.duration_sec)}${m.sent_count ? " · ✉ " + m.sent_count : ""}</div>
        <div class="vact">${m.is_primary ? "" : `<button data-main="${m.id}">Make main</button>`}<button data-not="${m.id}" title="This isn't a version of the others">Not a version</button></div></div>`).join(""));
    box.querySelectorAll("[data-open]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); openInspector(+a.dataset.open); }));
    box.querySelectorAll("[data-main]").forEach((b) => (b.onclick = async () => { await jpost(`/api/versions/${d.group_id}/primary`, { track_id: +b.dataset.main }); afterEdit(); }));
    box.querySelectorAll("[data-not]").forEach((b) => (b.onclick = async () => { await jpost("/api/versions/ungroup", { track_id: +b.dataset.not }); toast("Removed from the stack"); afterEdit(); }));
  }
  box.insertAdjacentHTML("beforeend", `<label>Send tracker</label><div id="send-hist" class="muted">Loading…</div><div class="muted" style="margin-top:8px">Who to send it to</div><div id="send-sugg"></div><button class="btn-soft" id="send-other" style="margin-top:8px">Mark sent to someone else…</button>`);
  $("send-other").onclick = (e) => pickPerson(e.target, (p) => recordSends([d.id], p));
  const [hist, sug] = await Promise.all([api(`/api/tracks/${d.id}/sends`).catch(() => []), api(`/api/tracks/${d.id}/send-suggestions`).catch(() => null)]);
  if (S.inspectId !== d.id) return;
  $("send-hist").innerHTML = hist.length ? hist.map((h) => `<div class="hist">✉ ${esc(h.name)} · ${esc(h.sent_at.slice(0, 10))}${h.via_version ? ` · via ${esc(h.filename)}` : ""}${h.note ? ` · ${esc(h.note)}` : ""}
      <button class="linkish" data-undo="${h.id}">remove</button></div>`).join("") : "Not sent to anyone yet.";
  $("send-hist").querySelectorAll("[data-undo]").forEach((b) => (b.onclick = async () => { await jpost("/api/sends/delete", { ids: [+b.dataset.undo] }); afterEdit(); }));
  $("send-sugg").innerHTML = sug ? (sug.note ? `<p class="detected">${esc(sug.note)}</p>` : "") + sug.recipients.slice(0, 6).map((p) => `<div class="prow"><div><b>${esc(p.name)}</b><div class="detected">${esc(p.reasons.join(" · "))}</div></div><button data-send="${p.id}">Mark sent</button></div>`).join("") || `<div class="muted">No eligible contacts left.</div>` : "";
  $("send-sugg").querySelectorAll("[data-send]").forEach((b) => (b.onclick = () => recordSends([d.id], sug.recipients.find((p) => p.id == b.dataset.send))));
}

// ---------- People & sends dialog ----------
const P = { q: "", offset: 0 };
function openPeople() { $("people-title").textContent = "People & sends"; $("people-dlg").showModal(); P.q = ""; peopleList(); }
async function peopleList(append) {
  if (!append) P.offset = 0;
  const d = await api(`/api/people?q=${encodeURIComponent(P.q)}&limit=25&offset=${P.offset}`);
  const rows = d.people.map((p) => `<div class="prow2" data-id="${p.id}"><div class="pinfo"><b>${esc(p.name)}</b> ${p.tier ? `<span class="tier t${p.tier}">T${p.tier}</span>` : ""} ${p.inactive ? `<span class="tier">inactive</span>` : ""}
      <div class="detected">${esc([p.role, p.location, p.company].filter(Boolean).join(" · "))}${p.taste.length ? ` · likes ${esc(p.taste.join(", "))}` : ""}</div>
      <div class="detected">${p.n_sends ? `✉ ${p.n_sends} sent · last ${esc((p.last_sent || "").slice(0, 10))}` : "nothing sent yet"}</div></div>
      <div class="pact"><button data-sugg="${p.id}">What to send</button><button data-edit="${p.id}">Edit</button></div></div>`).join("");
  if (!append) $("people-body").innerHTML = `<div class="ptools"><input class="full" id="people-q" placeholder="Search ${d.total.toLocaleString()} contacts by name, email, tag…" value="${esc(P.q)}"><button class="btn-primary" id="people-new">＋ New</button></div><div id="people-rows">${rows}</div><button class="more" id="people-more" ${d.total > 25 ? "" : "hidden"}>Load more</button>`;
  else $("people-rows").insertAdjacentHTML("beforeend", rows);
  P.shown = (append ? P.shown : 0) + d.people.length; $("people-more").hidden = P.shown >= d.total;
  $("people-q").oninput = debounce(() => { P.q = $("people-q").value; peopleList(); }, 250);
  $("people-new").onclick = () => personForm({});
  $("people-more").onclick = () => { P.offset += 25; peopleList(true); };
  $("people-rows").onclick = async (e) => {
    const sg = e.target.dataset.sugg, ed = e.target.dataset.edit;
    if (sg) personSuggestions(+sg);
    if (ed) { const p = (await api(`/api/people?q=${encodeURIComponent(P.q)}&limit=200`)).people.find((x) => x.id == ed); personForm(p); }
  };
}
async function personSuggestions(id) {
  const d = await api(`/api/people/${id}/suggestions`);
  $("people-title").textContent = `What to send ${d.person.name}`;
  $("people-body").innerHTML = `<button class="linkish" id="p-back">← all contacts</button>${d.note ? `<p class="muted">${esc(d.note)}</p>` : ""}<p class="muted">${d.already_heard} track${d.already_heard === 1 ? "" : "s"} already sent to them are left out (and so are other versions of those).</p>
    ${d.tracks.map((t) => `<div class="prow" data-id="${t.id}"><button class="play" data-a="play">▶</button><div class="pinfo"><a href="#" data-a="open">${esc(t.filename)}</a><div class="detected">${esc(t.reasons.join(" · "))}</div></div><button data-a="send">Mark sent</button></div>`).join("") || `<div class="muted">Nothing left to suggest.</div>`}`;
  $("p-back").onclick = () => { $("people-title").textContent = "People & sends"; peopleList(); };
  $("people-body").onclick = async (e) => {
    const row = e.target.closest(".prow"); if (!row) return; const t = d.tracks.find((x) => x.id == row.dataset.id), a = e.target.dataset.a;
    if (a === "play") play(t); else if (a === "open") { e.preventDefault(); openInspector(t.id); }
    else if (a === "send") { await recordSends([t.id], d.person); row.remove(); }
  };
}
function personForm(p) {
  $("people-title").textContent = p.id ? `Edit ${p.name}` : "New contact";
  const f = (id, label, val, extra = "") => `<label>${label}<input id="pf-${id}" value="${esc(val ?? "")}" ${extra}></label>`;
  $("people-body").innerHTML = `<button class="linkish" id="p-back">← all contacts</button><div class="form">${f("name", "Name", p.name)}${f("email", "Email", p.email)}
    <div class="form-row">${f("role", "Role", p.role)}${f("location", "Location", p.location)}${f("company", "Company", p.company)}<label>Tier<select id="pf-tier"><option value="">—</option>${[1, 2, 3, 4, 5].map((n) => `<option value="${n}" ${p.tier === n ? "selected" : ""}>${n}${n === 1 ? " · inner circle" : n === 5 ? " · blacklist" : ""}</option>`).join("")}</select></label></div>
    ${f("tags", "Tags (role:artist, loc:nyc, and the genres / moods they like — e.g. trap, rnb, dark)", (p.tags || []).join(", "))}
    ${f("status", "Status (Active / Inactive)", p.status)}<label>Notes<textarea id="pf-notes" rows="3" class="full">${esc(p.notes ?? "")}</textarea></label><p class="error" id="pf-err"></p>
    <div class="dlg-foot"><button id="pf-cancel" type="button">Cancel</button><button class="btn-primary" id="pf-save" type="button">Save</button></div></div>`;
  $("p-back").onclick = $("pf-cancel").onclick = () => { $("people-title").textContent = "People & sends"; peopleList(); };
  $("pf-save").onclick = async () => {
    const g = (k) => $("pf-" + k).value;
    try { await jpost("/api/people", { id: p.id, name: g("name"), email: g("email"), role: g("role"), location: g("location"), company: g("company"), tier: g("tier"), tags: g("tags"), status: g("status"), notes: g("notes") }); toast("Saved"); peopleList(); $("people-title").textContent = "People & sends"; }
    catch (e) { $("pf-err").textContent = e.message; }
  };
}
$("open-people").onclick = openPeople;

// ---------- Genres dialog ----------
async function openGenres() {
  $("genres-dlg").showModal(); $("genres-body").innerHTML = `<p class="muted">Loading…</p>`;
  const [ov, sg] = await Promise.all([api("/api/genres"), api("/api/genres/suggestions?limit=40")]);
  const list = ov.genres.map((g) => `<div class="grow"><b>${esc(g.genre)}</b><span class="muted">${g.count} track${g.count === 1 ? "" : "s"}${g.can_suggest ? "" : ` · label ${g.examples_needed} more to enable suggestions`}</span><button data-browse="${esc(g.genre)}">Browse</button></div>`).join("") || `<p class="muted">No genre tags yet. Tag a few tracks with a genre (select tracks → Edit → Add tags, e.g. “trap”) and the app will learn what each genre sounds like in your library.</p>`;
  const sugg = sg.suggestions.map((x, i) => `<div class="prow" data-i="${i}"><div class="pinfo"><a href="#" data-a="open">${esc(x.filename)}</a><div class="detected">sounds like your ${esc(x.because.join(", "))}</div></div>
      <span class="gconf" title="confidence">${Math.round(x.confidence * 100)}%</span><b class="gtag">${esc(x.genre)}</b><button data-a="yes">Accept</button><button data-a="no">No</button></div>`).join("");
  $("genres-body").innerHTML = `<p class="muted">${ov.labeled.toLocaleString()} of ${ov.tracks.toLocaleString()} tracks have a genre. Suggestions are learned from the tracks you've labeled — they never change anything until you accept.</p>
    <div class="glist">${list}</div><h3 class="gh">Suggested genres ${sg.total ? `<span class="muted">(${sg.total})</span>` : ""}</h3>
    ${sg.reason && !sg.suggestions.length ? `<p class="muted">${esc(sg.reason)}${/Analyze/.test(sg.reason) ? ` <button class="btn-soft" id="g-analyze">Analyze now</button>` : ""}</p>` : `<div class="g-all"><button class="btn-primary" id="g-accept-all">Accept all ≥ 70%</button></div><div id="sugg-list">${sugg}</div>`}
    <h3 class="gh">Mood tags</h3><p class="muted">Moods (dark, chill, hard…) are added automatically after sound analysis, judged against the rest of your library, and marked ✦. Remove one and it stays removed.</p><button class="btn-soft" id="g-moods">Re-detect moods from audio</button>`;
  $("genres-body").querySelectorAll("[data-browse]").forEach((b) => (b.onclick = () => { $("genres-dlg").close(); resetAll(); S.pinned = [b.dataset.browse]; S.view = "browse"; runSearch(); }));
  $("g-analyze")?.addEventListener("click", () => { $("genres-dlg").close(); startAnalysis(); });
  $("g-moods").onclick = async () => { const r = await jpost("/api/moods/apply", {}); toast(r.applied ? `Moods updated: +${r.added}, −${r.removed}` : r.reason); afterEdit(); };
  const acceptItems = async (items) => {
    const r = await jpost("/api/genres/accept", { items: items.map((x) => ({ track_id: x.track_id, genre: x.genre })) });
    toast(`Added genre to ${r.accepted} track${r.accepted > 1 ? "s" : ""}`, { label: "Undo", fn: async () => { for (const b of r.batch_ids) await jpost(`/api/undo/${b}`, {}); afterEdit(); } });
    afterEdit(); openGenres();
  };
  $("g-accept-all")?.addEventListener("click", () => { const hi = sg.suggestions.filter((x) => x.confidence >= 0.7); hi.length ? acceptItems(hi) : toast("No suggestions at 70% or higher yet"); });
  $("sugg-list")?.addEventListener("click", async (e) => {
    const row = e.target.closest(".prow"); if (!row) return; const x = sg.suggestions[+row.dataset.i], a = e.target.dataset.a;
    if (a === "open") { e.preventDefault(); openInspector(x.track_id); }
    else if (a === "yes") await acceptItems([x]);
    else if (a === "no") { await jpost("/api/genres/reject", { items: [{ track_id: x.track_id, genre: x.genre }] }); row.remove(); }
  });
}
$("open-genres").onclick = openGenres;

// ---------- Version stacks dialog ----------
const V = { offset: 0 };
async function openVersions(append) {
  if (!append) { V.offset = 0; $("versions-dlg").showModal(); }
  const d = await api(`/api/versions/groups?offset=${V.offset}&limit=15`);
  const html = d.groups.map((g) => `<div class="vgroup" data-g="${g.id}"><div class="vhead"><span class="tier">${g.kind === "duplicate" ? "same audio" : "same title"}</span><span class="muted">${Math.round((g.confidence || 0) * 100)}% sure${g.locked ? " · you decided" : ""}</span></div>
      ${g.members.map((m) => `<div class="vrow"><div class="vmain"><span class="vstar ${m.is_primary ? "on" : ""}">★</span><a href="#" data-open="${m.id}">${esc(m.filename)}</a></div>
        <div class="vmeta">${esc((m.format || "").toUpperCase())}${m.kbps ? " · " + m.kbps + " kbps" : ""} · ${fmtTime(m.duration_sec)}</div>
        <div class="vact">${m.is_primary ? "" : `<button data-main="${m.id}" data-gid="${g.id}">Make main</button>`}<button data-not="${m.id}">Not a version</button></div></div>`).join("")}</div>`).join("");
  if (!append) $("versions-body").innerHTML = `<p class="muted">${d.total.toLocaleString()} group${d.total === 1 ? "" : "s"}. Each stack shows once in your library, with the ★ file on top. Nothing is deleted or renamed. Least certain first.</p><div id="vgroups">${html || `<p class="muted">No stacks found.</p>`}</div><button class="more" id="v-more" ${d.total > 15 ? "" : "hidden"}>Load more</button><div class="dlg-foot"><button id="v-rescan" type="button">Rescan library for versions</button></div>`;
  else $("vgroups").insertAdjacentHTML("beforeend", html);
  V.shown = (append ? V.shown : 0) + d.groups.length; $("v-more").hidden = V.shown >= d.total;
  $("v-more").onclick = () => { V.offset += 15; openVersions(true); };
  $("v-rescan").onclick = async () => { const r = await jpost("/api/versions/rebuild", {}); toast(`${r.groups} stacks found (${r.duplicates} same audio, ${r.versions} same title)`); openVersions(); afterEdit(); };
  $("vgroups").onclick = async (e) => {
    const t = e.target, a = t.closest("[data-open]");
    if (a) { e.preventDefault(); $("versions-dlg").close(); return openInspector(+a.dataset.open); }
    if (t.dataset.main) { await jpost(`/api/versions/${t.dataset.gid}/primary`, { track_id: +t.dataset.main }); openVersions(); afterEdit(); }
    if (t.dataset.not) { await jpost("/api/versions/ungroup", { track_id: +t.dataset.not }); toast("Removed from the stack"); openVersions(); afterEdit(); }
  };
}
$("open-versions").onclick = () => openVersions();
