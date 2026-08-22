const OUTPUT_W = 2212;
const OUTPUT_H = 608;
const PREVIEW_W = 1106;
const PREVIEW_H = 304;
const THEME_KEY = "headerEditorTheme";
const DRAG_MIME = "application/x-shelf-item";

const els = {
  themeToggle: document.querySelector("#themeToggle"),
  file: document.querySelector("#fileInput"),
  shelfCount: document.querySelector("#shelfCount"),
  applyFiltersAll: document.querySelector("#applyFiltersAll"),
  clearShelfBtn: document.querySelector("#clearShelfBtn"),
  shelf: document.querySelector("#shelf"),
  zoom: document.querySelector("#zoom"),
  posX: document.querySelector("#posX"),
  posY: document.querySelector("#posY"),
  brightness: document.querySelector("#brightness"),
  contrast: document.querySelector("#contrast"),
  saturation: document.querySelector("#saturation"),
  zoomValue: document.querySelector("#zoomValue"),
  posXValue: document.querySelector("#posXValue"),
  posYValue: document.querySelector("#posYValue"),
  brightnessValue: document.querySelector("#brightnessValue"),
  contrastValue: document.querySelector("#contrastValue"),
  saturationValue: document.querySelector("#saturationValue"),
  fitCover: document.querySelector("#fitCover"),
  centerImage: document.querySelector("#centerImage"),
  resetFilters: document.querySelector("#resetFilters"),
  status: document.querySelector("#status"),
  canvas: document.querySelector("#preview"),
  dropZone: document.querySelector(".preview-shell"),
  targetFilter: document.querySelector("#targetFilter"),
  targetGrid: document.querySelector("#targetGrid"),
};

const ctx = els.canvas.getContext("2d");
let albums = [];
let img = null;
let activeId = null;
let nextId = 1;
let drag = null;
const shelf = [];
const state = { x: 0, y: 0, zoom: 1, brightness: 1, contrast: 1, saturation: 1, baseScale: 1 };

function setStatus(text) {
  els.status.textContent = text;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function fileStem(name) {
  return String(name || "header").replace(/\.[^.]+$/, "").trim() || "header";
}

// --- theme ---------------------------------------------------------------

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  els.themeToggle.textContent = theme === "light" ? "☀️" : "🌙";
  els.themeToggle.setAttribute("aria-label", theme === "light" ? "Switch to dark theme" : "Switch to light theme");
}

function initTheme() {
  let theme = "dark";
  try {
    theme = localStorage.getItem(THEME_KEY) || "dark";
  } catch {
    // storage unavailable, keep default
  }
  applyTheme(theme);
}

els.themeToggle.addEventListener("click", () => {
  const current = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  const next = current === "light" ? "dark" : "light";
  applyTheme(next);
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch {
    // storage unavailable, ignore
  }
  draw();
});

// --- shelf (imported / pulled-in images awaiting placement) ---------------

function activeItem() {
  return shelf.find((item) => item.id === activeId) || null;
}

function snapshotState() {
  return { ...state };
}

function applyState(nextState) {
  Object.assign(
    state,
    nextState || {
      x: 0,
      y: 0,
      zoom: 1,
      brightness: 1,
      contrast: 1,
      saturation: 1,
      baseScale: 1,
    },
  );
}

function persistActiveState() {
  const item = activeItem();
  if (item && img) item.state = snapshotState();
}

function syncInputs() {
  els.zoom.value = state.zoom;
  els.posX.value = Math.round(state.x);
  els.posY.value = Math.round(state.y);
  els.brightness.value = state.brightness;
  els.contrast.value = state.contrast;
  els.saturation.value = state.saturation;
  els.zoomValue.textContent = `${Math.round(state.zoom * 100)}%`;
  els.posXValue.textContent = `${Math.round(state.x)}px`;
  els.posYValue.textContent = `${Math.round(state.y)}px`;
  els.brightnessValue.textContent = state.brightness.toFixed(2);
  els.contrastValue.textContent = state.contrast.toFixed(2);
  els.saturationValue.textContent = state.saturation.toFixed(2);
}

function resetFilters() {
  state.brightness = 1;
  state.contrast = 1;
  state.saturation = 1;
}

function renderShelf() {
  els.shelfCount.textContent = shelf.length ? `${shelf.length} image${shelf.length === 1 ? "" : "s"} in shelf` : "Shelf empty";
  els.applyFiltersAll.disabled = shelf.length < 2 || !activeItem();
  els.clearShelfBtn.disabled = !shelf.length;

  els.shelf.innerHTML = shelf
    .map(
      (item) => `
    <div class="shelf-item${item.id === activeId ? " active" : ""}" data-id="${item.id}" tabindex="0" draggable="true">
      <img class="shelf-thumb" src="${item.src}" alt="" draggable="false">
      <div class="shelf-meta">
        <div class="shelf-name-row">
          <div class="shelf-name">${escapeHtml(item.fileName)}</div>
          <button type="button" class="shelf-remove" title="Remove" aria-label="Remove ${escapeHtml(item.fileName)}">&times;</button>
        </div>
        <input class="shelf-save-name" type="text" value="${escapeHtml(item.variantName)}" placeholder="Save name (blank = auto)">
        ${item.fromTarget ? `<div class="shelf-origin">from ${escapeHtml(item.fromTarget)}</div>` : ""}
      </div>
    </div>
  `,
    )
    .join("");

  els.shelf.querySelectorAll(".shelf-item").forEach((node) => {
    const id = Number(node.dataset.id);
    node.addEventListener("click", (event) => {
      if (event.target.matches("input,button")) return;
      selectShelfItem(id);
    });
    node.addEventListener("keydown", (event) => {
      if (event.target.matches("input,select")) return;
      if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        removeShelfItem(id);
      }
    });
    node.addEventListener("dragstart", (event) => {
      if (event.target.matches("input,button")) {
        event.preventDefault();
        return;
      }
      if (id === activeId) persistActiveState();
      event.dataTransfer.setData(DRAG_MIME, String(id));
      event.dataTransfer.effectAllowed = "move";
      node.classList.add("dragging");
    });
    node.addEventListener("dragend", () => node.classList.remove("dragging"));
    node.querySelector(".shelf-remove").addEventListener("click", (event) => {
      event.stopPropagation();
      removeShelfItem(id);
    });
    node.querySelector(".shelf-save-name").addEventListener("input", (event) => {
      const item = shelf.find((entry) => entry.id === id);
      if (item) item.variantName = event.target.value;
    });
  });
}

function removeShelfItem(id) {
  const idx = shelf.findIndex((item) => item.id === id);
  if (idx === -1) return;
  const wasActive = activeId === id;
  shelf.splice(idx, 1);
  if (wasActive) {
    activeId = null;
    img = null;
    if (shelf.length) {
      selectShelfItem(shelf[Math.min(idx, shelf.length - 1)].id);
    } else {
      draw();
      setStatus("Shelf empty. Import images or click a target to continue.");
    }
  }
  renderShelf();
}

els.clearShelfBtn.addEventListener("click", () => {
  if (!shelf.length) return;
  if (!confirm(`Remove all ${shelf.length} shelf image(s)? This does not delete already-saved files.`)) return;
  shelf.length = 0;
  activeId = null;
  img = null;
  renderShelf();
  draw();
  setStatus("Shelf cleared.");
});

els.applyFiltersAll.addEventListener("click", () => {
  const source = activeItem();
  if (!source || !source.state) {
    setStatus("Select an image to copy filters from first.");
    return;
  }
  const { brightness, contrast, saturation } = source.state;
  let count = 0;
  for (const item of shelf) {
    if (item.id === source.id) continue;
    if (item.state) {
      item.state.brightness = brightness;
      item.state.contrast = contrast;
      item.state.saturation = saturation;
      count += 1;
    }
  }
  setStatus(`Copied brightness/contrast/saturation from ${source.fileName} to ${count} other opened image(s).`);
});

// --- drawing -----------------------------------------------------------------

function fitCover() {
  if (!img) return;
  state.baseScale = Math.max(PREVIEW_W / img.naturalWidth, PREVIEW_H / img.naturalHeight);
  state.zoom = 1;
  state.x = (PREVIEW_W - img.naturalWidth * state.baseScale) / 2;
  state.y = (PREVIEW_H - img.naturalHeight * state.baseScale) / 2;
  persistActiveState();
  syncInputs();
  draw();
}

function drawImageTo(targetCtx, sourceImage, sourceState, scale = 1) {
  const drawScale = sourceState.baseScale * sourceState.zoom * scale;
  targetCtx.save();
  targetCtx.filter = `brightness(${sourceState.brightness}) contrast(${sourceState.contrast}) saturate(${sourceState.saturation})`;
  targetCtx.imageSmoothingEnabled = true;
  targetCtx.imageSmoothingQuality = "high";
  targetCtx.drawImage(
    sourceImage,
    sourceState.x * scale,
    sourceState.y * scale,
    sourceImage.naturalWidth * drawScale,
    sourceImage.naturalHeight * drawScale,
  );
  targetCtx.restore();
}

function draw(targetCtx = ctx, scale = 1, sourceImage = img, sourceState = state) {
  const w = PREVIEW_W * scale;
  const h = PREVIEW_H * scale;
  targetCtx.clearRect(0, 0, w, h);
  targetCtx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--canvas-bg") || "#111";
  targetCtx.fillRect(0, 0, w, h);
  if (!sourceImage) {
    targetCtx.fillStyle = "#888";
    targetCtx.font = `${18 * scale}px Inter, Arial`;
    targetCtx.textAlign = "center";
    targetCtx.fillText("Import, drop, or paste images to begin", w / 2, h / 2);
    return;
  }
  drawImageTo(targetCtx, sourceImage, sourceState, scale);
}

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const next = new Image();
    next.onload = () => resolve(next);
    next.onerror = reject;
    next.src = src;
  });
}

function fittedStateFor(image) {
  const baseScale = Math.max(PREVIEW_W / image.naturalWidth, PREVIEW_H / image.naturalHeight);
  return {
    x: (PREVIEW_W - image.naturalWidth * baseScale) / 2,
    y: (PREVIEW_H - image.naturalHeight * baseScale) / 2,
    zoom: 1,
    brightness: 1,
    contrast: 1,
    saturation: 1,
    baseScale,
  };
}

async function selectShelfItem(id) {
  const item = shelf.find((entry) => entry.id === id);
  if (!item) return;
  persistActiveState();
  activeId = id;
  img = await loadImage(item.src);
  if (!item.state) item.state = fittedStateFor(img);
  applyState(item.state);
  syncInputs();
  draw();
  renderShelf();
  setStatus(`Editing ${item.fileName}.`);
}

async function loadCardVariantIntoShelf(targetName, variant) {
  persistActiveState();
  const item = {
    id: nextId++,
    fileName: variant.legacy ? `${targetName} (legacy)` : `${targetName} - ${variant.name}`,
    src: variant.url,
    variantName: variant.legacy ? "" : variant.name,
    state: null,
    fromTarget: targetName,
  };
  shelf.push(item);
  renderShelf();
  await selectShelfItem(item.id);
  setStatus(`Loaded "${variant.name}" from ${targetName} into the shelf. Adjust it, then drag it onto a target to save.`);
}

// --- targets grid ------------------------------------------------------------

function renderGrid() {
  const filter = (els.targetFilter.value || "").trim().toLowerCase();
  els.targetGrid.innerHTML = albums
    .map((target) => {
      const coverSrc = target.kind === "album" ? target.coverUrl || target.variants[0]?.url || "" : target.variants[0]?.url || "";
      const countLabel = target.variants.length ? `${target.variants.length} header${target.variants.length === 1 ? "" : "s"}` : "no header yet";
      const matches = !filter || target.name.toLowerCase().includes(filter);
      const variantsHtml =
        target.variants.length > 1
          ? `<div class="target-variants">${target.variants
              .map((v) => `<img class="variant-chip" data-variant-id="${escapeHtml(v.id)}" src="${escapeHtml(v.url)}" title="${escapeHtml(v.name)}">`)
              .join("")}</div>`
          : "";
      const coverHtml = coverSrc ? `<img class="target-cover" src="${escapeHtml(coverSrc)}" alt="">` : `<div class="target-cover placeholder">${escapeHtml(target.name)}</div>`;
      return `
      <div class="target-card${target.kind === "global" ? " global" : ""}${matches ? "" : " hidden"}" data-target="${escapeHtml(target.name)}">
        ${coverHtml}
        <div class="target-body">
          <span class="target-name">${escapeHtml(target.name)}</span>
          <span class="target-count">${countLabel}</span>
        </div>
        ${variantsHtml}
      </div>
    `;
    })
    .join("");

  els.targetGrid.querySelectorAll(".target-card").forEach((node) => {
    const targetName = node.dataset.target;
    node.addEventListener("click", (event) => {
      const chip = event.target.closest(".variant-chip");
      const target = albums.find((a) => a.name === targetName);
      if (!target) return;
      if (chip) {
        const variant = target.variants.find((v) => v.id === chip.dataset.variantId);
        if (variant) loadCardVariantIntoShelf(targetName, variant);
        return;
      }
      if (target.variants.length === 1) {
        loadCardVariantIntoShelf(targetName, target.variants[0]);
      } else if (target.variants.length > 1) {
        node.classList.toggle("expanded");
      } else {
        setStatus(`No header yet for ${targetName} - drag a fitted image here to create one.`);
      }
    });
    node.addEventListener("dragover", (event) => {
      if (!event.dataTransfer.types.includes(DRAG_MIME)) return;
      event.preventDefault();
      node.classList.add("drop-hover");
    });
    node.addEventListener("dragleave", () => node.classList.remove("drop-hover"));
    node.addEventListener("drop", (event) => {
      node.classList.remove("drop-hover");
      const raw = event.dataTransfer.getData(DRAG_MIME);
      if (!raw) return;
      event.preventDefault();
      const item = shelf.find((entry) => entry.id === Number(raw));
      if (item) saveShelfItemToTarget(item, targetName);
    });
  });
}

els.targetFilter.addEventListener("input", renderGrid);

async function loadAlbums() {
  const res = await fetch("/api/albums");
  const payload = await res.json();
  if (!payload.ok) throw new Error("Unable to load albums");
  albums = payload.albums;
  renderGrid();
  setStatus(`${albums.length} targets loaded. Import images, fit them, then drag onto a target.`);
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function addFilesToShelf(fileList) {
  const files = Array.from(fileList || []).filter((file) => file.type.startsWith("image/"));
  if (!files.length) return [];
  persistActiveState();
  const created = [];
  for (const file of files) {
    const src = await readFile(file);
    const item = {
      id: nextId++,
      fileName: file.name || "pasted-image.png",
      src,
      variantName: fileStem(file.name),
      state: null,
      fromTarget: null,
    };
    shelf.push(item);
    created.push(item);
  }
  renderShelf();
  await selectShelfItem(created[0].id);
  setStatus(`Imported ${created.length} image${created.length === 1 ? "" : "s"}. Fit it, then drag it onto a target below.`);
  return created;
}

// --- import: file picker, drag & drop, paste --------------------------------

els.file.addEventListener("change", async () => {
  await addFilesToShelf(els.file.files);
  els.file.value = "";
});

["dragenter", "dragover"].forEach((evt) =>
  els.dropZone.addEventListener(evt, (event) => {
    if (event.dataTransfer.types.includes(DRAG_MIME)) return;
    event.preventDefault();
    els.dropZone.classList.add("drag-over");
  }),
);
["dragleave", "dragend"].forEach((evt) =>
  els.dropZone.addEventListener(evt, () => {
    els.dropZone.classList.remove("drag-over");
  }),
);
els.dropZone.addEventListener("drop", async (event) => {
  els.dropZone.classList.remove("drag-over");
  if (event.dataTransfer.types.includes(DRAG_MIME)) return;
  event.preventDefault();
  if (event.dataTransfer?.files?.length) {
    await addFilesToShelf(event.dataTransfer.files);
  }
});

window.addEventListener("paste", async (event) => {
  const active = document.activeElement;
  if (active && active.matches("input,textarea")) return;
  const items = Array.from(event.clipboardData?.items || []);
  const files = items
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter(Boolean);
  if (files.length) await addFilesToShelf(files);
});

// --- controls ----------------------------------------------------------------

for (const [key, input] of Object.entries({
  zoom: els.zoom,
  x: els.posX,
  y: els.posY,
  brightness: els.brightness,
  contrast: els.contrast,
  saturation: els.saturation,
})) {
  input.addEventListener("input", () => {
    state[key] = Number(input.value);
    persistActiveState();
    syncInputs();
    draw();
  });
}

els.fitCover.addEventListener("click", fitCover);
els.centerImage.addEventListener("click", () => {
  if (!img) return;
  const drawScale = state.baseScale * state.zoom;
  state.x = (PREVIEW_W - img.naturalWidth * drawScale) / 2;
  state.y = (PREVIEW_H - img.naturalHeight * drawScale) / 2;
  persistActiveState();
  syncInputs();
  draw();
});
els.resetFilters.addEventListener("click", () => {
  if (!img) return;
  resetFilters();
  persistActiveState();
  syncInputs();
  draw();
});

els.canvas.addEventListener("pointerdown", (event) => {
  els.canvas.setPointerCapture(event.pointerId);
  els.canvas.focus();
  drag = { startX: event.clientX, startY: event.clientY, x: state.x, y: state.y };
});
els.canvas.addEventListener("pointermove", (event) => {
  if (!drag) return;
  const rect = els.canvas.getBoundingClientRect();
  const sx = PREVIEW_W / rect.width;
  const sy = PREVIEW_H / rect.height;
  state.x = drag.x + (event.clientX - drag.startX) * sx;
  state.y = drag.y + (event.clientY - drag.startY) * sy;
  persistActiveState();
  syncInputs();
  draw();
});
els.canvas.addEventListener("pointerup", () => {
  drag = null;
});
els.canvas.addEventListener("pointercancel", () => {
  drag = null;
});

els.canvas.addEventListener(
  "wheel",
  (event) => {
    if (!img) return;
    event.preventDefault();
    const rect = els.canvas.getBoundingClientRect();
    const sx = PREVIEW_W / rect.width;
    const sy = PREVIEW_H / rect.height;
    const mouseX = (event.clientX - rect.left) * sx;
    const mouseY = (event.clientY - rect.top) * sy;
    const oldDrawScale = state.baseScale * state.zoom;
    const imgX = (mouseX - state.x) / oldDrawScale;
    const imgY = (mouseY - state.y) / oldDrawScale;
    const factor = event.deltaY < 0 ? 1.08 : 1 / 1.08;
    state.zoom = Math.min(4, Math.max(0.2, state.zoom * factor));
    const newDrawScale = state.baseScale * state.zoom;
    state.x = mouseX - imgX * newDrawScale;
    state.y = mouseY - imgY * newDrawScale;
    persistActiveState();
    syncInputs();
    draw();
  },
  { passive: false },
);

els.canvas.addEventListener("keydown", (event) => {
  if (!img) return;
  const step = event.shiftKey ? 10 : 1;
  let handled = true;
  if (event.key === "ArrowLeft") state.x -= step;
  else if (event.key === "ArrowRight") state.x += step;
  else if (event.key === "ArrowUp") state.y -= step;
  else if (event.key === "ArrowDown") state.y += step;
  else handled = false;
  if (!handled) return;
  event.preventDefault();
  persistActiveState();
  syncInputs();
  draw();
});

// --- save (drag a shelf item onto a target card) ------------------------------

async function pngForItem(item) {
  const sourceImage = item.id === activeId && img ? img : await loadImage(item.src);
  const sourceState = item.state || fittedStateFor(sourceImage);
  const out = document.createElement("canvas");
  out.width = OUTPUT_W;
  out.height = OUTPUT_H;
  draw(out.getContext("2d"), OUTPUT_W / PREVIEW_W, sourceImage, sourceState);
  return out.toDataURL("image/png");
}

async function saveShelfItemToTarget(item, targetName) {
  if (item.id === activeId) persistActiveState();
  setStatus(`Saving ${item.fileName} -> ${targetName}...`);
  try {
    const png = await pngForItem(item);
    const res = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        album: targetName,
        selectedVariant: "",
        variantName: (item.variantName || "").trim(),
        png,
      }),
    });
    const payload = await res.json();
    if (!payload.ok) throw new Error(payload.error || "unknown error");
    if (payload.albums) albums = payload.albums;
    removeShelfItem(item.id);
    renderGrid();
    setStatus(`Saved ${item.fileName} -> ${targetName}. (${payload.path}${payload.backup ? " | backup kept" : ""})`);
  } catch (err) {
    setStatus(`Save failed for ${item.fileName}: ${err.message || err}`);
  }
}

initTheme();
loadAlbums()
  .then(() => draw())
  .catch((err) => setStatus(String(err)));
