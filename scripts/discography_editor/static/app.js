"use strict";

/** @typedef {{id:number, group:string, section:string, display_order:number, title:string,
 * url:string, track_id:string, total_streams:number|null, on_album:boolean, role:string,
 * extra_type:string, category:string, release_edition:string, display_album:string,
 * display_section:string, primary_artist:string, featured_artists:string[], song_family:string,
 * version_tag:string|null, chart_extra:boolean, tags:string[]}} Row */

let SAVED = null; // { rows: Row[], options: {...}, generated_at }
const dirty = new Map(); // rowId -> { field: value }
const rowErrors = new Map(); // rowId -> message
const doneSet = new Set(); // review_key of tracks ticked "déjà vérifié"

const els = {};

function $(sel) { return document.querySelector(sel); }

function slug(text) {
  return String(text).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

// snake_case, not kebab-case: `section` doubles as a filename and is matched
// against a fixed list of slugs elsewhere (export_for_web.py _EXTRA_SECTIONS),
// so it must follow that convention — unlike the free-text `display_section`.
function slugSnake(text) {
  return String(text).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

function arraysEqual(a, b) {
  const x = a || [];
  const y = b || [];
  if (x.length !== y.length) return false;
  return x.every((v, i) => v === y[i]);
}

function originalRow(id) {
  return SAVED.rows.find((r) => r.id === id);
}

function effectiveRow(row) {
  const d = dirty.get(row.id);
  return d ? { ...row, ...d } : row;
}

function groupInfo(key) {
  return SAVED.options.groups.find((g) => g.key === key);
}

function setDirty(rowId, field, value) {
  const row = originalRow(rowId);
  const orig = row[field];
  const same = Array.isArray(orig) ? arraysEqual(orig, value) : orig === value;

  let entry = dirty.get(rowId);
  if (same) {
    if (entry) {
      delete entry[field];
      if (Object.keys(entry).length === 0) dirty.delete(rowId);
    }
  } else {
    if (!entry) {
      entry = {};
      dirty.set(rowId, entry);
    }
    entry[field] = value;
  }
  rowErrors.delete(rowId);
  renderAll();
}

// ---------- data loading ----------

async function loadState() {
  const res = await fetch("/api/state");
  SAVED = await res.json();
  syncDoneSetFromSaved();
}

function syncDoneSetFromSaved() {
  doneSet.clear();
  for (const row of SAVED.rows) if (row.done) doneSet.add(row.review_key);
}

async function toggleDone(row, wantDone) {
  if (wantDone) doneSet.add(row.review_key); else doneSet.delete(row.review_key);
  renderAll();
  try {
    await fetch("/api/mark-done", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: row.review_key, done: wantDone }),
    });
  } catch (e) {
    setStatus("Erreur réseau lors du marquage « fait » : " + e, "error");
  }
}

// ---------- rendering ----------

function fmtStreams(n) {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("en-US");
}

function makeTextInput(value, onCommit, opts = {}) {
  const input = document.createElement("input");
  input.type = "text";
  input.value = value ?? "";
  if (opts.placeholder) input.placeholder = opts.placeholder;
  input.addEventListener("change", () => onCommit(input.value));
  return input;
}

/** Text input + a "▾" button that always opens the FULL list of existing
 * values (native <datalist> only shows values matching what's already typed,
 * which hid most options — this always shows everything). Still freeform:
 * typing a value not in the list and committing (blur/Enter) is allowed. */
function makeComboInput(value, getOptions, onCommit, opts = {}) {
  const wrap = document.createElement("div");
  wrap.className = "combo";

  const input = document.createElement("input");
  input.type = "text";
  input.value = value ?? "";
  if (opts.placeholder) input.placeholder = opts.placeholder;
  input.addEventListener("change", () => onCommit(input.value));
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") input.blur(); });

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "combo-arrow";
  btn.textContent = "▾";
  btn.tabIndex = -1;
  btn.addEventListener("mousedown", (e) => e.preventDefault()); // keep input focus/value intact
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    openComboPopover(wrap, getOptions(), (picked) => {
      input.value = picked;
      onCommit(picked);
    });
  });

  wrap.appendChild(input);
  wrap.appendChild(btn);
  return wrap;
}

/** Native <select> for closed vocabularies (role/extra_type/category) — unlike
 * makeComboInput this can't take free text, but it shows the full selected
 * label instead of a tiny text box that truncates to a single character in a
 * narrow column. Always includes the current value even if it's not in
 * `options`, so nothing already saved is ever silently dropped from view. */
function makeSelectInput(value, options, onCommit) {
  const select = document.createElement("select");
  const allValues = value && !options.includes(value) ? [value, ...options] : options;

  const emptyOpt = document.createElement("option");
  emptyOpt.value = "";
  emptyOpt.textContent = "—";
  select.appendChild(emptyOpt);

  for (const v of allValues) {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = v;
    if (v === value) opt.selected = true;
    select.appendChild(opt);
  }
  select.addEventListener("change", () => onCommit(select.value));
  return select;
}

function makeGroupSelect(row, eff) {
  const select = document.createElement("select");
  for (const g of SAVED.options.groups) {
    const opt = document.createElement("option");
    opt.value = g.key;
    opt.textContent = g.label;
    if (g.key === eff.group) opt.selected = true;
    select.appendChild(opt);
  }
  select.addEventListener("change", () => setDirty(row.id, "group", select.value));
  return select;
}

function buildTagsCell(row, eff) {
  const cell = document.createElement("div");
  cell.className = "tags-cell";
  if (eff.tags && eff.tags.length) {
    eff.tags.forEach((t) => {
      const chip = document.createElement("span");
      chip.className = "tag-chip";
      chip.textContent = t;
      cell.appendChild(chip);
    });
  } else {
    const empty = document.createElement("span");
    empty.className = "tags-empty";
    empty.textContent = "— cliquer pour éditer —";
    cell.appendChild(empty);
  }
  cell.addEventListener("click", (e) => toggleTagsPopover(row.id, cell));
  return cell;
}

function buildDoneCell(row) {
  const td = document.createElement("td");
  td.className = "col-done";
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.className = "done-box";
  cb.title = "Marquer cette chanson comme déjà vérifiée";
  cb.checked = doneSet.has(row.review_key);
  cb.addEventListener("change", () => toggleDone(row, cb.checked));
  td.appendChild(cb);
  return td;
}

function isMarkedForDeletion(rowId) {
  return !!dirty.get(rowId)?._delete;
}

function toggleDeleteMark(row) {
  if (isMarkedForDeletion(row.id)) {
    const entry = dirty.get(row.id);
    delete entry._delete;
    if (Object.keys(entry).length === 0) dirty.delete(row.id);
  } else {
    if (!confirm(`Supprimer définitivement « ${row.title} » de la base ?\nCe n'est écrit sur disque qu'après avoir cliqué Enregistrer (backup automatique).`)) return;
    if (!dirty.has(row.id)) dirty.set(row.id, {});
    dirty.get(row.id)._delete = true;
  }
  rowErrors.delete(row.id);
  renderAll();
}

function buildTitleCell(row, eff) {
  const wrap = document.createElement("div");
  wrap.className = "title-cell";
  const deleted = isMarkedForDeletion(row.id);

  const input = makeTextInput(eff.title, (v) => setDirty(row.id, "title", v));
  input.disabled = deleted;
  wrap.appendChild(input);

  if (!deleted) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "url-edit-btn";
    btn.textContent = "🔗";
    btn.title = eff.track_id
      ? `Track ID : ${eff.track_id} — cliquer pour modifier l'URL Spotify`
      : "Pas d'URL — cliquer pour en renseigner une";
    btn.addEventListener("click", () => {
      const urlInput = document.createElement("input");
      urlInput.type = "text";
      urlInput.value = eff.url || "";
      urlInput.placeholder = "https://open.spotify.com/track/...";
      wrap.replaceChild(urlInput, input);
      btn.classList.add("hidden");
      urlInput.focus();
      const commit = () => setDirty(row.id, "url", urlInput.value.trim());
      urlInput.addEventListener("blur", commit); // triggers renderAll(), rebuilds a fresh cell
      urlInput.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") urlInput.blur();
        if (ev.key === "Escape") { urlInput.value = eff.url || ""; urlInput.blur(); }
      });
    });
    wrap.appendChild(btn);
  }

  const delBtn = document.createElement("button");
  delBtn.type = "button";
  delBtn.className = "delete-btn";
  delBtn.textContent = deleted ? "↩" : "🗑";
  delBtn.title = deleted ? "Annuler la suppression" : "Supprimer cette chanson";
  delBtn.addEventListener("click", () => toggleDeleteMark(eff));
  wrap.appendChild(delBtn);

  return wrap;
}

function buildRow(row) {
  const eff = effectiveRow(row);
  const tr = document.createElement("tr");
  tr.className = "track-row";
  if (dirty.has(row.id)) tr.classList.add("dirty");
  if (rowErrors.has(row.id)) tr.classList.add("row-error");
  if (doneSet.has(row.review_key)) tr.classList.add("row-done");
  if (isMarkedForDeletion(row.id)) tr.classList.add("pending-delete");
  tr.dataset.rowId = String(row.id);
  tr.dataset.search = [eff.title, eff.track_id, eff.song_family, eff.primary_artist]
    .filter(Boolean).join(" ").toLowerCase();

  const cell = (child) => { const td = document.createElement("td"); td.appendChild(child); return td; };
  const textCell = (value) => { const td = document.createElement("td"); td.textContent = value ?? ""; return td; };

  tr.appendChild(buildDoneCell(row));
  tr.appendChild(cell(buildTitleCell(row, eff)));

  const streamsTd = document.createElement("td");
  streamsTd.className = "streams-cell";
  streamsTd.textContent = fmtStreams(eff.total_streams);
  tr.appendChild(streamsTd);

  const onAlbumTd = document.createElement("td");
  onAlbumTd.className = "col-onalbum";
  const onAlbumBox = document.createElement("input");
  onAlbumBox.type = "checkbox";
  onAlbumBox.checked = !!eff.on_album;
  onAlbumBox.addEventListener("change", () => setDirty(row.id, "on_album", onAlbumBox.checked));
  onAlbumTd.appendChild(onAlbumBox);
  tr.appendChild(onAlbumTd);

  const chartTd = document.createElement("td");
  chartTd.className = "col-chartextra";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.className = "chart-extra-box";
  checkbox.checked = !!eff.chart_extra;
  checkbox.addEventListener("change", () => setDirty(row.id, "chart_extra", checkbox.checked));
  chartTd.appendChild(checkbox);
  tr.appendChild(chartTd);

  tr.appendChild(cell(makeSelectInput(eff.role, SAVED.options.roles, (v) => setDirty(row.id, "role", v))));
  tr.appendChild(cell(makeSelectInput(eff.extra_type, SAVED.options.extra_types, (v) => setDirty(row.id, "extra_type", v))));
  tr.appendChild(cell(makeSelectInput(eff.category, SAVED.options.categories, (v) => setDirty(row.id, "category", v))));
  tr.appendChild(cell(makeComboInput(eff.release_edition, () => SAVED.options.release_editions, (v) => setDirty(row.id, "release_edition", v))));
  tr.appendChild(cell(makeComboInput(eff.display_album, () => SAVED.options.album_names, (v) => setDirty(row.id, "display_album", v))));

  tr.appendChild(cell(makeTextInput(eff.display_section, (v) => {
    setDirty(row.id, "display_section", v);
    // Convenience only: suggest a matching `section` slug, but never override
    // a value the user already picked/typed themselves for this row.
    const entry = dirty.get(row.id);
    if (!entry || !("section" in entry)) {
      const suggestion = slugSnake(v);
      if (suggestion) setDirty(row.id, "section", suggestion);
    }
  })));
  tr.appendChild(cell(makeGroupSelect(row, eff)));

  tr.appendChild(cell(makeComboInput(
    eff.section,
    () => (groupInfo(eff.group) ? groupInfo(eff.group).sections : []),
    (v) => setDirty(row.id, "section", v),
  )));

  tr.appendChild(cell(makeComboInput(eff.primary_artist, () => SAVED.options.primary_artists, (v) => setDirty(row.id, "primary_artist", v))));

  const featuredStr = (Array.isArray(eff.featured_artists) ? eff.featured_artists : []).join(", ");
  tr.appendChild(cell(makeTextInput(featuredStr, (v) => {
    const list = v.split(",").map((s) => s.trim()).filter(Boolean);
    setDirty(row.id, "featured_artists", list);
  }, { placeholder: "ex: Ed Sheeran, Bon Iver" })));

  tr.appendChild(cell(makeTextInput(eff.song_family, (v) => setDirty(row.id, "song_family", v))));
  tr.appendChild(cell(makeComboInput(
    eff.version_tag,
    () => SAVED.options.version_tags.filter(Boolean),
    (v) => setDirty(row.id, "version_tag", v.trim() === "" ? null : v),
  )));

  tr.appendChild(cell(buildTagsCell(row, eff)));

  return tr;
}

function groupRows() {
  // Grouped by the row's SAVED (on-disk) group, not the pending edit — a track
  // stays put while you're deciding, and only actually jumps to its new album
  // section once "Enregistrer" has written the move to disk. Field values
  // shown within the row still reflect the pending edit via effectiveRow().
  const byGroup = new Map();
  for (const row of SAVED.rows) {
    if (!byGroup.has(row.group)) byGroup.set(row.group, []);
    byGroup.get(row.group).push(row);
  }
  return byGroup;
}

function renderAll() {
  const body = els.tbody;
  body.innerHTML = "";
  const byGroup = groupRows();

  for (const g of SAVED.options.groups) {
    const rows = byGroup.get(g.key) || [];
    if (rows.length === 0) continue;
    rows.sort((a, b) => (effectiveRow(a).display_order || 0) - (effectiveRow(b).display_order || 0));

    const headerTr = document.createElement("tr");
    headerTr.className = "group-header";
    headerTr.id = `group-${slug(g.key)}`;
    const th = document.createElement("td");
    th.colSpan = 18;
    th.textContent = `${g.label} — ${rows.length} tracks`;
    headerTr.appendChild(th);
    body.appendChild(headerTr);

    for (const row of rows) {
      const tr = buildRow(row);
      body.appendChild(tr);
      if (rowErrors.has(row.id)) {
        const errTr = document.createElement("tr");
        const td = document.createElement("td");
        td.colSpan = 18;
        td.className = "row-error-msg";
        td.textContent = "⚠ " + rowErrors.get(row.id);
        errTr.appendChild(td);
        body.appendChild(errTr);
      }
    }
  }

  applySearch();
  renderToolbar();
}

function renderToolbar() {
  const n = dirty.size;
  els.dirtyCount.textContent = `${n} modification${n > 1 ? "s" : ""}`;
  els.dirtyCount.classList.toggle("hidden", n === 0);
  els.saveBtn.classList.toggle("hidden", n === 0);
  els.discardBtn.classList.toggle("hidden", n === 0);
  els.rowCount.textContent = `${SAVED.rows.length} tracks · généré ${SAVED.generated_at}`;
}

function applySearch() {
  const term = els.search.value.trim().toLowerCase();
  const rows = els.tbody.querySelectorAll("tr.track-row");
  rows.forEach((tr) => {
    const match = !term || (tr.dataset.search || "").includes(term);
    tr.classList.toggle("filtered-out", !match);
  });
  els.tbody.querySelectorAll("tr.group-header").forEach((headerTr) => {
    let sib = headerTr.nextElementSibling;
    let anyVisible = false;
    while (sib && !sib.classList.contains("group-header")) {
      if (sib.classList.contains("track-row") && !sib.classList.contains("filtered-out")) anyVisible = true;
      sib = sib.nextElementSibling;
    }
    headerTr.classList.toggle("filtered-out", !anyVisible);
  });
}

// ---------- shared popover (filter_tags multi-select + combo single-select) ----------

let activePopoverAnchor = null;

function closePopover() {
  els.popover.classList.add("hidden");
  els.popover.innerHTML = "";
  activePopoverAnchor = null;
}

function positionPopover(anchorEl) {
  const rect = anchorEl.getBoundingClientRect();
  els.popover.style.top = `${rect.bottom + 4}px`;
  els.popover.style.left = `${Math.min(rect.left, window.innerWidth - 260)}px`;
}

/** Single-select popover always listing every existing value (used for Type,
 * Édition, Section, Primary artist, Version tag comboboxes). */
function openComboPopover(anchorEl, options, onPick) {
  if (activePopoverAnchor === anchorEl) { closePopover(); return; }
  els.popover.innerHTML = "";
  if (!options.length) {
    const empty = document.createElement("div");
    empty.className = "combo-empty";
    empty.textContent = "Aucune valeur existante pour l'instant — tape une valeur libre.";
    els.popover.appendChild(empty);
  }
  for (const val of options) {
    const item = document.createElement("div");
    item.className = "combo-option";
    item.textContent = val;
    item.addEventListener("click", () => { onPick(val); closePopover(); });
    els.popover.appendChild(item);
  }
  positionPopover(anchorEl);
  els.popover.classList.remove("hidden");
  activePopoverAnchor = anchorEl;
}

/** Multi-select checkbox popover for filter_tags. Rebuilds its checkbox list
 * in place on every toggle (without repositioning or touching visibility) so
 * it stays open and anchored while you tick several tags in a row — even
 * though each toggle triggers a full table re-render behind it. */
function renderTagsPopoverBody(rowId) {
  const eff = effectiveRow(originalRow(rowId));
  const current = new Set(eff.tags || []);

  els.popover.innerHTML = "";
  els.popover.dataset.rowId = String(rowId);
  for (const tag of SAVED.options.tags_vocab) {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = current.has(tag);
    cb.addEventListener("change", () => {
      const next = new Set(effectiveRow(originalRow(rowId)).tags || []);
      if (cb.checked) next.add(tag); else next.delete(tag);
      setDirty(rowId, "tags", Array.from(next));
      renderTagsPopoverBody(rowId); // rebuild in place, keep it open
    });
    label.appendChild(cb);
    label.appendChild(document.createTextNode(tag));
    els.popover.appendChild(label);
  }
}

function toggleTagsPopover(rowId, anchorEl) {
  if (activePopoverAnchor === anchorEl && !els.popover.classList.contains("hidden")) {
    closePopover();
    return;
  }
  renderTagsPopoverBody(rowId);
  positionPopover(anchorEl);
  els.popover.classList.remove("hidden");
  activePopoverAnchor = anchorEl;
}

document.addEventListener("click", (e) => {
  if (!els.popover || els.popover.classList.contains("hidden")) return;
  if (els.popover.contains(e.target)) return;
  if (e.target.closest(".tags-cell") || e.target.closest(".combo")) return;
  closePopover();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePopover(); });

// ---------- save / discard ----------

function setStatus(message, kind) {
  els.status.textContent = message;
  els.status.className = kind ? kind : "";
  els.status.classList.toggle("hidden", !message);
}

async function save() {
  const changes = {};
  for (const [id, fields] of dirty.entries()) changes[String(id)] = fields;

  els.saveBtn.disabled = true;
  setStatus("Enregistrement…", "");
  try {
    const res = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ changes }),
    });
    const data = await res.json();
    if (data.ok) {
      SAVED = data.state;
      syncDoneSetFromSaved();
      dirty.clear();
      rowErrors.clear();
      setStatus(`Enregistré avec succès (${Object.keys(changes).length} tracks modifiés).`, "success");
      renderAll();
    } else {
      for (const err of data.errors || []) {
        const m = err.match(/^row (\d+):/);
        if (m) rowErrors.set(Number(m[1]), err);
      }
      setStatus("Échec de l'enregistrement — rien n'a été écrit sur disque :\n" + (data.errors || []).join("\n"), "error");
      renderAll();
    }
  } catch (e) {
    setStatus("Erreur réseau : " + e, "error");
  } finally {
    els.saveBtn.disabled = false;
  }
}

function discard() {
  if (dirty.size && !confirm("Annuler toutes les modifications en attente ?")) return;
  dirty.clear();
  rowErrors.clear();
  setStatus("", "");
  renderAll();
}

// ---------- init ----------

function populateJumpGroup() {
  els.jumpGroup.innerHTML = '<option value="">Aller à un album…</option>';
  for (const g of SAVED.options.groups) {
    const opt = document.createElement("option");
    opt.value = g.key;
    opt.textContent = g.label;
    els.jumpGroup.appendChild(opt);
  }
  els.jumpGroup.addEventListener("change", () => {
    if (!els.jumpGroup.value) return;
    const target = document.getElementById(`group-${slug(els.jumpGroup.value)}`);
    if (target) target.scrollIntoView({ block: "start" });
    els.jumpGroup.value = "";
  });
}

window.addEventListener("beforeunload", (e) => {
  if (dirty.size > 0) { e.preventDefault(); e.returnValue = ""; }
});

async function init() {
  els.tbody = $("#table-body");
  els.search = $("#search");
  els.jumpGroup = $("#jump-group");
  els.dirtyCount = $("#dirty-count");
  els.saveBtn = $("#save-btn");
  els.discardBtn = $("#discard-btn");
  els.status = $("#status-bar");
  els.rowCount = $("#row-count");
  els.popover = $("#tags-popover");

  await loadState();
  populateJumpGroup();
  renderAll();

  $("#loading").classList.add("hidden");
  $("#disco-table").classList.remove("hidden");

  els.search.addEventListener("input", applySearch);
  els.saveBtn.addEventListener("click", save);
  els.discardBtn.addEventListener("click", discard);
}

init();
