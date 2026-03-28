/* =========================
   STATE
========================= */

export const state = {
  songs: [],
  albums: [],
  history: {},
  dates: [],
  selectedDate: localStorage.getItem("site-selected-date") || null,

  sortMode: localStorage.getItem("site-sort-mode") || "daily",
  albumSortMode: localStorage.getItem("site-album-sort-mode") || "daily",
  albumsPageSortMode: localStorage.getItem("site-album-sort-mode") || "daily",

  page: document.body.dataset.page || "home",
  combineVersions: localStorage.getItem("site-combine-versions") === "true",

  updateLogText: "",
  updateLogClass: "update-log",

  artist: null,
  themeMode: localStorage.getItem("site-theme-mode") || "light",

  searchQuery: "",
  focusFamily: null,
  albumCovers: {},

  expectedMilestones: [],

  songByTrackId: new Map(),
  _dataGen: 0,

  lastRunState: null,
  notFoundStreak: null,

  billboard: null,
  billboardTab: "hot_100",

  appearancesLoaded: false,
};
