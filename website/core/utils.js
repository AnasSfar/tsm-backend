import { state } from "./state.js";

/* =========================
   GENERIC HELPERS
========================= */

export function normalizeAlbumName(name) {
  if (!name) return "";

  return name
    .toLowerCase()
    .replace(/\(taylor'?s version\)/gi, "")
    .replace(/[^\w\s]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function getAlbumSectionPriority(sectionName) {
  if (!sectionName) return 50;

  const name = sectionName.toLowerCase();

  if (name.includes("standard")) return 0;
  if (name.includes("deluxe")) return 10;
  if (name.includes("edition")) return 20;
  if (name.includes("bonus")) return 80;
  if (name.includes("extra")) return 90;
  if (name.includes("vault")) return 70;

  return 40;
}

export function getAlbumCover(album) {
  if (!album) return "";

  const targetTitle = String(album.album || "").trim().toLowerCase();
  if (!targetTitle) return album.image_url || "";

  for (const value of Object.values(state.albumCovers || {})) {
    if (!value) continue;

    const title = String(value.title || "").trim().toLowerCase();
    if (title === targetTitle) {
      return value.cover_url || album.image_url || "";
    }
  }

  return album.image_url || "";
}

export function renderFocusModal() {
  return "";
}

function buildFetchCandidates(url) {
  const candidates = [];
  const seen = new Set();

  const add = (value) => {
    if (!value || seen.has(value)) return;
    seen.add(value);
    candidates.push(value);
  };

  add(url);

  if (url.startsWith("/website/site/")) {
    add(url.replace("/website/site/", "/site/"));
  }

  if (url.startsWith("/website/")) {
    add(url.replace("/website/", "/"));
  }

  if (url.startsWith("/site/")) {
    add(url.replace("/site/", "/website/site/"));
  }

  if (url.startsWith("/db/")) {
    add(url.replace("/db/", "/website/db/"));
  }

  if (url.startsWith("/website/db/")) {
    add(url.replace("/website/db/", "/db/"));
  }

  const firstSegment = String(window.location.pathname || "")
    .split("/")
    .filter(Boolean)[0];

  if (firstSegment) {
    const prefixed = `/${firstSegment}${url.startsWith("/") ? "" : "/"}${url}`;
    add(prefixed);

    if (url.startsWith("/website/")) {
      add(prefixed.replace(`/${firstSegment}/website/`, `/${firstSegment}/`));
    }
  }

  return candidates;
}

export async function fetchJSON(url) {
  const tried = [];
  const ts = Date.now();

  for (const candidate of buildFetchCandidates(url)) {
    const withTs = `${candidate}${candidate.includes("?") ? "&" : "?"}ts=${ts}`;
    tried.push(candidate);

    try {
      const r = await fetch(withTs);
      if (!r.ok) continue;
      return await r.json();
    } catch {
      // Continue trying fallback paths.
    }
  }

  throw new Error(`Failed to fetch ${url}. Tried: ${tried.join(", ")}`);
}

export const normalize = v =>
  String(v || "")
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .trim();


export function formatFull(v) {
  if (v === null || v === undefined) return "N/A";
  return v.toLocaleString("en-US");
}

export function formatSigned(v) {
  if (v === null || v === undefined) return "N/A";
  return v > 0 ? `+${formatFull(v)}` : formatFull(v);
}

export function formatPercent(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "N/A";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}


export function withCacheBuster(url) {
  if (!url || typeof url !== "string") return "";

  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}v=${Date.now()}`;
}

export function persistSelectedDate() {
  if (state.selectedDate) {
    localStorage.setItem("site-selected-date", state.selectedDate);
  }
}


export function getQueryParam(name) {
  return new URL(window.location.href).searchParams.get(name);
}


/* =========================
   DATE HELPERS
========================= */

export function getPreviousDate(date) {
  const i = state.dates.indexOf(date);
  return i > 0 ? state.dates[i - 1] : null;
}

export function getNextDate(date) {
  const i = state.dates.indexOf(date);
  return i >= 0 && i < state.dates.length - 1
    ? state.dates[i + 1]
    : null;
}


/* =========================
   HISTORY ACCESS
========================= */

export function getDayData(trackId, date){
  return state.history?.[date]?.[trackId] || null;
}


/* =========================
   ARTIST FORMATTING
========================= */

export function formatArtists(song) {
  if (Array.isArray(song.artists) && song.artists.length) {
    return song.artists.join(", ");
  }

  if (song.primary_artist) {
    return song.primary_artist;
  }

  return "Unknown artist";
}

export function formatArtistAlbum(song) {
  return `${formatArtists(song)} / ${song.primary_album || "Unknown album"}`;
}

export function sortDisplayBlocks(blocks) {
  function _normalize(value) {
    return String(value || "")
      .trim()
      .toLowerCase();
  }

  function getRank(block) {
    const key = _normalize(block.key);
    const name = _normalize(block.name);

    if (
      key.includes("standard") ||
      name.includes("standard")
    ) return 0;

    if (
      key.includes("extra") ||
      name.includes("extra")
    ) return 99;

    return 10;
  }

  return [...blocks].sort((a, b) => {
    const rankDiff = getRank(a) - getRank(b);
    if (rankDiff !== 0) return rankDiff;

    const aLabel = _normalize(a.name || a.key);
    const bLabel = _normalize(b.name || b.key);

    return aLabel.localeCompare(bLabel);
  });
}

/* =========================
   SEARCH NORMALIZATION
========================= */

export function filterSongsByQuery(rows) {
  const raw = state.searchQuery.trim();
  if (!raw) return rows;

  // Syntaxe spéciale : album:folklore → filtre uniquement sur primary_album
  const albumMatch = raw.match(/^album:(.+)/i);
  if (albumMatch) {
    const albumQ = normalize(albumMatch[1]);
    return rows.filter(song => normalize(song.primary_album || "").includes(albumQ));
  }

  const q = normalize(raw);
  return rows.filter(song => {
    // Use pre-computed search text when available (set at load time)
    const text = song._searchText || normalize([
      song.title, song.title_clean, song.primary_album, song.primary_artist,
      formatArtists(song), song.version_tag, song.edition, song.type
    ].join(" "));
    return text.includes(q);
  });
}
