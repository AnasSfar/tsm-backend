import { state } from "./state.js";
import {
  fetchJSON, normalize, persistSelectedDate,
  getPreviousDate, getNextDate
} from "./utils.js";
import { loadHistory, getCombineKey } from "./data.js";
import { applyTheme, bindThemeSwitcher } from "./theme.js";
import { renderAmbientEffects, bindCursorGlow } from "./components.js";
import {
  renderHome, renderAlbums, renderAlbumPage,
  renderSongPage, renderMilestones, renderAdmin, renderBillboard,
  updateHomeTable
} from "./pages.js";

/* =========================
   DATE CONTROLS
========================= */

function bindDateControls() {
  const input = document.getElementById("dateInput");
  const prev = document.getElementById("prevDayBtn");
  const next = document.getElementById("nextDayBtn");

  if (input) {
    input.onchange = async () => {
      state.selectedDate = input.value;
      persistSelectedDate();
      await loadHistory(state.selectedDate);
      renderPage();
    };
  }

  if (prev) {
    prev.onclick = async () => {
      const d = getPreviousDate(state.selectedDate);
      if (!d) return;
      state.selectedDate = d;
      persistSelectedDate();
      await loadHistory(state.selectedDate);
      renderPage();
    };
  }

  if (next) {
    next.onclick = async () => {
      const d = getNextDate(state.selectedDate);
      if (!d) return;
      state.selectedDate = d;
      persistSelectedDate();
      await loadHistory(state.selectedDate);
      renderPage();
    };
  }
}

/* =========================
   SEARCH BINDING
========================= */

function bindSearch(){
  const input = document.getElementById("searchInput");
  if(!input) return;

  input.oninput = ()=>{
    state.searchQuery = input.value.trim();
    updateHomeTable();
  };
}

/* =========================
   UPDATE BUTTON
========================= */

function bindUpdateButton(){
  const btn = document.getElementById("updateBtn");
  if(!btn) return;

  btn.onclick = async ()=>{
    btn.classList.add("loading");

    try{
      const r = await fetch("/api/update",{ method:"POST" });
      const data = await r.json();

      state.updateLogText = data.message || "Updated";
      state.updateLogClass = "update-log success";

      await loadData();
      renderPage();
    }
    catch(e){
      state.updateLogText =
        "It's a cruel summer without fresh data. Spotify usually updates around 15:00 Paris time — check back then! 🌞";

      state.updateLogClass = "update-log error";
      renderPage();
    }

    btn.classList.remove("loading");
  };
}

/* =========================
   PAGE ROUTER
========================= */

async function renderPage() {
  if (state.selectedDate) {
    const prevDate = getPreviousDate(state.selectedDate);
    await Promise.all([
      loadHistory(state.selectedDate),
      prevDate ? loadHistory(prevDate) : Promise.resolve(),
    ]);
  }

  if (state.page === "albums" || state.page === "album") {
    const loads = [];
    if (!state.albums.length) {
      loads.push(
        fetchJSON("/website/site/data/albums.json").catch(() => null).then(d => {
          state.albums = d?.albums || [];
        })
      );
    }
    if (!state.appearancesLoaded) {
      loads.push(
        fetchJSON("/website/site/data/songs-appearances.json").catch(() => null).then(d => {
          if (d?.appearances) {
            const map = d.appearances;
            state.songs.forEach(s => { s.appearances = map[s.track_id] || []; });
            state.appearancesLoaded = true;
          }
        })
      );
    }
    if (loads.length) await Promise.all(loads);
  }

  if (state.page === "milestones") {
    if (!state.expectedMilestones.length) {
      const d = await fetchJSON("/website/site/data/expected_milestones.json").catch(() => null);
      state.expectedMilestones = d?.forecasts || [];
    }
  }

  if (state.page === "billboard") {
    if (!state.billboard) {
      state.billboard = await fetchJSON("/website/site/data/billboard.json").catch(() => null);
    }
  }

  if (state.page === "song") {
    if (!state.chartsWorldwide) {
      state.chartsWorldwide = await fetchJSON(
        "/website/site/data/charts_worldwide.json"
      ).catch(() => null);
    }
  }

  const container = document.getElementById("app") || document.body;

  await applyTheme(state.themeMode);

  if (state.page === "home") {
    renderHome(container);
  } else if (state.page === "albums") {
    renderAlbums(container);
  } else if (state.page === "album") {
    renderAlbumPage(container);
  } else if (state.page === "song") {
    renderSongPage(container);
  } else if (state.page === "milestones") {
    renderMilestones(container);
  } else if (state.page === "admin") {
    renderAdmin(container);
  } else if (state.page === "billboard") {
    renderBillboard(container);
  }

  bindThemeSwitcher(renderPage);
  bindCursorGlow();
  bindDateControls();
  bindSearch();
  bindUpdateButton();
}

window.addEventListener("site:render", () => renderPage());

/* =========================
   DATA LOADING
========================= */

async function loadData() {
  const [
    songsData,
    artistData,
    albumCoversData,
    lastRunStateData,
    notFoundStreakData,
  ] = await Promise.all([
    fetchJSON("/website/site/data/songs.json"),
    fetchJSON("/website/site/data/artist.json").catch(() => null),
    fetchJSON("/db/discography/covers.json").catch(() => ({})),
    fetchJSON("/website/site/data/last_run_state.json").catch(() => null),
    fetchJSON("/website/site/data/not_found_streak.json").catch(() => null),
  ]);

  state.songs = songsData.songs || [];

  state.songs.forEach(s => {
    s._combineKey = getCombineKey(s);
    s._searchText = normalize([
      s.title, s.title_clean, s.primary_album, s.primary_artist,
      Array.isArray(s.artists) ? s.artists.join(" ") : (s.primary_artist || ""),
      s.version_tag, s.edition, s.type
    ].join(" "));
  });

  state.songByTrackId = new Map(state.songs.map(s => [s.track_id, s]));

  state.albums = [];
  state.expectedMilestones = [];
  state.billboard = null;
  state.chartsWorldwide = null;

  state.artist = artistData || null;
  state.albumCovers = albumCoversData || {};
  state.lastRunState   = lastRunStateData   || null;
  state.notFoundStreak = notFoundStreakData || null;

  state._dataGen = (state._dataGen || 0) + 1;
  state.history = {};

  let allDates = songsData.summary?.dates || songsData.dates || [];

  if (!allDates.length) {
    const r = await fetchJSON("/website/site/history/index.json");
    allDates = r.dates || [];
  }

  state.dates = allDates;

  const storedDate = localStorage.getItem("site-selected-date");
  const latestDate = state.dates[state.dates.length - 1] || null;

  if (storedDate && storedDate === latestDate) {
    state.selectedDate = storedDate;
  } else {
    state.selectedDate = latestDate;
    persistSelectedDate();
  }

  if (state.selectedDate) {
    const prevDate = getPreviousDate(state.selectedDate);
    await Promise.all([
      loadHistory(state.selectedDate),
      prevDate ? loadHistory(prevDate) : Promise.resolve(),
    ]);
  }
}

/* =========================
   INIT
========================= */

async function init(){
  try{
    await loadData();

    document.body.insertAdjacentHTML(
      "beforeend",
      renderAmbientEffects()
    );

    renderPage();
  }
  catch(e){
    console.error(e);

    document.body.innerHTML = `
      <div style="padding:40px;font-family:sans-serif;">
        Got blank space, baby. The vault door's locked right now. Retry in a moment! 💫
      </div>
    `;
  }
}

document.addEventListener("DOMContentLoaded", init);