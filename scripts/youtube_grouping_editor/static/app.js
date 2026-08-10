"use strict";

const POOL_ID = "__pool__";

let videos = {};   // video_id -> {video_id, title, published_at, thumbnail_url}
let board = [];    // [{id, title, video_ids: [...], isPool}]
let dirty = false;
let newColumnCounter = 0;

const el = {
  loading: document.getElementById("loading"),
  board: document.getElementById("board"),
  status: document.getElementById("status-bar"),
  videoCount: document.getElementById("video-count"),
  dirtyBadge: document.getElementById("dirty-badge"),
  search: document.getElementById("search"),
  boardWrap: document.getElementById("board-wrap"),
  hideSingles: document.getElementById("hide-singles"),
  groupCount: document.getElementById("group-count"),
  addColumnBtn: document.getElementById("add-column-btn"),
  saveBtn: document.getElementById("save-btn"),
};

function setStatus(message, kind) {
  el.status.textContent = message;
  el.status.className = kind ? kind : "";
  if (!message) el.status.classList.add("hidden");
  else el.status.classList.remove("hidden");
}

function setDirty(value) {
  dirty = value;
  el.dirtyBadge.classList.toggle("hidden", !dirty);
}

window.addEventListener("beforeunload", (e) => {
  if (!dirty) return;
  e.preventDefault();
  e.returnValue = "";
});

function applyState(state) {
  videos = state.videos || {};
  board = [{ id: POOL_ID, title: "Non groupées", video_ids: [...(state.ungrouped_video_ids || [])], isPool: true }];
  for (const g of state.groups || []) {
    board.push({ id: g.title_key, title: g.title, video_ids: [...g.video_ids], isPool: false });
  }
  el.videoCount.textContent = `${Object.keys(videos).length} vidéos connues`;
  render();
}

async function loadState() {
  el.loading.classList.remove("hidden");
  el.board.classList.add("hidden");
  try {
    const res = await fetch("/api/state");
    const state = await res.json();
    applyState(state);
    setDirty(false);
    el.loading.classList.add("hidden");
    el.board.classList.remove("hidden");
  } catch (err) {
    setStatus("Échec du chargement : " + err, "error");
  }
}

function findColumn(id) {
  return board.find((c) => c.id === id);
}

function moveVideo(videoId, fromId, toId) {
  if (fromId === toId) return;
  const from = findColumn(fromId);
  const to = findColumn(toId);
  if (!from || !to) return;
  const idx = from.video_ids.indexOf(videoId);
  if (idx === -1) return;
  from.video_ids.splice(idx, 1);
  to.video_ids.push(videoId);
  setDirty(true);
  render();
}

function addColumn() {
  newColumnCounter += 1;
  const id = `__new_${newColumnCounter}`;
  board.push({ id, title: "", video_ids: [], isPool: false });
  setDirty(true);
  render();
  requestAnimationFrame(() => {
    const input = el.board.querySelector(`[data-column-id="${id}"] .column-title-input`);
    if (input) input.focus();
  });
}

function deleteColumn(id) {
  const col = findColumn(id);
  if (!col) return;
  if (col.video_ids.length && !window.confirm(`"${col.title || "(sans titre)"}" contient ${col.video_ids.length} vidéo(s) — les remettre dans "Non groupées" et supprimer la colonne ?`)) {
    return;
  }
  const pool = findColumn(POOL_ID);
  pool.video_ids.push(...col.video_ids);
  board = board.filter((c) => c.id !== id);
  setDirty(true);
  render();
}

function cardMatchesSearch(video, query) {
  if (!query) return true;
  const q = query.toLowerCase();
  return (video.title || "").toLowerCase().includes(q) || (video.video_id || "").toLowerCase().includes(q);
}

function renderCard(videoId, columnId) {
  const video = videos[videoId];
  if (!video) return null;
  const card = document.createElement("div");
  card.className = "card";
  card.draggable = true;
  card.dataset.videoId = videoId;

  card.addEventListener("dragstart", (e) => {
    e.dataTransfer.setData("text/plain", JSON.stringify({ videoId, fromColumnId: columnId }));
    e.dataTransfer.effectAllowed = "move";
    card.classList.add("dragging");
  });
  card.addEventListener("dragend", () => card.classList.remove("dragging"));

  const img = document.createElement("img");
  img.src = video.thumbnail_url;
  img.alt = "";
  img.loading = "lazy";
  card.appendChild(img);

  const body = document.createElement("div");
  body.className = "card-body";

  const title = document.createElement("div");
  title.className = "card-title";
  title.textContent = video.title || "(sans titre)";
  title.title = video.title || "";
  body.appendChild(title);

  const meta = document.createElement("div");
  meta.className = "card-meta";
  meta.innerHTML = `<span>${video.published_at || ""}</span><span class="vid">${video.video_id}</span>`;
  body.appendChild(meta);

  const currentCol = findColumn(columnId);
  const input = document.createElement("input");
  input.type = "text";
  input.className = "card-move";
  input.setAttribute("list", "group-options");
  input.placeholder = "Rechercher un groupe…";
  input.value = currentCol ? (currentCol.isPool ? currentCol.title : currentCol.title || "(sans titre)") : "";
  input.addEventListener("focus", () => {
    // Clear so the datalist shows every group, not just the one matching
    // the text already in the field (that text stays as the filter query
    // otherwise, hiding everything else).
    input.dataset.prevValue = input.value;
    input.value = "";
  });
  input.addEventListener("blur", () => {
    const target = board.find((c) => (c.isPool ? c.title : c.title || "(sans titre)") === input.value);
    if (!target) input.value = input.dataset.prevValue || "";
  });
  input.addEventListener("click", (e) => e.stopPropagation());
  input.addEventListener("input", () => {
    const target = board.find((c) => (c.isPool ? c.title : c.title || "(sans titre)") === input.value);
    if (target && target.id !== columnId) moveVideo(videoId, columnId, target.id);
  });
  body.appendChild(input);

  card.appendChild(body);
  return card;
}

function renderColumn(col) {
  const wrap = document.createElement("div");
  wrap.className = "column" + (col.isPool ? " pool" : "");
  wrap.dataset.columnId = col.id;

  const header = document.createElement("div");
  header.className = "column-header";

  if (col.isPool) {
    const label = document.createElement("div");
    label.className = "column-title-input";
    label.textContent = col.title;
    label.style.cursor = "default";
    header.appendChild(label);
  } else {
    const input = document.createElement("input");
    input.type = "text";
    input.className = "column-title-input";
    input.placeholder = "Nom du groupe…";
    input.value = col.title;
    input.addEventListener("input", () => {
      col.title = input.value;
      setDirty(true);
    });
    header.appendChild(input);
  }

  const count = document.createElement("span");
  count.className = "column-count";
  count.textContent = String(col.video_ids.length);
  header.appendChild(count);

  if (!col.isPool) {
    const delBtn = document.createElement("button");
    delBtn.className = "column-delete-btn";
    delBtn.textContent = "✕";
    delBtn.title = "Supprimer la colonne";
    delBtn.addEventListener("click", () => deleteColumn(col.id));
    header.appendChild(delBtn);
  }

  wrap.appendChild(header);

  const body = document.createElement("div");
  body.className = "column-body";
  body.addEventListener("dragover", (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    wrap.classList.add("dragover");
  });
  body.addEventListener("dragleave", () => wrap.classList.remove("dragover"));
  body.addEventListener("drop", (e) => {
    e.preventDefault();
    wrap.classList.remove("dragover");
    let payload;
    try {
      payload = JSON.parse(e.dataTransfer.getData("text/plain"));
    } catch (err) {
      return;
    }
    moveVideo(payload.videoId, payload.fromColumnId, col.id);
  });

  if (col.video_ids.length === 0) {
    const hint = document.createElement("div");
    hint.className = "column-empty-hint";
    hint.textContent = "Glisser des vidéos ici";
    body.appendChild(hint);
  } else {
    for (const videoId of col.video_ids) {
      const card = renderCard(videoId, col.id);
      if (card) body.appendChild(card);
    }
  }

  wrap.appendChild(body);
  return wrap;
}

function columnMatchesQuery(col, query) {
  if (!query) return true;
  if (cardMatchesSearch({ title: col.title, video_id: "" }, query)) return true;
  return col.video_ids.some((vid) => cardMatchesSearch(videos[vid] || {}, query));
}

// Rebuilds every column/card DOM node from `board`. Expensive (hundreds of
// nodes) — only call after board *structure* changes (load, add/delete
// column, drag a video). Search/hide-singles must NOT call this: see
// applyFilters().
function refreshGroupOptions() {
  const datalist = document.getElementById("group-options");
  datalist.innerHTML = "";
  const seen = new Set();
  for (const col of board) {
    const label = col.isPool ? col.title : col.title || "(sans titre)";
    if (seen.has(label)) continue;
    seen.add(label);
    const opt = document.createElement("option");
    opt.value = label;
    datalist.appendChild(opt);
  }
}

function buildBoard() {
  refreshGroupOptions();
  el.board.innerHTML = "";
  for (const col of board) {
    el.board.appendChild(renderColumn(col));
  }
}

// Cheap pass over the already-built DOM: toggles visibility classes only,
// no node creation. Safe to call on every keystroke.
function applyFilters({ scrollToTop = false } = {}) {
  const query = el.search.value.trim();
  const hideSingles = el.hideSingles.checked;
  let shownCount = 0;
  let totalCount = 0;

  for (const wrap of el.board.children) {
    const col = findColumn(wrap.dataset.columnId);
    if (!col) continue;

    for (const card of wrap.querySelectorAll(".card")) {
      const video = videos[card.dataset.videoId];
      card.classList.toggle("filtered-out", !cardMatchesSearch(video || {}, query));
    }

    if (col.isPool) continue;
    totalCount += 1;
    const isSingle = col.video_ids.length <= 1;
    const matches = columnMatchesQuery(col, query);
    const hideAsSingle = !query && hideSingles && isSingle;
    const hide = !matches || hideAsSingle;
    wrap.classList.toggle("hidden-by-filter", hide);
    if (!hide) shownCount += 1;
  }

  el.groupCount.textContent = `${shownCount} / ${totalCount} groupe(s) affiché(s)`;
  if (scrollToTop) el.boardWrap.scrollTop = 0;
}

function render() {
  buildBoard();
  applyFilters();
}

let searchDebounceTimer = null;
el.search.addEventListener("input", () => {
  clearTimeout(searchDebounceTimer);
  searchDebounceTimer = setTimeout(() => applyFilters({ scrollToTop: true }), 120);
});
el.hideSingles.addEventListener("change", () => applyFilters({ scrollToTop: true }));

el.addColumnBtn.addEventListener("click", addColumn);

el.saveBtn.addEventListener("click", async () => {
  // Find the culprit by id (not just by scanning the mapped title/video_ids
  // pairs) so we can reveal it — it may be hidden by the singles filter or a
  // leftover search query, which is why "there's nothing wrong" from the
  // user's point of view.
  const emptyTitled = board.find((c) => !c.isPool && c.video_ids.length && !c.title.trim());
  if (emptyTitled) {
    el.search.value = "";
    el.hideSingles.checked = false;
    applyFilters();
    const wrap = el.board.querySelector(`[data-column-id="${CSS.escape(emptyTitled.id)}"]`);
    if (wrap) {
      wrap.scrollIntoView({ behavior: "smooth", block: "center" });
      wrap.classList.add("flash-error");
      setTimeout(() => wrap.classList.remove("flash-error"), 2500);
      const input = wrap.querySelector(".column-title-input");
      if (input) input.focus();
    }
    setStatus(
      `Une colonne avec ${emptyTitled.video_ids.length} vidéo(s) n'a pas de titre — je l'ai affichée et mise en évidence ci-dessous.`,
      "error"
    );
    return;
  }

  const groups = board
    .filter((c) => !c.isPool)
    .map((c) => ({ title: c.title.trim(), video_ids: c.video_ids }));

  el.saveBtn.disabled = true;
  setStatus("Enregistrement…", "");
  try {
    const res = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ groups }),
    });
    const payload = await res.json();
    if (payload.ok) {
      applyState(payload.state);
      setDirty(false);
      setStatus("Enregistré.", "success");
    } else {
      setStatus("Erreur : " + (payload.errors || []).join(" · "), "error");
    }
  } catch (err) {
    setStatus("Échec de l'enregistrement : " + err, "error");
  } finally {
    el.saveBtn.disabled = false;
  }
});

loadState();
