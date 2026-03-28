import { state } from "./state.js";
import {
  formatFull, formatSigned, withCacheBuster, getDayData, getPreviousDate,
  formatArtists, formatArtistAlbum, normalizeAlbumName, getAlbumSectionPriority,
  getAlbumCover, filterSongsByQuery, normalize, persistSelectedDate,
  getQueryParam, sortDisplayBlocks, renderFocusModal
} from "./utils.js";
import {
  loadHistory, getCombineKey, enrichSongsForDate, sortSongs,
  combineSongVersions, withRankChanges
} from "./data.js";
import {
  renderTopbar, renderSearchBar, renderNewsSection, renderRankChange,
  renderStreamChange, renderPercentChange, songRow, renderStats
} from "./components.js";

/* =========================
   ALBUM IMAGE DOWNLOAD
========================= */

function _loadImg(url) {
  return new Promise((res, rej) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload  = () => res(img);
    img.onerror = rej;
    img.src = url;
  });
}

function _loadImgLocal(url) {
  return new Promise((res, rej) => {
    const img = new Image();
    img.onload  = () => res(img);
    img.onerror = rej;
    img.src = url;
  });
}

function _rrect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.arcTo(x + w, y,     x + w, y + r,     r);
  ctx.lineTo(x + w, y + h - r);
  ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
  ctx.lineTo(x + r, y + h);
  ctx.arcTo(x, y + h,     x, y + h - r,     r);
  ctx.lineTo(x, y + r);
  ctx.arcTo(x, y,         x + r, y,         r);
  ctx.closePath();
}

function _clip(ctx, x, y, w, h, r) {
  _rrect(ctx, x, y, w, h, r);
  ctx.clip();
}

function _ellipsis(ctx, text, maxW) {
  if (ctx.measureText(text).width <= maxW) return text;
  let t = text;
  while (t.length && ctx.measureText(t + "…").width > maxW) t = t.slice(0, -1);
  return t + "…";
}

/* colour helpers for dominant-colour edition tinting */
function _rgbToHsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  let h, s, l = (max + min) / 2;
  if (max === min) { h = s = 0; }
  else {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    switch (max) {
      case r: h = ((g - b) / d + (g < b ? 6 : 0)) / 6; break;
      case g: h = ((b - r) / d + 2) / 6; break;
      default: h = ((r - g) / d + 4) / 6;
    }
  }
  return [h * 360, s * 100, l * 100];
}
function _hslToRgb(h, s, l) {
  h /= 360; s /= 100; l /= 100;
  let r, g, b;
  if (s === 0) { r = g = b = l; }
  else {
    const hue2rgb = (p, q, t) => {
      if (t < 0) t += 1; if (t > 1) t -= 1;
      if (t < 1/6) return p + (q - p) * 6 * t;
      if (t < 1/2) return q;
      if (t < 2/3) return p + (q - p) * (2/3 - t) * 6;
      return p;
    };
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    r = hue2rgb(p, q, h + 1/3);
    g = hue2rgb(p, q, h);
    b = hue2rgb(p, q, h - 1/3);
  }
  return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
}
function _dominantRgb(img) {
  try {
    const tmp = document.createElement("canvas");
    tmp.width = tmp.height = 16;
    const c = tmp.getContext("2d");
    c.drawImage(img, 0, 0, 16, 16);
    const d = c.getImageData(0, 0, 16, 16).data;
    let r = 0, g = 0, b = 0;
    for (let i = 0; i < d.length; i += 4) { r += d[i]; g += d[i+1]; b += d[i+2]; }
    const n = d.length / 4;
    return [Math.round(r/n), Math.round(g/n), Math.round(b/n)];
  } catch { return [29, 185, 84]; }
}
function _shortenTitle(t) {
  return t
    .replace(/\(feat\.\s*/gi, "(ft. ")
    .replace(/\bDressing Room\s*/gi, "")
    .replace(/\bRehearsal\b/gi, "Reh.")
    .trim().replace(/\s{2,}/g, " ");
}
function _editionColors(dominantRgb, bi) {
  const [r, g, b] = dominantRgb;
  const [h, s] = _rgbToHsl(r, g, b);
  const sat     = Math.max(35, Math.min(s, 70));
  const lightBg = Math.max(88, 97 - bi * 4);
  const accent  = _hslToRgb(h, sat, 38);
  const totalBg = _hslToRgb(h, Math.min(sat, 50), lightBg);
  const css = ([r2,g2,b2]) => `rgb(${r2},${g2},${b2})`;
  return { accent: css(accent), totalBg: css(totalBg) };
}

async function downloadAlbumImage(albumName, blocks, totalStreams, totalDaily, dateLabel, coverUrl) {
  const W = 800, SCALE = 2;
  const PAD = 12;
  const HDR_H = 100;
  const COL_H = 18;   // single column-labels row (after header)
  const ROW_H = 32;   // song row
  const TOT_H = 36;   // section total row
  const ERA_H = 36;   // total bar
  const FTR_H = 28;   // footer

  /* sort songs by track order (display_order) for the image */
  function byTrackOrder(block) {
    return [...block.songs].sort((a, b) => {
      const ao = (a.appearances || []).find(ap => ap.album === albumName)?.display_order ?? 9999;
      const bo = (b.appearances || []).find(ap => ap.album === albumName)?.display_order ?? 9999;
      return ao - bo;
    });
  }

  /* compute canvas height */
  let innerH = HDR_H + COL_H;
  blocks.forEach((block) => {
    innerH += byTrackOrder(block).length * ROW_H + TOT_H;
  });
  innerH += ERA_H + FTR_H;

  const H = PAD * 2 + innerH;

  const canvas = document.createElement("canvas");
  canvas.width  = W * SCALE;
  canvas.height = H * SCALE;
  const ctx = canvas.getContext("2d");
  ctx.scale(SCALE, SCALE);

  const cx = PAD, cy = PAD, cw = W - PAD * 2;

  /* — background — */
  const bgGrad = ctx.createLinearGradient(0, 0, 0, H);
  bgGrad.addColorStop(0, "#f4f7f8");
  bgGrad.addColorStop(1, "#edf3f4");
  ctx.fillStyle = bgGrad;
  ctx.fillRect(0, 0, W, H);

  /* — white card — */
  ctx.shadowColor = "rgba(16,24,40,0.13)";
  ctx.shadowBlur  = 28;
  ctx.shadowOffsetY = 6;
  ctx.fillStyle = "#fff";
  _rrect(ctx, cx, cy, cw, H - PAD * 2, 18);
  ctx.fill();
  ctx.shadowColor = "transparent"; ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
  ctx.strokeStyle = "rgba(16,24,40,0.09)";
  ctx.lineWidth = 1;
  _rrect(ctx, cx, cy, cw, H - PAD * 2, 18);
  ctx.stroke();

  /* — header (rounded top) — */
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(cx + 18, cy);
  ctx.lineTo(cx + cw - 18, cy);
  ctx.arcTo(cx + cw, cy,     cx + cw, cy + 18, 18);
  ctx.lineTo(cx + cw, cy + HDR_H);
  ctx.lineTo(cx,      cy + HDR_H);
  ctx.lineTo(cx,      cy + 18);
  ctx.arcTo(cx, cy,   cx + 18, cy, 18);
  ctx.closePath();
  ctx.clip();

  /* try loading the album header image (crossOrigin — needed for getImageData/dominant color) */
  const _hdrBase2 = `../site/data/headers/${albumName.toLowerCase().replace(/ /g, "%20")}`;
  let headerLoaded = false;
  let hdrImgRef = null;
  let _resolvedHdr = null;
  try { _resolvedHdr = await _loadImg(`${_hdrBase2}.png`); } catch { /* try jpg */ }
  if (!_resolvedHdr) { try { _resolvedHdr = await _loadImg(`${_hdrBase2}.jpg`); } catch { /* no header */ } }
  try {
    const hdrImg = _resolvedHdr;
    if (!hdrImg) throw new Error("no header");
    hdrImgRef = hdrImg;
    const iw = hdrImg.naturalWidth, ih = hdrImg.naturalHeight;
    if (iw > 0 && ih > 0) {
      // object-fit: cover with top-anchor crop (show head/subject at top)
      const scale = Math.max(cw / iw, HDR_H / ih);
      const srcW = cw / scale, srcH = HDR_H / scale;
      const srcX = (iw - srcW) / 2;   // center horizontally
      const srcY = 0;                  // anchor to top
      ctx.drawImage(hdrImg, srcX, srcY, srcW, srcH, cx, cy, cw, HDR_H);
    } else {
      ctx.drawImage(hdrImg, cx, cy, cw, HDR_H);
    }
    ctx.fillStyle = "rgba(0,0,0,0.50)";
    ctx.fillRect(cx, cy, cw, HDR_H);
    headerLoaded = true;
  } catch { /* fallback below */ }

  if (!headerLoaded) {
    const hGrad = ctx.createLinearGradient(cx, cy, cx + cw, cy + HDR_H);
    hGrad.addColorStop(0,    "#0d1117");
    hGrad.addColorStop(0.55, "#131e15");
    hGrad.addColorStop(1,    "#0e1c24");
    ctx.fillStyle = hGrad;
    ctx.fillRect(cx, cy, cw, HDR_H);

    const g1 = ctx.createRadialGradient(cx + cw * .75, cy + HDR_H * .5, 0, cx + cw * .75, cy + HDR_H * .5, 160);
    g1.addColorStop(0, "rgba(29,185,84,.18)"); g1.addColorStop(1, "transparent");
    ctx.fillStyle = g1; ctx.fillRect(cx, cy, cw, HDR_H);
  }

  /* album cover + extract dominant color (prefer header image for accent, fallback to cover) */
  const SZ = 76, ax = cx + 20, ay = cy + (HDR_H - SZ) / 2;
  let dominantRgb = hdrImgRef ? _dominantRgb(hdrImgRef) : [29, 185, 84];
  try {
    const img = await _loadImg(coverUrl);
    if (!hdrImgRef) dominantRgb = _dominantRgb(img);
    ctx.save(); _clip(ctx, ax, ay, SZ, SZ, 10);
    ctx.drawImage(img, ax, ay, SZ, SZ);
    ctx.restore();
  } catch {
    ctx.fillStyle = "#1f2937"; _rrect(ctx, ax, ay, SZ, SZ, 10); ctx.fill();
  }
  ctx.restore();

  const accentCss = _editionColors(dominantRgb, 0).accent;

  /* header text — left-aligned next to the album cover, vertically centred */
  const tx = ax + SZ + 20;
  const textAreaW = cx + cw - tx - 12;
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";

  ctx.fillStyle = "#fff";
  ctx.font = "800 21px Inter,system-ui,sans-serif";
  ctx.fillText(_ellipsis(ctx, albumName, textAreaW), tx, cy + HDR_H / 2 - 20);

  ctx.fillStyle = "#fff";
  ctx.font = "700 15px Inter,system-ui,sans-serif";
  ctx.fillText("Taylor Swift · " + dateLabel, tx, cy + HDR_H / 2 + 0);

  ctx.fillStyle = accentCss;
  ctx.font = "700 11px Inter,system-ui,sans-serif";
  ctx.fillText("@swiftiescharts", tx, cy + HDR_H / 2 + 18);

  /* ── column labels row ── */
  const DAILY_X  = cx + cw - 230;
  const CHANGE_X = cx + cw - 118;
  const TOTAL_X  = cx + cw - 14;
  const altRowColor = `rgba(${dominantRgb[0]},${dominantRgb[1]},${dominantRgb[2]},0.07)`;

  let y = cy + HDR_H;
  ctx.fillStyle = "rgba(241,245,246,.95)";
  ctx.fillRect(cx, y, cw, COL_H);
  ctx.fillStyle = "rgba(16,24,40,.07)";
  ctx.fillRect(cx, y + COL_H - 1, cw, 1);
  ctx.fillStyle = "#9aa5b4";
  ctx.font = "700 9px Inter,system-ui,sans-serif";
  ctx.textBaseline = "middle";
  ctx.textAlign = "center";
  ctx.fillText("#", cx + 20, y + COL_H / 2);
  ctx.textAlign = "left";
  ctx.fillText("SONG", cx + 44, y + COL_H / 2);
  ctx.textAlign = "right";
  ctx.fillText("DAILY", DAILY_X, y + COL_H / 2);
  ctx.fillText("CHG", CHANGE_X, y + COL_H / 2);
  ctx.fillText("TOTAL", TOTAL_X, y + COL_H / 2);
  y += COL_H;

  for (let bi = 0; bi < blocks.length; bi++) {
    const block    = blocks[bi];
    const songs    = byTrackOrder(block);
    const secStr    = block.songs.reduce((s, sg) => s + (sg.streams || 0), 0);
    const secDaily  = block.songs.reduce((s, sg) => s + (sg.daily_streams || 0), 0);
    const secChange = block.songs.reduce((s, sg) => s + (sg.total_change || 0), 0);
    const secYest   = secDaily - secChange;
    const secPct    = secYest !== 0 ? (secChange / secYest * 100) : null;
    const colors   = _editionColors(dominantRgb, bi);

    /* ── full song rows ── */
    for (let si = 0; si < songs.length; si++) {
      const s  = songs[si];
      const rY = y;
      const mr = rY + ROW_H / 2;

      ctx.fillStyle = si % 2 === 0 ? "#fff" : altRowColor;
      ctx.fillRect(cx, rY, cw, ROW_H);

      /* track number */
      ctx.fillStyle = "#b0bac8";
      ctx.font = "500 11px Inter,system-ui,sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(String(si + 1), cx + 20, mr);

      /* title + version tag */
      const nameX = cx + 44;
      const maxW  = DAILY_X - nameX - 55;
      ctx.textAlign = "left"; ctx.textBaseline = "middle";
      ctx.fillStyle = "#101828";
      ctx.font = s.version_tag ? "600 12px Inter,system-ui,sans-serif" : "600 12.5px Inter,system-ui,sans-serif";
      ctx.fillText(_ellipsis(ctx, _shortenTitle(s.title_clean || s.title), maxW), nameX, mr - (s.version_tag ? 7 : 0));
      if (s.version_tag) {
        ctx.fillStyle = "#9aa5b4";
        ctx.font = "400 10px Inter,system-ui,sans-serif";
        ctx.fillText(_ellipsis(ctx, _shortenTitle(s.version_tag), maxW), nameX, mr + 8);
      }

      /* daily */
      ctx.textAlign = "right"; ctx.textBaseline = "middle";
      ctx.fillStyle = "#101828";
      ctx.font = "700 11.5px Inter,system-ui,sans-serif";
      ctx.fillText("+" + formatFull(s.daily_streams), DAILY_X, mr);

      /* change (absolute + percent stacked) */
      const chg = s.total_change || 0;
      const pct = s.percent_change != null ? s.percent_change : null;
      const chgColor = chg >= 0 ? "#1db954" : "#f04438";
      ctx.fillStyle = chgColor;
      ctx.font = "700 11px Inter,system-ui,sans-serif";
      ctx.fillText((chg >= 0 ? "+" : "") + formatFull(chg), CHANGE_X, mr - (pct != null ? 6 : 0));
      if (pct != null) {
        ctx.font = "500 9px Inter,system-ui,sans-serif";
        ctx.fillText((pct >= 0 ? "+" : "") + pct.toFixed(2) + "%", CHANGE_X, mr + 7);
      }

      /* total */
      ctx.fillStyle = "#344054";
      ctx.font = "700 11.5px Inter,system-ui,sans-serif";
      ctx.fillText(formatFull(s.streams), TOTAL_X, mr);

      y += ROW_H;
    }

    /* ── section total ── */
    ctx.fillStyle = colors.totalBg;
    ctx.fillRect(cx, y, cw, TOT_H);
    ctx.fillStyle = colors.accent;
    ctx.fillRect(cx, y, 4, TOT_H);

    ctx.fillStyle = "#101828";
    ctx.font = "700 11px Inter,system-ui,sans-serif";
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.fillText(block.name + "  —  Total", cx + 20, y + TOT_H / 2);

    ctx.textAlign = "right";
    ctx.font = "700 11px Inter,system-ui,sans-serif";
    ctx.fillStyle = "#101828";
    ctx.fillText("+" + formatFull(secDaily), DAILY_X, y + TOT_H / 2);
    ctx.fillStyle = secChange >= 0 ? "#067647" : "#b42318";
    ctx.fillText((secChange >= 0 ? "+" : "") + formatFull(secChange), CHANGE_X, y + TOT_H / 2 - (secPct != null ? 6 : 0));
    if (secPct != null) {
      ctx.font = "500 9px Inter,system-ui,sans-serif";
      ctx.fillText((secPct >= 0 ? "+" : "") + secPct.toFixed(1) + "%", CHANGE_X, y + TOT_H / 2 + 7);
    }
    ctx.font = "700 11px Inter,system-ui,sans-serif";
    ctx.fillStyle = "#101828";
    ctx.fillText(formatFull(secStr), TOTAL_X, y + TOT_H / 2);

    y += TOT_H;
  }

  /* ── total ── */
  const totalChange = blocks.reduce((s, b) => s + b.songs.reduce((ss, sg) => ss + (sg.total_change || 0), 0), 0);
  const totalYest   = totalDaily - totalChange;
  const totalPct    = totalYest !== 0 ? (totalChange / totalYest * 100) : null;

  ctx.fillStyle = "#0d1117";
  ctx.fillRect(cx, y, cw, ERA_H);
  const eraGlow = ctx.createRadialGradient(cx + cw * .5, y + ERA_H * .5, 0, cx + cw * .5, y + ERA_H * .5, 300);
  eraGlow.addColorStop(0, "rgba(29,185,84,.12)"); eraGlow.addColorStop(1, "transparent");
  ctx.fillStyle = eraGlow;
  ctx.fillRect(cx, y, cw, ERA_H);

  ctx.fillStyle = "rgba(255,255,255,.9)";
  ctx.font = "700 13px Inter,system-ui,sans-serif";
  ctx.textAlign = "left"; ctx.textBaseline = "middle";
  ctx.fillText("Total", cx + 20, y + ERA_H / 2);

  ctx.textAlign = "right";
  ctx.fillStyle = "rgba(255,255,255,.9)";
  ctx.font = "700 13px Inter,system-ui,sans-serif";
  ctx.fillText("+" + formatFull(totalDaily), DAILY_X, y + ERA_H / 2);

  ctx.fillStyle = totalChange >= 0 ? "#1db954" : "#f04438";
  ctx.font = "700 13px Inter,system-ui,sans-serif";
  ctx.fillText(
    (totalChange >= 0 ? "+" : "") + formatFull(totalChange),
    CHANGE_X, y + ERA_H / 2 - (totalPct != null ? 7 : 0)
  );
  if (totalPct != null) {
    ctx.font = "500 10px Inter,system-ui,sans-serif";
    ctx.fillText(
      (totalPct >= 0 ? "+" : "") + totalPct.toFixed(1) + "%",
      CHANGE_X, y + ERA_H / 2 + 8
    );
  }

  ctx.fillStyle = "#fff";
  ctx.font = "700 13px Inter,system-ui,sans-serif";
  ctx.fillText(formatFull(totalStreams), TOTAL_X, y + ERA_H / 2);

  y += ERA_H;

  /* ── footer ── */
  ctx.fillStyle = "#f1f5f6";
  ctx.fillRect(cx, y, cw, FTR_H);
  ctx.fillStyle = "rgba(16,24,40,.07)";
  ctx.fillRect(cx, y, cw, 1);

  ctx.fillStyle = accentCss;
  ctx.font = "700 11px Inter,system-ui,sans-serif";
  ctx.textAlign = "left"; ctx.textBaseline = "middle";
  ctx.fillText("@swiftiescharts", cx + 16, y + FTR_H / 2);

  ctx.fillStyle = "#667085";
  ctx.font = "500 11px Inter,system-ui,sans-serif";
  ctx.textAlign = "right";
  ctx.fillText(dateLabel, cx + cw - 16, y + FTR_H / 2);

  /* — download — */
  const link = document.createElement("a");
  link.download = `${albumName.replace(/[^a-z0-9]/gi, "_")}_${state.selectedDate}.png`;
  link.href = canvas.toDataURL("image/png");
  link.click();
}


/* =========================
   HOME PAGE
========================= */

function _buildHomeRows() {
  const raw    = enrichSongsForDate(state.selectedDate);
  const base   = state.combineVersions ? combineSongVersions(raw) : raw;
  const ranked = withRankChanges(base, state.selectedDate, state.sortMode);
  const filtered = filterSongsByQuery(ranked);
  const sorted   = sortSongs(filtered, state.sortMode);
  return { ranked, filtered, sorted };
}

function _bindHomeButtons() {
  document.getElementById("sortStreamsBtn")?.addEventListener("click", () => {
    state.sortMode = "streams"; localStorage.setItem("site-sort-mode", "streams"); updateHomeTable();
  });
  document.getElementById("sortDailyBtn")?.addEventListener("click", () => {
    state.sortMode = "daily"; localStorage.setItem("site-sort-mode", "daily"); updateHomeTable();
  });
  document.getElementById("combineBtn")?.addEventListener("click", () => {
    state.combineVersions = !state.combineVersions; localStorage.setItem("site-combine-versions", String(state.combineVersions)); updateHomeTable();
  });
}

export function updateHomeTable() {
  const { filtered, sorted } = _buildHomeRows();

  const tbody = document.getElementById("home-songs-body");
  if (tbody) tbody.innerHTML = sorted.map(songRow).join("");

  const desc = document.getElementById("home-sort-desc");
  if (desc) {
    desc.textContent = `${state.selectedDate} • sorted by ${state.sortMode === "daily" ? "daily streams" : "total streams"} • ${filtered.length} result${filtered.length !== 1 ? "s" : ""}`;
  }

  const btns = {
    sortStreamsBtn: state.sortMode === "streams",
    sortDailyBtn:  state.sortMode === "daily",
    combineBtn:    state.combineVersions,
  };
  for (const [id, active] of Object.entries(btns)) {
    const el = document.getElementById(id);
    if (el) el.className = active ? "active" : "";
  }

  const si = document.getElementById("searchInput");
  if (si && document.activeElement !== si) si.value = state.searchQuery;
}

export function renderHome(container) {
  const shell = document.getElementById("home-shell");

  // Partial update: date and data-generation unchanged → only refresh table
  // (sort/search/combine trigger updateHomeTable() directly; renderPage() forces full re-render via generation bump)
  if (shell && shell.dataset.date === state.selectedDate && shell.dataset.gen === String(state._dataGen || 0)) {
    updateHomeTable();
    return; // topbar/controls preserved in DOM, already bound
  }

  // Full render (first load or date changed)
  const { ranked, filtered, sorted } = _buildHomeRows();

  container.innerHTML = `
    <div id="home-shell" data-date="${state.selectedDate}" data-gen="${state._dataGen || 0}">
      ${renderTopbar()}
      ${renderNewsSection(ranked, state.selectedDate)}

      ${renderStats(ranked)}

      <section class="section-card">
        <div class="section-head">
          <div>
            <h2>Main Ranking</h2>
          </div>
          <div class="toolbar-search-row">
            ${renderSearchBar()}
          </div>
          <div class="toolbar">
            <button id="sortStreamsBtn" class="${state.sortMode === "streams" ? "active" : ""}">Total streams</button>
            <button id="sortDailyBtn"  class="${state.sortMode === "daily"   ? "active" : ""}">Daily streams</button>
            <button id="combineBtn"    class="${state.combineVersions        ? "active" : ""}">Combine</button>
          </div>
        </div>

        <div class="table-wrap ranking-wrap">
          <table class="table ranking-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Rank change</th>
                <th>Song</th>
                <th class="sortable" data-sort="daily">Daily</th>
                <th class="sortable" data-sort="streams">Total</th>
                <th>Streams change</th>
              </tr>
            </thead>
            <tbody id="home-songs-body">
              ${sorted.map(songRow).join("")}
            </tbody>
          </table>
        </div>
      </section>

      ${renderFocusModal()}
    </div>
  `;

  _bindHomeButtons();
}
/* =========================
   ALBUM CARD
========================= */

function albumRow(album){

  const url = `/website/streams/album.html?album=${encodeURIComponent(album.album)}`;

  const daily = album.daily_streams ?? 0;
  const total = album.streams ?? 0;
  const change = album.stream_change ?? null;

  return `
  <tr>

    <td colspan="5" class="row-shell-cell">

      <article class="song-row-card">

        <div class="album-row-grid">

          <div class="col-rank">
            ${album.rank ?? "-"}
          </div>

          <div class="col-song">

            <a class="song-link" href="${url}">

              <img
                class="album-cover-small"
                src="${withCacheBuster(getAlbumCover(album))}"
                alt="${album.album}"
              >

              <div class="row-song-meta">

                <div class="row-song-title">
                  ${album.album}
                </div>

                <div class="row-song-sub">
                  ${album.primary_artist || "Taylor Swift"}
                </div>

              </div>

            </a>

          </div>

          <div class="col-daily">
            ${formatFull(daily)}
          </div>

          <div class="col-total">
            ${formatFull(total)}
          </div>

          <div class="col-stream-change">
            ${renderStreamChange(change)}<span class="sub-delta">${renderPercentChange(album.percent_change ?? null)}</span>
          </div>

        </div>

      </article>

    </td>

  </tr>
  `;
}


/* =========================
   ALBUMS PAGE
========================= */

export function renderAlbums(container) {
  const rowsForDate = enrichSongsForDate(state.selectedDate);

  const validAlbums = state.albums.filter(album => {
    const name = String(album.album || "").trim().toLowerCase();
    const kind = String(album.kind || "").trim().toLowerCase();
    return name !== "misc" && kind !== "misc";
  });

  const albumGroups = new Map();

  for (const album of validAlbums) {
    const key = state.combineVersions
      ? normalizeAlbumName(album.album)
      : String(album.album || "");

    if (!albumGroups.has(key)) {
      albumGroups.set(key, {
        key,
        label: album.album,
        representative: album,
      });
    } else {
      const existing = albumGroups.get(key);

      const existingName = String(existing.representative.album || "");
      const currentName = String(album.album || "");

      if (
        /\(taylor'?s version\)/i.test(existingName) &&
        !/\(taylor'?s version\)/i.test(currentName)
      ) {
        existing.label = album.album;
        existing.representative = album;
      }
    }
  }

  const albums = [...albumGroups.values()].map(group => {
    const albumSongs = rowsForDate.filter(song => {
      const songAlbum = String(song.primary_album || "");
      return state.combineVersions
        ? normalizeAlbumName(songAlbum) === group.key
        : songAlbum === group.label;
    });

    const totalStreams = albumSongs.reduce((sum, song) => sum + (song.streams || 0), 0);
    const totalChange = albumSongs.reduce((sum, song) => sum + (song.total_change || 0), 0);
    const prevStreams = totalStreams - totalChange;
    return {
      ...group.representative,
      album: group.label,
      daily_streams: albumSongs.reduce((sum, song) => sum + (song.daily_streams || 0), 0),
      streams: totalStreams,
      stream_change: totalChange,
      percent_change: prevStreams > 0 ? (totalChange / prevStreams) * 100 : null,
      track_count: albumSongs.length,
    };
  });

  const sorted =
    state.albumSortMode === "daily"
      ? [...albums].sort((a, b) =>
          (b.daily_streams || 0) - (a.daily_streams || 0) ||
          (b.streams || 0) - (a.streams || 0) ||
          a.album.localeCompare(b.album)
        )
      : [...albums].sort((a, b) =>
          (b.streams || 0) - (a.streams || 0) ||
          (b.daily_streams || 0) - (a.daily_streams || 0) ||
          a.album.localeCompare(b.album)
        );

  sorted.forEach((album, i) => {
    album.rank = i + 1;
  });

  const streamsActive = state.albumSortMode === "streams" ? "active" : "";
  const dailyActive = state.albumSortMode === "daily" ? "active" : "";
  const combineActive = state.combineVersions ? "active" : "";

  container.innerHTML = `
    ${renderTopbar()}

    <section class="section-card">
      <div class="section-head">
        <div>
          <h2>Albums</h2>
          <p>${sorted.length} album${sorted.length > 1 ? "s" : ""}</p>
        </div>

        <div class="toolbar">
          <button id="sortAlbumStreamsBtn" class="${streamsActive}">
            Total streams
          </button>

          <button id="sortAlbumDailyBtn" class="${dailyActive}">
            Daily streams
          </button>

          <button id="albumsCombineBtn" class="${combineActive}">
            Combine
          </button>
        </div>
      </div>

      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>#</th>
              <th>Album</th>
              <th>Daily</th>
              <th>Total</th>
              <th>Change</th>
            </tr>
          </thead>
          <tbody>
            ${sorted.map(albumRow).join("")}
          </tbody>
        </table>
      </div>
    </section>
  `;

  const sortAlbumStreamsBtn = document.getElementById("sortAlbumStreamsBtn");
  if (sortAlbumStreamsBtn) {
    sortAlbumStreamsBtn.onclick = () => {
      state.albumSortMode = "streams"; localStorage.setItem("site-album-sort-mode", "streams");
      window.dispatchEvent(new Event("site:render"));
    };
  }

  const sortAlbumDailyBtn = document.getElementById("sortAlbumDailyBtn");
  if (sortAlbumDailyBtn) {
    sortAlbumDailyBtn.onclick = () => {
      state.albumSortMode = "daily"; localStorage.setItem("site-album-sort-mode", "daily");
      window.dispatchEvent(new Event("site:render"));
    };
  }

  const albumsCombineBtn = document.getElementById("albumsCombineBtn");
  if (albumsCombineBtn) {
    albumsCombineBtn.onclick = () => {
      state.combineVersions = !state.combineVersions; localStorage.setItem("site-combine-versions", String(state.combineVersions));
      window.dispatchEvent(new Event("site:render"));
    };
  }
}


/* =========================
   ALBUM DETAIL PAGE
========================= */

export function renderAlbumPage(container) {
  const albumName = getQueryParam("album");
  const album = state.albums.find(a => a.album === albumName);

  if (!album) {
    container.innerHTML = `
      ${renderTopbar()}
      <div class="section-card empty">Album not found</div>
    `;
    return;
  }

  const rowsForDate = enrichSongsForDate(state.selectedDate);

  let albumSongs = rowsForDate.filter(song =>
    (song.appearances || []).some(app => app.album === albumName)
  );

  // Combine avant le groupement pour fusionner les versions qui sont dans des sections différentes
  if (state.combineVersions) albumSongs = combineSongVersions(albumSongs);

  // Pour chaque chanson (potentiellement combinée), choisir la section avec le display_order le plus bas
  const groups = new Map();
  for (const song of albumSongs) {
    const allAppearances = (song.appearances || []).filter(app => app.album === albumName);
    const appearance = allAppearances.sort((a, b) => (a.display_order ?? 9999) - (b.display_order ?? 9999))[0] || null;
    const sectionName = appearance?.display_section || "Other";
    const songOrder = appearance?.display_order ?? 9999;
    if (!groups.has(sectionName)) {
      groups.set(sectionName, { name: sectionName, firstSongOrder: songOrder, songs: [] });
    }
    const group = groups.get(sectionName);
    group.firstSongOrder = Math.min(group.firstSongOrder, songOrder);
    group.songs.push(song);
  }

  let blocks = [...groups.values()]
    .sort((a, b) => {
      const pa = getAlbumSectionPriority(a.name);
      const pb = getAlbumSectionPriority(b.name);
      if (pa !== pb) return pa - pb;
      return a.firstSongOrder - b.firstSongOrder || a.name.localeCompare(b.name);
    })
    .map(block => {
      let songs = [...block.songs];
      songs.sort((a, b) =>
        state.albumSortMode === "daily"
          ? ((b.daily_streams || 0) - (a.daily_streams || 0)) || ((b.streams || 0) - (a.streams || 0)) || a.title.localeCompare(b.title)
          : ((b.streams || 0) - (a.streams || 0)) || ((b.daily_streams || 0) - (a.daily_streams || 0)) || a.title.localeCompare(b.title)
      );
      return { ...block, songs };
    });

  const totalStreams = blocks.reduce((s, b) => s + b.songs.reduce((ss, song) => ss + (song.streams || 0), 0), 0);
  const totalDaily  = blocks.reduce((s, b) => s + b.songs.reduce((ss, song) => ss + (song.daily_streams || 0), 0), 0);
  const totalPrevDaily = blocks.reduce((s, b) => s + b.songs.reduce((ss, song) => ss + (song.previous_daily_streams || 0), 0), 0);
  const totalPct = totalPrevDaily ? ((totalDaily - totalPrevDaily) / totalPrevDaily * 100) : null;

  // Header image — looks for data/headers/{albumName}.png (fallback .jpg)
  const coverUrl  = withCacheBuster(getAlbumCover(album));
  const _hdrBase  = `data/headers/${albumName.toLowerCase().replace(/ /g, "%20")}`;
  const hdrStyle  = `background-image:linear-gradient(rgba(0,0,0,.52),rgba(0,0,0,.52)),url('${_hdrBase}.png'),url('${_hdrBase}.jpg');background-size:cover;background-position:center top;background-color:#0d1117;`;

  // Date label
  const dateLabel = state.selectedDate
    ? new Date(state.selectedDate + "T12:00:00").toLocaleDateString("en-US", { year:"numeric", month:"long", day:"numeric" })
    : "";

  function pctHtml(pct) {
    if (pct === null || pct === undefined) return '<span class="alb-neutral">—</span>';
    const cls = pct >= 0 ? "alb-pos" : "alb-neg";
    return `<span class="${cls}">${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%</span>`;
  }

  function blocksHtml() {
    return blocks.map(block => {
      const secStreams    = block.songs.reduce((s, sg) => s + (sg.streams || 0), 0);
      const secDaily     = block.songs.reduce((s, sg) => s + (sg.daily_streams || 0), 0);
      const secPrevDaily = block.songs.reduce((s, sg) => s + (sg.previous_daily_streams || 0), 0);
      const secPct       = secPrevDaily ? ((secDaily - secPrevDaily) / secPrevDaily * 100) : null;
      const secDailySign = secDaily >= 0 ? "+" : "";

      const rows = block.songs.map((song, i) => {
        const pct    = song.percent_change ?? null;
        const art    = song.image_url
          ? `<img class="alb-art" src="${withCacheBuster(song.image_url)}" loading="lazy" alt="">`
          : `<div class="alb-art-ph"></div>`;
        const sub    = state.combineVersions && (song.combined_versions_count || 1) > 1
          ? `${song.combined_versions_count} versions`
          : (song.version_tag || "");
        const rowCls = i === 0 ? "alb-row alb-row-gold" : (i % 2 !== 0 ? "alb-row alb-row-odd" : "alb-row");
        return `
        <div class="${rowCls}">
          <div class="alb-col-num">${i + 1}</div>
          <div class="alb-col-song">
            ${art}
            <div class="alb-song-text">
              <div class="alb-song-title">${song.title_clean || song.title}</div>
              ${sub ? `<div class="alb-song-sub">${sub}</div>` : ""}
            </div>
          </div>
          <div class="alb-col-num alb-right">${formatFull(song.streams)}</div>
          <div class="alb-col-num alb-right">${formatFull(song.daily_streams)}</div>
          <div class="alb-col-num alb-right">${pctHtml(pct)}</div>
        </div>`;
      }).join("");

      return `
      ${rows}
      <div class="alb-section-total">
        <span class="alb-total-label">${block.name} — Total</span>
        <span class="alb-total-streams">${formatFull(secStreams)}</span>
        <span class="alb-total-daily">${secDailySign}${formatFull(secDaily)}</span>
        <span>${pctHtml(secPct)}</span>
      </div>`;
    }).join("");
  }

  const totalSongCount = blocks.reduce((s, b) => s + b.songs.length, 0);
  const compactCls = totalSongCount > 16 ? " alb-compact" : "";

  container.innerHTML = `
    ${renderTopbar()}
    <div class="alb-wrap${compactCls}">

      <div class="alb-hdr" style="${hdrStyle}">
        <img class="alb-cover" src="${coverUrl}" alt="${albumName}">
        <div class="alb-hdr-info">
          <div class="alb-title">${albumName}</div>
          <div class="alb-hdr-sub">Taylor Swift &middot; ${dateLabel}</div>
        </div>
        <div class="alb-toolbar">
          <button id="albumSortStreamsBtn" class="${state.albumSortMode === "streams" ? "active" : ""}">Total</button>
          <button id="albumSortDailyBtn"  class="${state.albumSortMode === "daily"   ? "active" : ""}">Daily</button>
          <button id="albumCombineBtn"    class="${state.combineVersions             ? "active" : ""}">Combine</button>
          <button id="albumDownloadBtn" class="alb-dl-btn" title="Download image">⬇ Image</button>
        </div>
      </div>

      <div class="alb-col-heads">
        <span>#</span>
        <span>Song</span>
        <span class="alb-right">Streams</span>
        <span class="alb-right">Daily</span>
        <span class="alb-right">Change</span>
      </div>

      ${blocksHtml()}

      <div class="alb-grand-total">
        <span class="alb-total-label">Album Total</span>
        <span class="alb-total-streams">${formatFull(totalStreams)}</span>
        <span class="alb-total-daily">${totalDaily >= 0 ? "+" : ""}${formatFull(totalDaily)}</span>
        <span>${pctHtml(totalPct)}</span>
      </div>

    </div>
  `;

  document.getElementById("albumSortStreamsBtn")?.addEventListener("click", () => { state.albumSortMode = "streams"; localStorage.setItem("site-album-sort-mode", "streams"); window.dispatchEvent(new Event("site:render")); });
  document.getElementById("albumSortDailyBtn") ?.addEventListener("click", () => { state.albumSortMode = "daily";   localStorage.setItem("site-album-sort-mode", "daily");   window.dispatchEvent(new Event("site:render")); });
  document.getElementById("albumCombineBtn")   ?.addEventListener("click", () => { state.combineVersions = !state.combineVersions; localStorage.setItem("site-combine-versions", String(state.combineVersions)); window.dispatchEvent(new Event("site:render")); });

  document.getElementById("albumDownloadBtn")?.addEventListener("click", async () => {
    const btn = document.getElementById("albumDownloadBtn");
    if (btn) { btn.textContent = "⏳ …"; btn.disabled = true; }
    try {
      const _EXCL_KW = ["extra", "remix", "track by track", "music video", "extended", "live"];
      const imgBlocks = blocks.filter(b => !_EXCL_KW.some(kw => b.name.toLowerCase().includes(kw)));
      const imgTotal  = imgBlocks.reduce((s, b) => s + b.songs.reduce((ss, sg) => ss + (sg.streams || 0), 0), 0);
      const imgDaily  = imgBlocks.reduce((s, b) => s + b.songs.reduce((ss, sg) => ss + (sg.daily_streams || 0), 0), 0);
      await downloadAlbumImage(albumName, imgBlocks, imgTotal, imgDaily, dateLabel, coverUrl);
    } finally {
      if (btn) { btn.textContent = "⬇ Image"; btn.disabled = false; }
    }
  });
}


/* =========================
   SONG CHART
========================= */

function _smoothPath(pts) {
  if (pts.length < 2) return "";
  let d = `M ${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
  for (let i = 1; i < pts.length; i++) {
    const prev = pts[i - 1];
    const curr = pts[i];
    const cpX = ((prev[0] + curr[0]) / 2).toFixed(1);
    d += ` C ${cpX},${prev[1].toFixed(1)} ${cpX},${curr[1].toFixed(1)} ${curr[0].toFixed(1)},${curr[1].toFixed(1)}`;
  }
  return d;
}

function _buildSongSvg(data, mode) {
  const W = 700, H = 190;
  const pad = { top: 14, right: 16, bottom: 36, left: 72 };
  const iW = W - pad.left - pad.right;
  const iH = H - pad.top - pad.bottom;

  const vals = data.map(d => d.value);
  const maxV = Math.max(...vals, 1);

  const xS = i => pad.left + (i / Math.max(data.length - 1, 1)) * iW;
  const yS = v => pad.top + iH - (v / maxV) * iH;

  const pts = data.map((d, i) => [xS(i), yS(d.value)]);
  const lineD = _smoothPath(pts);
  const areaD = `M ${pad.left.toFixed(1)},${(pad.top + iH).toFixed(1)} ` +
    pts.map(p => `L ${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ") +
    ` L ${(pad.left + iW).toFixed(1)},${(pad.top + iH).toFixed(1)} Z`;

  const step = Math.max(1, Math.floor(data.length / 7));
  const xLbls = data.map((d, i) => {
    if (i % step !== 0 && i !== data.length - 1) return "";
    const label = d.date.slice(5).replace("-", "/");
    return `<text x="${xS(i).toFixed(1)}" y="${H - 8}" text-anchor="middle" class="chart-axis-lbl">${label}</text>`;
  }).join("");

  const ySteps = [0, 0.25, 0.5, 0.75, 1];
  const yLbls = ySteps.map(t => {
    const v = t * maxV;
    const y = yS(v).toFixed(1);
    return `
      <line x1="${pad.left}" y1="${y}" x2="${W - pad.right}" y2="${y}" class="chart-grid-line"/>
      <text x="${pad.left - 6}" y="${(+y + 4).toFixed(1)}" text-anchor="end" class="chart-axis-lbl">${formatFull(Math.round(v))}</text>`;
  }).join("");

  const dots = data.map((d, i) => {
    const dateLabel = d.date.slice(5).replace("-", "/");
    return `<circle cx="${xS(i).toFixed(1)}" cy="${yS(d.value).toFixed(1)}" r="4" class="chart-dot"><title>${dateLabel}: ${formatFull(d.value)}</title></circle>`;
  }).join("");

  const uid = `sg_${mode}`;
  return `<svg class="song-chart-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    <defs>
      <linearGradient id="${uid}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#1db954" stop-opacity=".30"/>
        <stop offset="100%" stop-color="#1db954" stop-opacity=".02"/>
      </linearGradient>
    </defs>
    ${yLbls}
    <path d="${areaD}" fill="url(#${uid})"/>
    <path d="${lineD}" fill="none" stroke="#1db954" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>
    ${dots}
    ${xLbls}
  </svg>`;
}

async function _loadSongChart(family) {
  const wrap = document.getElementById("song-chart-wrap");
  if (!wrap) return;

  const trackIds = state.songs
    .filter(s => getCombineKey(s) === family)
    .map(s => s.track_id);
  if (!trackIds.length) return;

  const dates = state.dates.slice(-60);
  await Promise.allSettled(dates.map(d => loadHistory(d)));

  if (!document.getElementById("song-chart-wrap")) return; // navigated away

  const activeMode = document.querySelector(".song-chart-tab.active")?.dataset.mode || "daily";

  function buildData(mode) {
    return dates.map(d => ({
      date: d,
      value: trackIds.reduce((s, id) => {
        const entry = state.history[d]?.[id];
        if (!entry) return s;
        return s + (mode === "total" ? (entry.s ?? entry.streams ?? 0) : (entry.d ?? entry.daily_streams ?? 0));
      }, 0)
    })).filter(p => p.value > 0);
  }

  function renderChart(mode) {
    const data = buildData(mode);
    const inner = document.getElementById("song-chart-inner");
    if (!inner) return;
    if (data.length < 2) {
      inner.innerHTML = '<p class="chart-empty">Not enough data</p>';
      return;
    }
    inner.innerHTML = _buildSongSvg(data, mode);
  }

  const wrap2 = document.getElementById("song-chart-wrap");
  if (!wrap2) return;

  wrap2.innerHTML = `
    <div class="song-chart-tabs">
      <button class="song-chart-tab active" data-mode="daily">Daily streams</button>
      <button class="song-chart-tab" data-mode="total">Total streams</button>
    </div>
    <div id="song-chart-inner"></div>
  `;

  renderChart("daily");

  wrap2.querySelectorAll(".song-chart-tab").forEach(btn => {
    btn.onclick = () => {
      wrap2.querySelectorAll(".song-chart-tab").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      renderChart(btn.dataset.mode);
    };
  });
}


/* =========================
   SONG PAGE
========================= */

export function renderSongPage(container){

  const family = decodeURIComponent(getQueryParam("family")||"");

  const songs =
    state.songs.filter(s=>getCombineKey(s)===family);

  if(!songs.length){
    container.innerHTML = `
      ${renderTopbar()}
      <div class="section-card empty">Song not found</div>
    `;
    return;
  }

  const rows =
    enrichSongsForDate(state.selectedDate)
      .filter(s=>songs.some(x=>x.track_id===s.track_id));

  const totalStreams =
    rows.reduce((s,r)=>s+(r.streams||0),0);

  const totalDaily =
    rows.reduce((s,r)=>s+(r.daily_streams||0),0);

  const totalChange =
    rows.reduce((s,r)=>s+(r.total_change||0),0);

  const prevTotal = totalStreams - totalChange;
  const totalPct = prevTotal > 0 ? (totalChange / prevTotal) * 100 : null;

  const main = rows[0];

  container.innerHTML = `
    ${renderTopbar()}

    <section class="section-card">

      <div class="album-hero">

        <img
          class="album-cover-small"
          src="${withCacheBuster(main.image_url)}"
        >

        <div>

          <h2>${main.title_clean||main.title}</h2>

          <div class="mini-song-sub">
            ${formatArtistAlbum(main)}
          </div>

          <div class="mini-song-sub">
            ${formatFull(totalStreams)} streams
          </div>

        </div>

      </div>

      <div class="stats-grid">

        <div class="stat-card">
          <div class="stat-label">Daily streams</div>
          <div class="stat-value">${formatFull(totalDaily)}</div>
        </div>

        <div class="stat-card">
          <div class="stat-label">Total streams</div>
          <div class="stat-value">${formatFull(totalStreams)}</div>
        </div>

        <div class="stat-card">
          <div class="stat-label">Daily change</div>
          <div class="stat-value">${renderStreamChange(totalChange)}</div>
        </div>

        <div class="stat-card">
          <div class="stat-label">% change</div>
          <div class="stat-value">${renderPercentChange(totalPct)}</div>
        </div>

        <div class="stat-card">
          <div class="stat-label">Versions</div>
          <div class="stat-value">${rows.length}</div>
        </div>

      </div>

      <div class="table-wrap">

        <table class="table">

          <thead>
            <tr>
              <th>Version</th>
              <th>Album</th>
              <th>Daily</th>
              <th>Total</th>
              <th>Change</th>
              <th>%</th>
            </tr>
          </thead>

          <tbody>

            ${
              rows.map(r=>`
              <tr>

                <td class="version-cell">
                  ${r.image_url ? `<img class="version-thumb" src="${withCacheBuster(r.image_url)}" alt="">` : ''}
                  <span>${r.title}</span>
                </td>

                <td>${r.primary_album}</td>

                <td>${formatFull(r.daily_streams)}</td>

                <td>${formatFull(r.streams)}</td>

                <td>${renderStreamChange(r.total_change)}</td>

                <td>${renderPercentChange(r.percent_change)}</td>

              </tr>
              `).join("")
            }

          </tbody>

        </table>

      </div>

    </section>

    <section class="section-card">
      <div class="section-head">
        <div>
          <h2>Streams history</h2>
          <p>Last 60 days</p>
        </div>
      </div>
      <div id="song-chart-wrap" class="song-chart-wrap">
        <p class="chart-loading">Loading chart…</p>
      </div>
    </section>
  `;

  _loadSongChart(family);

}
/* =========================
   MILESTONE PROGRESS BAR
========================= */

function getMilestoneBarClass(percent) {
  if (percent >= 80) return "is-hot";
  if (percent >= 60) return "is-purple";
  if (percent >= 40) return "is-blue";
  return "is-teal";
}

function milestoneProgressBar(item) {
  const p = getMilestonePercent(item);

  return `<span class="milestone-mini-bar-wrap">
    <span class="milestone-mini-bar-track"><span class="milestone-mini-bar-fill" style="width:${p}%"></span></span>
    <span class="milestone-mini-bar-pct">${p.toFixed(1)}%</span>
  </span>`;
}

/* =========================
   MILESTONE ROW
========================= */

function getMilestonePercent(item) {
  const current = Number(item.current_streams ?? item.streams ?? 0);
  const target = Number(item.next_milestone ?? item.progress?.target ?? 0);

  if (!current || !target) return 0;

  return Math.max(0, Math.min(100, (current / target) * 100));
}

function milestoneRow(item) {
  const daysLeft = item.forecast?.days_left ?? null;
  if (!item?.forecast?.expected_date) return "";

  const song = state.songByTrackId?.get(item.track_id) || state.songs.find(s => s.track_id === item.track_id);
  if (!song) return "";

  const currentStreams = item.current_streams ?? song.streams ?? 0;
  const avgDaily =
    item.estimated_base_daily ??
    item.latest_daily_streams ??
    item.daily_streams ??
    0;

  const remaining = item.progress?.remaining ?? 0;

  return `
    <div class="milestone-highlight-item">
      <img
        class="milestone-highlight-cover"
        src="${withCacheBuster(song.image_url)}"
        alt="${song.title}"
      >

      <div class="milestone-highlight-content">
        <div class="milestone-highlight-title">
          ${song.title_clean || song.title}
        </div>

        <div class="milestone-highlight-text">
          ${milestoneProgressBar(item)} ${item.next_milestone_label} milestone expected <strong>${item.forecast.expected_date}</strong>
        </div>

        <div class="milestone-inline-stats">
          <span>Total streams <strong>${formatFull(currentStreams)}</strong></span>
          <span>Avg daily <strong>${formatFull(avgDaily)}</strong></span>
          <span>Remaining <strong>${formatFull(remaining)}</strong></span>
        </div>
      </div>
    </div>
  `;
}

/* =========================
   MILESTONES PAGE
========================= */

export function renderMilestones(container) {
  const rows = (state.expectedMilestones || [])
    .filter(item => item?.forecast?.expected_date)
    .slice()
    .sort((a, b) => {
      const da = new Date(a.forecast.expected_date);
      const db = new Date(b.forecast.expected_date);
      return da - db;
    });

  if (!rows.length) {
    container.innerHTML = `
      ${renderTopbar()}

      <section class="section-card">
        <div class="section-head">
          <h2>Milestones</h2>
        </div>

        <div class="empty">
          No upcoming milestones
        </div>
      </section>
    `;
    return;
  }

  container.innerHTML = `
    ${renderTopbar()}

    <section class="section-card">
      <div class="section-head">
        <div>
          <h2>Upcoming Milestones</h2>
          <p>Sorted by expected date</p>
        </div>
      </div>

      <div class="milestone-highlight-list">
        ${rows.map(milestoneRow).join("")}
      </div>
    </section>
  `;
}


/* =========================
   ADMIN
========================= */

export function renderAdmin(container) {

  if (!state.lastRunState) {
    container.innerHTML = `
      ${renderTopbar()}
      <section class="section-card">
        <div class="section-head"><div><h2>Admin</h2><p>Pipeline monitoring</p></div></div>
        <div class="empty">No run state data yet — run the update pipeline first.</div>
      </section>
    `;
    return;
  }

  const statuses = Object.values(state.lastRunState);
  const counts = { updated: 0, ok: 0, timeout: 0, not_found: 0, pending: 0 };
  statuses.forEach(s => { if (counts[s] !== undefined) counts[s]++; else counts.pending++; });

  const problemTracks = Object.entries(state.lastRunState)
    .filter(([, v]) => v === "timeout" || v === "not_found")
    .map(([id, status]) => ({ id, status, song: state.songByTrackId?.get(id) }));

  const streakEntries = state.notFoundStreak
    ? Object.entries(state.notFoundStreak).map(([id, days]) => ({ id, days, song: state.songByTrackId?.get(id) }))
      .sort((a, b) => b.days - a.days)
    : [];

  const latestDate = state.dates[state.dates.length - 1] || "N/A";

  container.innerHTML = `
    ${renderTopbar()}

    <section class="section-card">
      <div class="section-head"><div><h2>Admin</h2><p>Pipeline monitoring</p></div></div>

      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">Updated</div>
          <div class="stat-value admin-status-updated">${counts.updated}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">OK (no change)</div>
          <div class="stat-value">${counts.ok}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Timeout</div>
          <div class="stat-value admin-status-timeout">${counts.timeout}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Not found</div>
          <div class="stat-value admin-status-not_found">${counts.not_found}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Pending</div>
          <div class="stat-value admin-status-pending">${counts.pending}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Total tracks</div>
          <div class="stat-value">${statuses.length}</div>
        </div>
      </div>
    </section>

    ${problemTracks.length ? `
    <section class="section-card">
      <div class="section-head"><div><h2>Problem tracks</h2><p>Timeout or not found on last run</p></div></div>
      <div class="table-wrap">
        <table class="table">
          <thead><tr><th>Song</th><th>Album</th><th>Track ID</th><th>Status</th></tr></thead>
          <tbody>
            ${problemTracks.map(({ id, status, song }) => `
            <tr>
              <td>${song ? (song.title_clean || song.title) : "Unknown"}</td>
              <td>${song ? (song.primary_album || song.album || "") : ""}</td>
              <td style="font-size:11px;font-family:monospace">${id}</td>
              <td><span class="admin-status-${status}">${status}</span></td>
            </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </section>` : ""}

    ${streakEntries.length ? `
    <section class="section-card">
      <div class="section-head"><div><h2>Not-found streaks</h2><p>Auto-deleted after 7 consecutive days</p></div></div>
      <div class="table-wrap">
        <table class="table">
          <thead><tr><th>Song</th><th>Album</th><th>Days missing</th><th>Days left</th></tr></thead>
          <tbody>
            ${streakEntries.map(({ id, days, song }) => `
            <tr>
              <td>${song ? (song.title_clean || song.title) : id}</td>
              <td>${song ? (song.primary_album || song.album || "") : ""}</td>
              <td class="${days >= 5 ? "admin-streak-danger" : ""}">${days}</td>
              <td class="${(7 - days) <= 2 ? "admin-streak-danger" : ""}">${Math.max(0, 7 - days)}</td>
            </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </section>` : ""}

    <section class="section-card">
      <div class="section-head"><div><h2>Update</h2><p>Last update: ${latestDate} &nbsp;•&nbsp; Next: 14:05 UTC / 15:05 CET (daily)</p></div></div>
      <div style="display:flex;flex-direction:column;gap:12px;padding:0 4px 4px">
        <button id="updateBtn" class="update-btn">Refresh data</button>
        <div class="${state.updateLogClass}">${state.updateLogText || ""}</div>
        <pre class="admin-log-pre" id="adminLog">${state.updateLogText || "No recent log."}</pre>
      </div>
    </section>
  `;
}


/* =========================
   BILLBOARD
========================= */

export function renderBillboard(container) {

  if (!state.billboard) {
    container.innerHTML = `
      ${renderTopbar()}
      <section class="section-card">
        <div class="section-head"><div><h2>Billboard</h2><p>Taylor Swift chart entries</p></div></div>
        <div class="empty">No Billboard data available yet — run scrape_billboard.py first.</div>
      </section>
    `;
    return;
  }

  const tabs = [
    { key: "hot_100",          label: "Hot 100" },
    { key: "billboard_200",    label: "Billboard 200" },
    { key: "ts_chart_history", label: "TS Chart History" },
  ];

  const activeTab = state.billboardTab;
  const rows = state.billboard[activeTab] || [];
  const scrapedAt = state.billboard.scraped_at
    ? state.billboard.scraped_at.replace("T", " ").slice(0, 16)
    : "recently";

  const greatestArtists = state.billboard.greatest_artists;

  container.innerHTML = `
    ${renderTopbar()}

    <section class="section-card">
      <div class="section-head">
        <div>
          <h2>Billboard Charts</h2>
          <p>Taylor Swift entries &nbsp;•&nbsp; Scraped ${scrapedAt}</p>
        </div>
        <div class="toolbar">
          ${tabs.map(t =>
            `<button id="bbTab_${t.key}"
               class="${activeTab === t.key ? "active" : ""}">
               ${t.label}
             </button>`
          ).join("")}
        </div>
      </div>

      ${greatestArtists ? `
      <div class="admin-greatest-badge">
        Greatest of All Time Artists: <strong>#${greatestArtists.rank}</strong>
      </div>` : ""}

      ${rows.length === 0 ? `<div class="empty">No entries for this chart.</div>` : `
      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th style="width:52px">#</th>
              <th>Title</th>
              <th>Artist</th>
              <th style="width:130px">Weeks on Chart</th>
              <th style="width:100px">Peak Rank</th>
              ${activeTab === "ts_chart_history" ? "<th>Chart</th>" : ""}
            </tr>
          </thead>
          <tbody>
            ${rows.map(r => `
            <tr>
              <td>${r.rank ?? "-"}</td>
              <td>${r.title ?? "-"}</td>
              <td>${r.artist ?? "Taylor Swift"}</td>
              <td>${r.weeks_on_chart ?? "-"}</td>
              <td>${r.peak_rank ?? "-"}</td>
              ${activeTab === "ts_chart_history" ? `<td>${r.chart ?? "-"}</td>` : ""}
            </tr>`).join("")}
          </tbody>
        </table>
      </div>`}
    </section>
  `;

  tabs.forEach(t => {
    document.getElementById(`bbTab_${t.key}`)?.addEventListener("click", () => {
      state.billboardTab = t.key;
      window.dispatchEvent(new Event("site:render"));
    });
  });
}
