// If you open index.html straight from disk (file://), talk to the local server explicitly.
// If the page is served by the backend at http://127.0.0.1:8765/, relative URLs work.
const API_BASE = window.location.protocol === "file:" ? "http://127.0.0.1:8765" : "";

const MAX_HISTORY = 10;
const MAX_FILENAME_LEN = 200; // keep in sync with the backend

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");
const statusEl = document.getElementById("status");
const formatSelect = document.getElementById("format");
const convertBtn = document.getElementById("convert-btn");
const templateInput = document.getElementById("naming-template");
const previewEl = document.getElementById("name-preview");
const presetButtons = document.querySelectorAll(".preset");
const copyButtons = document.querySelectorAll(".copy-btn");
const copyAllBtn = document.getElementById("copy-all-btn");
const historyList = document.getElementById("history-list");
const toastEl = document.getElementById("toast");

const out = {
  filename: document.getElementById("out-filename"),
  duration: document.getElementById("out-duration"),
  bpm: document.getElementById("out-bpm"),
  key: document.getElementById("out-key"),
  tags: document.getElementById("out-tags"),
};

const state = {
  file: null,
  meta: null, // latest /analyze response
  analysisId: 0, // bumps on every successful analyze; ties history entries to a track
  bitrate: null, // set by presets, e.g. "320k"; null = backend default
  history: [], // newest first, session only
};

// ---------- small helpers ----------

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

function formatDuration(seconds) {
  if (seconds == null) return "—";
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = String(total % 60).padStart(2, "0");
  return `${m}:${s}`;
}

// "C# minor" -> "C#m", "C major" -> "C"; null if unrecognised
function shortKey(key) {
  const match = /^\s*([A-G]#?)\s+(major|minor)\s*$/i.exec(key || "");
  if (!match) return null;
  return match[1].toUpperCase() + (match[2].toLowerCase() === "minor" ? "m" : "");
}

// "C#m (12A)", "C#m" if there is no Camelot value, or null
function keyDisplay(meta) {
  const short = shortKey(meta && meta.key) || (meta && meta.key) || null;
  if (!short) return null;
  return meta.camelot_key ? `${short} (${meta.camelot_key})` : short;
}

function bpmText(meta) {
  return meta && meta.bpm != null ? String(meta.bpm) : null;
}

function tagsText(meta) {
  return Object.entries((meta && meta.tags) || {})
    .map(([name, value]) => `${name}: ${value}`)
    .join("\n");
}

// ---------- naming template (mirrors build_output_name in main.py) ----------

function sanitizeFilename(name) {
  return name.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_").trim().replace(/^\.+|\.+$/g, "");
}

function buildOutputName(template, originalName, meta, ext) {
  const stem = sanitizeFilename(originalName.replace(/\.[^.]+$/, "")) || "audio";
  const fallback = `${stem}_converted.${ext}`;
  try {
    const bpmNum = meta && meta.bpm != null ? Number(meta.bpm) : NaN;
    const bpm = Number.isFinite(bpmNum) ? String(bpmNum) : "unknown";
    const key = shortKey(meta && meta.key) || "unknown";

    let result = sanitizeFilename(
      template
        .trim()
        .split("{name}").join(stem)
        .split("{bpm}").join(bpm)
        .split("{key}").join(key)
        .split("{ext}").join(ext)
    );
    if (!result || result.toLowerCase() === ext) return fallback;
    if (!result.toLowerCase().endsWith("." + ext)) result += "." + ext;
    if (result.length > MAX_FILENAME_LEN) return fallback;
    return result;
  } catch (_) {
    return fallback;
  }
}

function updatePreview() {
  if (!state.meta) {
    previewEl.textContent = "—";
    return;
  }
  previewEl.textContent = buildOutputName(
    templateInput.value, state.meta.filename || state.file.name, state.meta, formatSelect.value
  );
}

// ---------- results ----------

function resetResults() {
  state.meta = null;
  out.filename.textContent = "—";
  out.duration.textContent = "—";
  out.bpm.textContent = "—";
  out.key.textContent = "—";
  out.tags.innerHTML = '<p class="value">—</p>';
  setCopyEnabled(false);
  updatePreview();
}

function showResults(data) {
  state.meta = data;
  out.filename.textContent = data.filename || "—";
  out.duration.textContent = formatDuration(data.duration);
  out.bpm.textContent = bpmText(data) || "—";
  out.key.textContent = keyDisplay(data) || "—";

  out.tags.innerHTML = "";
  const entries = Object.entries(data.tags || {});
  if (entries.length === 0) {
    out.tags.innerHTML = '<p class="value">No tags found</p>';
  } else {
    for (const [name, value] of entries) {
      const dt = document.createElement("dt");
      dt.textContent = name;
      const dd = document.createElement("dd");
      dd.textContent = value;
      out.tags.append(dt, dd);
    }
  }
  setCopyEnabled(true);
  updatePreview();
}

// ---------- copy to clipboard ----------

let toastTimer = null;
function showToast(message) {
  toastEl.textContent = message;
  toastEl.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("show"), 1400);
}

async function copyText(text, label = "Copied!") {
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    // navigator.clipboard can be unavailable (e.g. file:// in some browsers); fall back
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_) { /* ignore */ }
    area.remove();
    if (!ok) {
      showToast("Copy failed");
      return;
    }
  }
  showToast(label);
}

function copyValue(field) {
  const meta = state.meta;
  if (!meta) return null;
  switch (field) {
    case "filename": return meta.filename || null;
    case "duration": return meta.duration != null ? formatDuration(meta.duration) : null;
    case "bpm": return bpmText(meta);
    case "key": return keyDisplay(meta);
    case "tags": return tagsText(meta) || null;
    default: return null;
  }
}

function allMetadataText(meta) {
  const lines = [
    `Filename: ${meta.filename || "—"}`,
    `Duration: ${meta.duration != null ? formatDuration(meta.duration) : "—"}`,
    `BPM: ${bpmText(meta) || "—"}`,
    `Key: ${keyDisplay(meta) || "—"}`,
  ];
  const tags = tagsText(meta);
  lines.push(tags ? "Tags:\n" + tags.split("\n").map((l) => "  " + l).join("\n") : "Tags: —");
  return lines.join("\n");
}

function setCopyEnabled(enabled) {
  copyButtons.forEach((btn) => (btn.disabled = !enabled));
  copyAllBtn.disabled = !enabled;
}

copyButtons.forEach((btn) =>
  btn.addEventListener("click", () => {
    const value = copyValue(btn.dataset.copy);
    if (value) copyText(value);
    else showToast("Nothing to copy");
  })
);
copyAllBtn.addEventListener("click", () => {
  if (state.meta) copyText(allMetadataText(state.meta), "Copied all!");
});

// ---------- history (in memory only) ----------

function renderHistory() {
  historyList.innerHTML = "";
  if (state.history.length === 0) {
    const li = document.createElement("li");
    li.className = "history-empty";
    li.textContent = "Nothing yet. Tracks you analyze or convert this session show up here.";
    historyList.appendChild(li);
    return;
  }
  for (const entry of state.history) {
    const parts = [
      entry.filename || "—",
      entry.bpm != null ? `${entry.bpm} BPM` : "— BPM",
      entry.key || "—",
    ];
    if (entry.format) parts.push(entry.format);
    const li = document.createElement("li");
    li.textContent = parts.join(" – ");
    historyList.appendChild(li);
  }
}

function addHistory(entry) {
  state.history.unshift(entry);
  state.history.length = Math.min(state.history.length, MAX_HISTORY);
  renderHistory();
}

function historyEntryFor(meta, format) {
  return {
    analysisId: state.analysisId,
    filename: meta.filename,
    bpm: meta.bpm,
    key: keyDisplay(meta),
    format,
  };
}

// After analyze an entry has no format yet; the first convert of that track fills it in,
// later converts of the same track add a new entry.
function recordConversion(format) {
  const existing = state.history.find((e) => e.analysisId === state.analysisId);
  if (existing && !existing.format) {
    existing.format = format;
    renderHistory();
  } else {
    addHistory(historyEntryFor(state.meta, format));
  }
}

// ---------- presets ----------

function setActivePreset(button) {
  presetButtons.forEach((b) => b.classList.toggle("active", b === button));
}

presetButtons.forEach((btn) =>
  btn.addEventListener("click", () => {
    formatSelect.value = btn.dataset.format;
    state.bitrate = btn.dataset.bitrate || null;
    setActivePreset(btn);
    updatePreview();
  })
);

formatSelect.addEventListener("change", () => {
  state.bitrate = null; // picking a format by hand goes back to that format's default quality
  setActivePreset(null);
  updatePreview();
});

templateInput.addEventListener("input", updatePreview);

// ---------- server calls ----------

async function errorMessage(response) {
  try {
    const body = await response.json();
    if (body.detail) return typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  } catch (_) { /* not JSON */ }
  return `Server error (${response.status})`;
}

async function analyzeFile(file) {
  state.file = file;
  convertBtn.disabled = true;
  resetResults();
  out.filename.textContent = file.name;
  setStatus("Analyzing… (BPM and key detection can take a few seconds)");

  const formData = new FormData();
  formData.append("file", file);

  try {
    const response = await fetch(`${API_BASE}/analyze`, { method: "POST", body: formData });
    if (!response.ok) throw new Error(await errorMessage(response));
    const data = await response.json();
    state.analysisId += 1;
    showResults(data);
    addHistory(historyEntryFor(data, null));
    setStatus("Ready. Pick a format and press Convert.");
    convertBtn.disabled = false;
  } catch (err) {
    state.file = null;
    const offline = err instanceof TypeError;
    setStatus(offline ? "Cannot reach the server. Is it running on port 8765?" : err.message, true);
  }
}

async function convertFile() {
  if (!state.file) return;

  const format = formatSelect.value;
  convertBtn.disabled = true;
  convertBtn.classList.add("loading");
  convertBtn.textContent = "Converting";
  formatSelect.disabled = true;
  setStatus("Converting…");

  const formData = new FormData();
  formData.append("file", state.file);
  formData.append("format", format);
  formData.append("naming_template", templateInput.value);
  if (state.meta && state.meta.bpm != null) formData.append("bpm", state.meta.bpm);
  if (state.meta && state.meta.key) formData.append("key", state.meta.key);
  if (state.bitrate) formData.append("bitrate", state.bitrate);

  try {
    const response = await fetch(`${API_BASE}/convert`, { method: "POST", body: formData });
    if (!response.ok) throw new Error(await errorMessage(response));

    const blob = await response.blob();
    // The server decides the final name; the local preview logic is only the fallback.
    let downloadName = buildOutputName(templateInput.value, state.file.name, state.meta, format);
    const headerName = response.headers.get("X-Output-Filename");
    if (headerName) {
      try { downloadName = decodeURIComponent(headerName); } catch (_) { /* keep local name */ }
    }

    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = downloadName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    recordConversion(format);
    setStatus(`Done! Downloaded ${downloadName}`);
  } catch (err) {
    const offline = err instanceof TypeError;
    setStatus(offline ? "Cannot reach the server. Is it running on port 8765?" : err.message, true);
  } finally {
    convertBtn.disabled = false;
    convertBtn.classList.remove("loading");
    convertBtn.textContent = "Convert";
    formatSelect.disabled = false;
  }
}

// ---------- file picking / drag and drop ----------

// Click or keyboard to open the file picker
dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    fileInput.click();
  }
});

fileInput.addEventListener("change", () => {
  if (fileInput.files.length) analyzeFile(fileInput.files[0]);
  fileInput.value = ""; // allow picking the same file again
});

// Drag and drop
["dragenter", "dragover"].forEach((type) =>
  dropzone.addEventListener(type, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((type) =>
  dropzone.addEventListener(type, (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  })
);
dropzone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) analyzeFile(file);
});

// Stop the browser from opening a file that is dropped outside the drop area
["dragover", "drop"].forEach((type) => window.addEventListener(type, (e) => e.preventDefault()));

convertBtn.addEventListener("click", convertFile);

setCopyEnabled(false);
updatePreview();
renderHistory();
