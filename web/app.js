/**
 * TIFF / BMP 8-Bit Grayscale Converter
 * Main application script running on client browser
 */

// Application State
const state = {
  files: [],            // Array of { id, file, buffer, info, status, convertedBlob, outFilename, outSizeBytes }
  selectedId: null,     // Currently selected file ID for preview
  activeJobId: 0,
  worker: null,
  workerReady: false,
  pendingRequests: new Map(),
  previewDebounceTimer: null,
  deferredPrompt: null  // PWA install prompt
};

// DOM Elements
const el = {
  engineStatus: document.getElementById("engine-status"),
  engineStatusText: document.getElementById("engine-status-text"),
  installBtn: document.getElementById("install-btn"),
  dropzone: document.getElementById("dropzone"),
  fileInput: document.getElementById("file-input"),
  fileCount: document.getElementById("file-count"),
  btnConvertAll: document.getElementById("btn-convert-all"),
  btnDownloadZip: document.getElementById("btn-download-zip"),
  btnClearAll: document.getElementById("btn-clear-all"),
  filesTbody: document.getElementById("files-tbody"),
  suffixInput: document.getElementById("suffix-input"),
  customColorPicker: document.getElementById("custom-color-picker"),
  progressBarFill: document.getElementById("progress-bar-fill"),
  progressLabel: document.getElementById("progress-label"),
  logConsole: document.getElementById("log-console"),
  btnClearLog: document.getElementById("btn-clear-log"),
  frameInput: document.getElementById("frame-input"),
  frameCountLabel: document.getElementById("frame-count-label"),
  btnRefreshPreview: document.getElementById("btn-refresh-preview"),
  previewMeta: document.getElementById("preview-meta"),
  previewNote: document.getElementById("preview-note"),
  previewBeforeImg: document.getElementById("preview-before-img"),
  previewAfterImg: document.getElementById("preview-after-img"),
  beforePlaceholder: document.getElementById("before-placeholder"),
  afterPlaceholder: document.getElementById("after-placeholder"),
  storageMeter: document.getElementById("storage-meter"),
  storageRemainingText: document.getElementById("storage-remaining-text"),
  storageBar: document.getElementById("storage-bar"),
  storageBarOrigin: document.getElementById("storage-bar-origin"),
  storageBarBatch: document.getElementById("storage-bar-batch"),
  storageBreakdownText: document.getElementById("storage-breakdown-text"),
  storageWarningText: document.getElementById("storage-warning-text"),
};

// --------------------------------------------------------------------------
// Logging & Status Helpers
// --------------------------------------------------------------------------

function log(text, level = "info") {
  const entry = document.createElement("div");
  entry.className = `log-entry ${level}`;
  const timestamp = new Date().toLocaleTimeString();
  entry.textContent = `[${timestamp}] ${text}`;
  el.logConsole.appendChild(entry);
  el.logConsole.scrollTop = el.logConsole.scrollHeight;
}

function updateEngineStatus(status, detail) {
  el.engineStatus.className = `status-indicator ${status}`;
  el.engineStatusText.textContent = detail;
  if (status === "ready") {
    state.workerReady = true;
    updateQueueButtons();
    updateStorageMeter();
  }
}

// --------------------------------------------------------------------------
// Browser Storage & Batch Footprint Meter
// --------------------------------------------------------------------------

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const idx = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / Math.pow(1024, idx);
  return `${idx === 0 ? Math.round(value) : value.toFixed(1)} ${units[idx]}`;
}

function getBatchStorageBytes() {
  let inputBytes = 0;
  let outputBytes = 0;
  for (const f of state.files) {
    inputBytes += f.buffer ? f.buffer.byteLength : 0;
    outputBytes += f.convertedBlob ? f.convertedBlob.size : (f.outSizeBytes || 0);
  }
  return { inputBytes, outputBytes, totalBytes: inputBytes + outputBytes };
}

async function updateStorageMeter() {
  if (!el.storageMeter) return;
  const batch = getBatchStorageBytes();

  let quota = 0;
  let originUsed = 0;
  if ("storage" in navigator && typeof navigator.storage.estimate === "function") {
    try {
      const estimate = await navigator.storage.estimate();
      quota = estimate.quota || 0;
      originUsed = Math.max(0, estimate.usage || 0);
    } catch {
      quota = 0;
    }
  }

  if (quota > 0) {
    const effectiveUsed = originUsed + batch.totalBytes;
    const remaining = Math.max(0, quota - effectiveUsed);
    const originPct = Math.min(100, (originUsed / quota) * 100);
    const batchPct = Math.min(100 - originPct, (batch.totalBytes / quota) * 100);
    const totalPct = Math.min(100, originPct + batchPct);

    el.storageBarOrigin.style.width = `${originPct.toFixed(2)}%`;
    el.storageBarBatch.style.width = `${batchPct.toFixed(2)}%`;
    el.storageBar.setAttribute("aria-valuenow", String(Math.round(totalPct)));
    el.storageBar.setAttribute("aria-valuetext", `${formatBytes(remaining)} left of ${formatBytes(quota)}`);

    el.storageRemainingText.textContent = `${formatBytes(remaining)} left`;
    el.storageBreakdownText.textContent = `Cache: ${formatBytes(originUsed)} • Batch: ${formatBytes(batch.totalBytes)} • Quota: ${formatBytes(quota)}`;

    const isDanger = totalPct >= 90 || remaining < 50 * 1024 * 1024;
    const isWarn = !isDanger && (totalPct >= 75 || remaining < 150 * 1024 * 1024);
    el.storageMeter.classList.toggle("danger", isDanger);
    el.storageMeter.classList.toggle("warn", isWarn);
    el.storageWarningText.classList.toggle("hidden", !isDanger && !isWarn);
  } else {
    el.storageBarOrigin.style.width = "0%";
    el.storageBarBatch.style.width = "0%";
    el.storageBar.setAttribute("aria-valuenow", "0");
    el.storageBar.removeAttribute("aria-valuetext");

    el.storageRemainingText.textContent = `Batch: ${formatBytes(batch.totalBytes)}`;
    el.storageBreakdownText.textContent = `Browser storage quota unavailable • Input: ${formatBytes(batch.inputBytes)} • Output: ${formatBytes(batch.outputBytes)}`;
    el.storageMeter.classList.remove("warn", "danger");
    el.storageWarningText.classList.add("hidden");
  }
}

// --------------------------------------------------------------------------
// Web Worker Bridge
// --------------------------------------------------------------------------

function initWorker() {
  state.worker = new Worker("worker.js");

  state.worker.onmessage = (e) => {
    const data = e.data;

    if (data.type === "status") {
      updateEngineStatus(data.status, data.detail);
      return;
    }

    if (data.type === "log") {
      log(data.text, data.level);
      return;
    }

    const { id, action, success, result, error } = data;
    if (state.pendingRequests.has(id)) {
      const resolver = state.pendingRequests.get(id);
      state.pendingRequests.delete(id);
      if (success) {
        resolver.resolve(result);
      } else {
        resolver.reject(new Error(error || "Worker processing error"));
      }
    }
  };

  state.worker.onerror = (err) => {
    log(`Worker fatal error: ${err.message}`, "error");
    updateEngineStatus("error", "Worker failed");
  };
}

function callWorker(action, payload, transferables = []) {
  return new Promise((resolve, reject) => {
    if (!state.worker) {
      reject(new Error("WebAssembly engine is still starting. Please wait a moment..."));
      return;
    }
    state.activeJobId++;
    const id = state.activeJobId;
    state.pendingRequests.set(id, { resolve, reject });
    state.worker.postMessage({ id, action, payload }, transferables);
  });
}

// --------------------------------------------------------------------------
// Settings Getters
// --------------------------------------------------------------------------

function getSelectedRadio(name) {
  const el = document.querySelector(`input[name="${name}"]:checked`);
  return el ? el.value : null;
}

function getSettings() {
  return {
    outFmt: getSelectedRadio("out_fmt") || "TIFF",
    suffix: el.suffixInput.value.trim() || "_8bit",
    hiMode: getSelectedRadio("hi_mode") || "scale",
    alphaMode: getSelectedRadio("alpha_mode") || "white",
    customColor: el.customColorPicker.value || "#ffffff",
    iccMode: getSelectedRadio("icc_mode") || "ignore"
  };
}

// --------------------------------------------------------------------------
// File Handling & Queue
// --------------------------------------------------------------------------

async function handleFiles(fileList) {
  if (!fileList || fileList.length === 0) return;

  const validExts = [".tif", ".tiff", ".bmp"];
  let addedCount = 0;

  for (const file of fileList) {
    const ext = "." + file.name.split(".").pop().toLowerCase();
    if (!validExts.includes(ext)) {
      log(`Skipped unsupported file: ${file.name}`, "warn");
      continue;
    }

    const id = "file_" + Date.now() + "_" + Math.random().toString(36).substr(2, 6);
    const arrayBuffer = await file.arrayBuffer();

    const item = {
      id,
      file,
      buffer: arrayBuffer,
      info: null,
      status: "inspecting",
      convertedBlob: null,
      outFilename: null,
      outSizeBytes: 0
    };

    state.files.push(item);
    addedCount++;
    renderTable();
    updateStorageMeter();

    // Inspect file in worker
    try {
      // Send a copy of the buffer to describe
      const info = await callWorker("describe", {
        filename: file.name,
        buffer: arrayBuffer.slice(0)
      });
      item.info = info;
      item.status = "queued";
    } catch (err) {
      log(`Error inspecting ${file.name}: ${err.message}`, "error");
      item.status = "error";
      item.info = { mode: "Error", depth: "-", size: "-", frames: 1 };
    }

    renderTable();
  }

  if (addedCount > 0) {
    log(`Added ${addedCount} file(s) to queue.`, "info");
    if (!state.selectedId && state.files.length > 0) {
      selectFile(state.files[0].id);
    } else {
      schedulePreview();
    }
  }
}

function selectFile(id) {
  state.selectedId = id;
  const item = state.files.find(f => f.id === id);
  if (item && item.info) {
    el.frameInput.max = item.info.frames || 1;
    el.frameInput.value = 1;
    el.frameCountLabel.textContent = `/ ${item.info.frames || 1}`;
  }
  renderTable();
  schedulePreview(50);
}

function renderTable() {
  el.fileCount.textContent = state.files.length;
  updateQueueButtons();

  if (state.files.length === 0) {
    el.filesTbody.innerHTML = `
      <tr class="empty-row">
        <td colspan="6">No files added yet. Drop files above to begin.</td>
      </tr>`;
    clearPreview("No files added. Drop files to see preview.");
    return;
  }

  el.filesTbody.innerHTML = "";
  state.files.forEach((item) => {
    const tr = document.createElement("tr");
    if (item.id === state.selectedId) {
      tr.classList.add("selected");
    }

    tr.onclick = () => selectFile(item.id);

    const info = item.info || {
      mode: "...",
      depth: "...",
      size: "...",
      frames: "..."
    };

    let statusHtml = "";
    if (item.status === "inspecting") {
      statusHtml = `<span class="status-badge processing">Inspecting</span>`;
    } else if (item.status === "queued") {
      statusHtml = `<span class="status-badge queued">Queued</span>`;
    } else if (item.status === "converting") {
      statusHtml = `<span class="status-badge processing">Converting...</span>`;
    } else if (item.status === "done") {
      statusHtml = `
        <span class="status-badge done">Done</span>
        <a class="download-link" title="Download converted file">Download</a>`;
    } else if (item.status === "error") {
      statusHtml = `<span class="status-badge error">Failed</span>`;
    }

    tr.innerHTML = `
      <td title="${item.file.name}" style="font-weight: 500; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
        ${item.file.name}
      </td>
      <td>${info.mode || "?"}</td>
      <td>${info.depth || "?"}</td>
      <td>${info.size || "?"}</td>
      <td>${info.frames || 1}</td>
      <td>${statusHtml}</td>
    `;

    // Download click handler for individual row
    const downloadLink = tr.querySelector(".download-link");
    if (downloadLink && item.convertedBlob) {
      downloadLink.onclick = (e) => {
        e.stopPropagation();
        triggerDownload(item.convertedBlob, item.outFilename);
      };
    }

    el.filesTbody.appendChild(tr);
  });
}

function updateQueueButtons() {
  const hasFiles = state.files.length > 0;
  const isReady = state.workerReady;
  el.btnConvertAll.disabled = !hasFiles || !isReady;
  el.btnClearAll.disabled = !hasFiles;

  const anyDone = state.files.some(f => f.status === "done" && f.convertedBlob);
  if (anyDone) {
    el.btnDownloadZip.classList.remove("hidden");
  } else {
    el.btnDownloadZip.classList.add("hidden");
  }
}

// --------------------------------------------------------------------------
// Preview Rendering
// --------------------------------------------------------------------------

function clearPreview(msg) {
  el.previewBeforeImg.classList.add("hidden");
  el.previewAfterImg.classList.add("hidden");
  el.beforePlaceholder.classList.remove("hidden");
  el.afterPlaceholder.classList.remove("hidden");
  el.previewMeta.textContent = msg;
  el.previewNote.classList.add("hidden");
}

function schedulePreview(delay = 150) {
  if (state.previewDebounceTimer) {
    clearTimeout(state.previewDebounceTimer);
  }
  state.previewDebounceTimer = setTimeout(renderPreview, delay);
}

async function renderPreview() {
  if (!state.workerReady) return;
  const item = state.files.find(f => f.id === state.selectedId);
  if (!item) {
    clearPreview("Add a file and select it to see a preview.");
    return;
  }

  el.previewMeta.textContent = "Rendering preview...";
  const settings = getSettings();
  const frameIndex = Math.max(0, parseInt(el.frameInput.value, 10) - 1 || 0);

  try {
    const res = await callWorker("preview", {
      filename: item.file.name,
      buffer: item.buffer.slice(0),
      frameIndex,
      alphaMode: settings.alphaMode,
      customColor: settings.customColor,
      hiMode: settings.hiMode,
      iccMode: settings.iccMode
    });

    el.previewBeforeImg.src = res.before_url;
    el.previewBeforeImg.classList.remove("hidden");
    el.beforePlaceholder.classList.add("hidden");

    el.previewAfterImg.src = res.after_url;
    el.previewAfterImg.classList.remove("hidden");
    el.afterPlaceholder.classList.add("hidden");

    el.previewMeta.textContent = res.info;

    if (res.notes && res.notes.length > 0) {
      el.previewNote.textContent = res.notes.join("; ");
      el.previewNote.classList.remove("hidden");
    } else {
      el.previewNote.classList.add("hidden");
    }

    el.frameInput.max = res.n_frames;
    el.frameCountLabel.textContent = `/ ${res.n_frames}`;
  } catch (err) {
    log(`Preview error for ${item.file.name}: ${err.message}`, "error");
    clearPreview(`Preview failed: ${err.message}`);
  }
}

// --------------------------------------------------------------------------
// Batch Conversion & ZIP Packaging
// --------------------------------------------------------------------------

async function convertAll() {
  if (!state.workerReady || state.files.length === 0) return;

  const settings = getSettings();
  el.btnConvertAll.disabled = true;
  el.btnClearAll.disabled = true;

  log(`--- Starting batch conversion (${state.files.length} file(s)) -> ${settings.outFmt} ---`, "info");

  let okCount = 0;
  let failCount = 0;

  for (let i = 0; i < state.files.length; i++) {
    const item = state.files[i];
    item.status = "converting";
    renderTable();

    const progressPct = Math.round(((i) / state.files.length) * 100);
    el.progressBarFill.style.width = `${progressPct}%`;
    el.progressLabel.textContent = `${i + 1} / ${state.files.length}`;

    try {
      const res = await callWorker("convert", {
        filename: item.file.name,
        buffer: item.buffer.slice(0),
        outFmt: settings.outFmt,
        alphaMode: settings.alphaMode,
        customColor: settings.customColor,
        hiMode: settings.hiMode,
        iccMode: settings.iccMode,
        suffix: settings.suffix
      });

      const mimeType = settings.outFmt === "BMP" ? "image/bmp" : "image/tiff";
      const blob = new Blob([res.bytes], { type: mimeType });

      item.status = "done";
      item.convertedBlob = blob;
      item.outFilename = res.filename;
      item.outSizeBytes = res.sizeBytes;
      okCount++;

      const noteText = res.note ? ` [${res.note}]` : "";
      log(`OK: ${item.file.name} -> ${res.filename} (${res.framesOut} frame(s), via ${res.reader})${noteText}`, "success");
    } catch (err) {
      item.status = "error";
      failCount++;
      log(`FAIL: ${item.file.name}: ${err.message}`, "error");
    }

    renderTable();
    updateStorageMeter();
  }

  el.progressBarFill.style.width = "100%";
  el.progressLabel.textContent = "Finished";
  log(`--- Conversion complete: ${okCount} succeeded, ${failCount} failed ---`, okCount > 0 ? "success" : "warn");

  el.btnConvertAll.disabled = false;
  el.btnClearAll.disabled = false;
  updateQueueButtons();
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function downloadAllZip() {
  if (typeof JSZip === "undefined") {
    log("JSZip library not loaded; downloading files individually...", "warn");
    state.files.forEach(f => {
      if (f.convertedBlob && f.outFilename) {
        triggerDownload(f.convertedBlob, f.outFilename);
      }
    });
    return;
  }

  const zip = new JSZip();
  let count = 0;

  state.files.forEach(f => {
    if (f.convertedBlob && f.outFilename) {
      zip.file(f.outFilename, f.convertedBlob);
      count++;
    }
  });

  if (count === 0) {
    log("No converted files available to zip.", "warn");
    return;
  }

  log(`Packaging ${count} file(s) into ZIP archive...`, "info");
  const zipBlob = await zip.generateAsync({ type: "blob", compression: "DEFLATE" });
  triggerDownload(zipBlob, "converted_8bit_images.zip");
  log("Downloaded converted_8bit_images.zip", "success");
}

// --------------------------------------------------------------------------
// Event Listeners Setup
// --------------------------------------------------------------------------

function setupEventListeners() {
  // Drag and drop
  const dropzone = el.dropzone;
  ["dragenter", "dragover"].forEach(evt => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.add("dragover");
    });
  });

  ["dragleave", "drop"].forEach(evt => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove("dragover");
    });
  });

  dropzone.addEventListener("drop", (e) => {
    const dt = e.dataTransfer;
    if (dt && dt.files) {
      handleFiles(Array.from(dt.files));
    }
  });

  dropzone.addEventListener("click", () => {
    el.fileInput.click();
  });

  el.fileInput.addEventListener("change", (e) => {
    if (e.target.files) {
      handleFiles(Array.from(e.target.files));
      el.fileInput.value = ""; // Reset
    }
  });

  // Buttons
  el.btnConvertAll.addEventListener("click", convertAll);
  el.btnDownloadZip.addEventListener("click", downloadAllZip);
  el.btnClearAll.addEventListener("click", () => {
    state.files = [];
    state.selectedId = null;
    renderTable();
    el.progressBarFill.style.width = "0%";
    el.progressLabel.textContent = "Ready";
    updateStorageMeter();
  });
  el.btnClearLog.addEventListener("click", () => {
    el.logConsole.innerHTML = "";
  });
  el.btnRefreshPreview.addEventListener("click", () => schedulePreview(0));

  // Frame selector
  el.frameInput.addEventListener("change", () => schedulePreview(50));
  el.frameInput.addEventListener("input", () => schedulePreview(100));

  // Option changes -> live preview refresh
  const radioInputs = document.querySelectorAll('input[type="radio"]');
  radioInputs.forEach(r => {
    r.addEventListener("change", () => schedulePreview(100));
  });

  el.customColorPicker.addEventListener("input", () => {
    const customRadio = document.querySelector('input[name="alpha_mode"][value="custom"]');
    if (customRadio) customRadio.checked = true;
    schedulePreview(150);
  });

  // PWA Install Prompt
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    state.deferredPrompt = e;
    el.installBtn.classList.remove("hidden");
  });

  el.installBtn.addEventListener("click", async () => {
    if (state.deferredPrompt) {
      state.deferredPrompt.prompt();
      const choice = await state.deferredPrompt.userChoice;
      if (choice.outcome === "accepted") {
        log("App installed as standalone PWA.", "success");
      }
      state.deferredPrompt = null;
      el.installBtn.classList.add("hidden");
    }
  });
}

// --------------------------------------------------------------------------
// Service Worker Registration (PWA Offline Support)
// --------------------------------------------------------------------------

function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return Promise.resolve();
  return navigator.serviceWorker.register("sw.js")
    .then(() => navigator.serviceWorker.ready)
    .then(() => {
      log("ServiceWorker active (offline caching enabled).", "info");
      if (navigator.serviceWorker.controller) return;
      // First visit: wait for clients.claim() so engine downloads are cached.
      return new Promise((resolve) => {
        navigator.serviceWorker.addEventListener("controllerchange", resolve, { once: true });
        setTimeout(resolve, 4000);
      });
    })
    .catch((err) => {
      console.warn("ServiceWorker registration failed:", err);
    });
}

// Initialize Application
setupEventListeners();
updateStorageMeter();
registerServiceWorker().then(initWorker);
