const DATA_URL    = '../site/data/applemusic.json';
const HISTORY_URL = '../site/data/applemusic_history.json';

let data         = null;
let historyData  = null;
let selectedDate = null; // null = show latest from applemusic.json

/* ── helpers ── */
function fmtChg(current, prev) {
  if (prev == null) return '<span class="chg-new">NEW</span>';
  const d = prev - current;
  if (d > 0) return `<span class="chg-up">▲${d}</span>`;
  if (d < 0) return `<span class="chg-dn">▼${Math.abs(d)}</span>`;
  return '<span class="chg-eq">—</span>';
}

function artImg(entry) {
  if (entry.image_url)
    return `<img class="track-art" src="${entry.image_url}" alt="" loading="lazy">`;
  return `<div class="track-art-ph">🎵</div>`;
}

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
}

function chartDate(iso) {
  if (!iso) return '';
  const d = new Date(iso + 'T00:00:00');
  d.setDate(d.getDate() - 1);
  return d.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
}

function updatedAt(scrapedAt) {
  if (!scrapedAt) return '';
  const d = new Date(scrapedAt);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    + ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
}

/* ── date-aware data accessors ── */
function getGlobalEntries() {
  if (selectedDate && historyData) {
    return { entries: historyData.global[selectedDate] || [], date: selectedDate };
  }
  return { entries: (data.global_chart || {}).entries || [], date: (data.global_chart || {}).date };
}

function getTopSongsEntries() {
  if (selectedDate && historyData) {
    return { entries: historyData.top_songs[selectedDate] || [], date: selectedDate };
  }
  return { entries: (data.ts_top_songs || {}).entries || [], date: (data.ts_top_songs || {}).date };
}

function getCountryData() {
  if (selectedDate && historyData) {
    const countries = historyData.country[selectedDate];
    return countries ? { countries, date: selectedDate } : null;
  }
  return data.country_charts || null;
}

function getGenreData() {
  if (selectedDate && historyData) {
    const byCountry = historyData.genre[selectedDate];
    return byCountry ? { by_country: byCountry, date: selectedDate } : null;
  }
  return data.genre_charts || null;
}

/* ── shared song row renderers ── */
const APPLE_ICON = `<img src="../../icons/apple-music.png" class="chart-logo" alt="Apple Music logo">`;

function renderSongRows(entries) {
  return entries.map((r, i) => `
    <div class="song-row${i % 2 === 1 ? ' odd' : ''}">
      <div class="col-pos">${r.rank}</div>
      <div class="col-chg">${fmtChg(r.rank, r.previous_rank)}</div>
      <div class="col-track">
        ${artImg(r)}
        <div>
          <div class="track-name">${r.song_name}</div>
          <div class="track-artist">Taylor Swift</div>
        </div>
      </div>
    </div>`).join('');
}

/* ── render global ── */
function renderGlobal() {
  const { entries, date } = getGlobalEntries();
  const cDate = chartDate(date);
  const uAt   = !selectedDate ? updatedAt(data.scraped_at) : '';

  if (!entries || !entries.length) {
    return `<div class="global-note"><strong>Blank Space on this date — the charts are in their Fortnight feature era. 🎬</strong></div>`;

  return `
    <div class="global-note">
      Taylor Swift songs in the <strong>Apple Music Global Top 100</strong> · ${cDate}${uAt ? ` · Updated ${uAt}` : ''}
    </div>
    <div class="chart-card">
      <div class="chart-hdr">
        ${APPLE_ICON}
        <div>
          <div class="chart-hdr-title">Apple Music · Global Top 100</div>
          <div class="chart-hdr-sub">${cDate}</div>
        </div>
      </div>
      <div class="col-heads"><span>#</span><span>CHG</span><span>TRACK</span></div>
      ${renderSongRows(entries)}
      <div class="chart-ftr">
        <span class="chart-ftr-handle">@swiftiescharts</span>
        <span class="chart-ftr-meta">${entries.length} song${entries.length !== 1 ? 's' : ''} in chart</span>
      </div>
    </div>`;
}

/* ── render top songs ── */
function renderTopSongs() {
  const { entries, date } = getTopSongsEntries();
  const cDate = chartDate(date);
  const uAt   = !selectedDate ? updatedAt(data.scraped_at) : '';

  if (!entries || !entries.length) {
    return `<div class="empty-msg">No songs for this date — it's the quiet part between albums. 🤫</div>`;

  return `
    <div class="chart-card">
      <div class="chart-hdr">
        ${APPLE_ICON}
        <div>
          <div class="chart-hdr-title">Taylor Swift · Top Songs</div>
          <div class="chart-hdr-sub">${cDate}${uAt ? ` · Updated ${uAt}` : ''}</div>
        </div>
      </div>
      <div class="col-heads"><span>#</span><span>CHG</span><span>TRACK</span></div>
      ${renderSongRows(entries)}
      <div class="chart-ftr">
        <span class="chart-ftr-handle">@swiftiescharts</span>
        <span class="chart-ftr-meta">${entries.length} songs</span>
      </div>
    </div>`;
}

/* ── country + genre combined ── */
const COUNTRY_INFO = {
  us: { flag: '🇺🇸', name: 'United States' },
  fr: { flag: '🇫🇷', name: 'France' },
  gb: { flag: '🇬🇧', name: 'United Kingdom' },
  de: { flag: '🇩🇪', name: 'Germany' },
  au: { flag: '🇦🇺', name: 'Australia' },
};
const COUNTRY_ORDER = ['us', 'fr', 'gb', 'de', 'au'];
const GENRE_ORDER   = ['Pop', 'Country', 'Hip-Hop/Rap', 'Rock', 'Singer/Songwriter'];

let _activeCountry = 'us';
let _activeGenre   = 'Pop';

function renderCountryRows(entries) {
  if (!entries || !entries.length)
    return `<div class="country-empty">No Taylor Swift in this Top 100 — she's taking a exile break from this realm. 🧛‍♀️</div>`;
  return entries.map((r, i) => `
    <div class="country-row${i % 2 === 1 ? ' odd' : ''}">
      <div class="col-pos">${r.rank}</div>
      <div class="col-chg">${fmtChg(r.rank, r.previous_rank)}</div>
      <div class="col-track">
        ${artImg(r)}
        <div>
          <div class="track-name">${r.song_name}</div>
          <div class="track-artist">Taylor Swift</div>
        </div>
      </div>
    </div>`).join('');
}

function renderCountryGenre() {
  const countrySection = getCountryData();
  const genreSection   = getGenreData();

  if (!countrySection && !genreSection) {
    return `<div class="global-note">
      <strong>Blank Space on this date.</strong> The vault's locked — run the country & genre collectors to unlock it! 🔑
    </div>`;
  }

  // Country selector buttons
  const countryBtns = COUNTRY_ORDER.map(code => {
    const info = COUNTRY_INFO[code];
    return `<button class="country-btn${code === _activeCountry ? ' active' : ''}"
      onclick="_activeCountry='${code}';renderPage()">${info.flag} ${info.name}</button>`;
  }).join('');

  // Genre selector for selected country
  const byCountry     = genreSection ? (genreSection.by_country || {}) : {};
  const countryGenres = byCountry[_activeCountry] || {};
  const availableGenres = GENRE_ORDER.filter(g => countryGenres[g] && countryGenres[g].length > 0);
  if (availableGenres.length && !availableGenres.includes(_activeGenre)) _activeGenre = availableGenres[0];

  const genreBtns = availableGenres.length
    ? availableGenres.map(g => `<button class="genre-btn${g === _activeGenre ? ' active' : ''}"
        onclick="_activeGenre='${g}';renderPage()">${g}</button>`).join('')
    : `<span style="font-size:12px;color:var(--muted)">No genre data here — this tab's still in its folklore phase. 🍂</span>`;
  // Top 100 card
  const countryEntries = countrySection ? ((countrySection.countries || {})[_activeCountry] || []) : [];
  const cDate = fmtDate(countrySection ? countrySection.date : null);

  const countryCard = `
    <div class="country-card" style="margin-bottom:20px">
      <div class="country-card-hdr">
        <span class="country-flag">${info.flag}</span>
        <span class="country-name-label">${info.name} · Top 100</span>
        <span class="country-count">${countryEntries.length} TS song${countryEntries.length !== 1 ? 's' : ''}</span>
      </div>
      <div>${renderCountryRows(countryEntries)}</div>
    </div>`;

  // Genre card
  const genreEntries = availableGenres.length ? (countryGenres[_activeGenre] || []) : [];

  const genreCard = availableGenres.length ? `
    <div class="country-card">
      <div class="country-card-hdr">
        <span class="country-flag">${info.flag}</span>
        <span class="country-name-label">${info.name} · ${_activeGenre}</span>
        <span class="country-count">${genreEntries.length} TS song${genreEntries.length !== 1 ? 's' : ''}</span>
      </div>
      <div>${renderCountryRows(genreEntries)}</div>
    </div>` : '';

  return `
    <div class="cg-selectors">
      <div class="cg-selector-row">
        <span class="cg-selector-label">Country</span>
        ${countryBtns}
      </div>
      <div class="cg-selector-row">
        <span class="cg-selector-label">Genre</span>
        ${genreBtns}
      </div>
    </div>
    <div class="global-note" style="margin-bottom:16px">
      Taylor Swift in <strong>Apple Music ${info.name}</strong>${cDate ? ` · ${cDate}` : ''}
    </div>
    ${countryCard}
    ${genreCard}`;
}

/* ── routing ── */
function getTab() {
  const h = location.hash;
  if (h === '#top') return 'top';
  if (h === '#country-genre') return 'country-genre';
  return 'global';
}

function setNavActive(tab) {
  document.getElementById('nl-global').classList.toggle('active', tab === 'global');
  document.getElementById('nl-top').classList.toggle('active', tab === 'top');
  document.getElementById('nl-cg').classList.toggle('active', tab === 'country-genre');
}

function renderPage() {
  if (!data) return;
  const tab = getTab();
  setNavActive(tab);
  const app = document.getElementById('app');
  let content;
  if (tab === 'top') content = renderTopSongs();
  else if (tab === 'country-genre') content = renderCountryGenre();
  else content = renderGlobal();
  app.innerHTML = `
    <div class="tab-bar">
      <button class="tab-btn${tab === 'global' ? ' active' : ''}" onclick="location.hash='#global'">Global Top 100</button>
      <button class="tab-btn${tab === 'top' ? ' active' : ''}" onclick="location.hash='#top'">Top Songs</button>
      <button class="tab-btn${tab === 'country-genre' ? ' active' : ''}" onclick="location.hash='#country-genre'">By Country &amp; Genre</button>
    </div>
    ${content}
  `;
}

/* ── date picker ── */
function setupDatePicker() {
  const picker = document.getElementById('datePicker');
  if (!historyData || !historyData.dates || !historyData.dates.length) {
    picker.closest('.date-picker-wrap').style.display = 'none';
    return;
  }

  const dates  = historyData.dates;
  const latest = dates[dates.length - 1];
  picker.min   = dates[0];
  picker.max   = latest;
  picker.value = latest;
  // Latest date = use live applemusic.json data
  selectedDate = null;

  const dateSet = new Set(dates);

  picker.addEventListener('change', () => {
    const val = picker.value;
    if (!val) return;
    let chosen = val;
    if (!dateSet.has(val)) {
      // Snap to nearest available date
      chosen = dates.reduce((a, b) =>
        Math.abs(new Date(b) - new Date(val)) < Math.abs(new Date(a) - new Date(val)) ? b : a
      );
      picker.value = chosen;
    }
    selectedDate = chosen === latest ? null : chosen;
    renderPage();
  });
}

/* ── init ── */
window.addEventListener('hashchange', renderPage);

Promise.all([
  fetch(DATA_URL).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); }),
  fetch(HISTORY_URL).then(r => r.ok ? r.json() : null).catch(() => null),
]).then(([d, h]) => {
  data        = d;
  historyData = h;
  renderPage();
  setupDatePicker();
}).catch(() => {
  document.getElementById('app').innerHTML =
    '<div class="empty-msg">Apple Music hit a dead end — vault\'s sealed. 🚪<br>Run the collectors first, then we\'ll all too well reload this!</div>';
