/* ════════════════════════════════════════
   CONFIG
════════════════════════════════════════ */
const REGIONS = {
  global: {
    csvUrl:    () => '../../db/charts_history_global.csv',
    dayUrl:    d  => { const [y,m]=d.split('-'); return `../../collectors/spotify/charts/global/history/${y}/${m}/${d}/ts_chart_${d}.json`; },
    title:      'Global Spotify',
    coversUrl:  '../spotify-charts/track_covers.json',
  },
  fr: {
    csvUrl:    () => '../../db/charts_history_fr.csv',
    dayUrl:    d  => { const [y,m]=d.split('-'); return `../../collectors/spotify/charts/fr/history/${y}/${m}/${d}/ts_chart_${d}.json`; },
    title:      'France Spotify',
    coversUrl:  '../spotify-charts/track_covers.json',
  },
  us: {
    csvUrl:    () => '../../db/charts_history_us.csv',
    dayUrl:    d  => { const [y,m]=d.split('-'); return `../../collectors/spotify/charts/us/history/${y}/${m}/${d}/ts_chart_${d}.json`; },
    title:      'US Spotify',
    coversUrl:  '../spotify-charts/track_covers.json',
  },
  uk: {
    csvUrl:    () => '../../db/charts_history_uk.csv',
    dayUrl:    d  => { const [y,m]=d.split('-'); return `../../collectors/spotify/charts/uk/history/${y}/${m}/${d}/ts_chart_${d}.json`; },
    title:      'UK Spotify',
    coversUrl:  '../spotify-charts/track_covers.json',
  },
};

/* ════════════════════════════════════════
   STATE (per region)
════════════════════════════════════════ */
const S = {
  global: { hist:{}, avail:[], imgCache:{}, dayCache:{}, loaded:false },
  fr:     { hist:{}, avail:[], imgCache:{}, dayCache:{}, loaded:false },
  us:     { hist:{}, avail:[], imgCache:{}, dayCache:{}, loaded:false },
  uk:     { hist:{}, avail:[], imgCache:{}, dayCache:{}, loaded:false },
};
let cjs = [];

/* ════════════════════════════════════════
   UTILS
════════════════════════════════════════ */
const fmtN = n =>
  n == null ? '—' : Math.round(n).toLocaleString('en-US').replace(/,/g, '\u202f');

const fmtPct = p =>
  p == null ? '—' : `${p >= 0 ? '+' : ''}${p.toFixed(1)}%`;

const pCls = p => p == null ? '' : p >= 0 ? 'up' : 'dn';

function chgLabel(rank, prevRank, totalDays) {
  if (prevRank == null) {
    return (totalDays != null && totalDays > 0) ? ['RE', 'chg-re'] : ['NEW', 'chg-new'];
  }
  const d = Math.round(prevRank) - Math.round(rank);
  if (d > 0) return [`▲${d}`, 'chg-up'];
  if (d < 0) return [`▼${Math.abs(d)}`, 'chg-dn'];
  return ['=', 'chg-eq'];
}

function prevStream(rgn, name, date, days) {
  const d = new Date(date + 'T12:00:00Z');
  d.setUTCDate(d.getUTCDate() - days);
  return S[rgn].hist[name]?.[d.toISOString().slice(0,10)]?.streams ?? null;
}

const fmtDate = s => new Date(s + 'T00:00:00').toLocaleDateString('en-US',
  { year: 'numeric', month: 'long', day: 'numeric' });

function killCharts() {
  cjs.forEach(c => { try { c.destroy(); } catch(e) {} });
  cjs = [];
}

function parseHash() {
  const raw = decodeURIComponent(location.hash.slice(1)) || 'global/chart';
  const parts = raw.split('/');
  let rgn = 'global', rest = raw;
  if (parts[0] === 'global' || parts[0] === 'fr' || parts[0] === 'us' || parts[0] === 'uk') {
    rgn  = parts[0];
    rest = parts.slice(1).join('/') || 'chart';
  }
  return { rgn, rest };
}

function navActive(page) {
  document.getElementById('nl-today')  ?.classList.toggle('active', page === 'today');
  document.getElementById('nl-history')?.classList.toggle('active', page === 'history');
}

/* ════════════════════════════════════════
   CSV PARSING
════════════════════════════════════════ */
function parseCSVLine(line) {
  const result = [];
  let cur = '', inQ = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (c === '"') {
      if (inQ && line[i+1] === '"') { cur += '"'; i++; }
      else inQ = !inQ;
    } else if (c === ',' && !inQ) {
      result.push(cur); cur = '';
    } else {
      cur += c;
    }
  }
  result.push(cur);
  return result;
}

function parseCSV(text) {
  const lines = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
  let headers = null;
  const rows = [];
  for (const line of lines) {
    if (!line.trim()) continue;
    const vals = parseCSVLine(line);
    if (!headers) { headers = vals; continue; }
    const row = {};
    headers.forEach((h, i) => row[h] = (vals[i] ?? '').trim());
    rows.push(row);
  }
  return rows;
}

function csvToHist(rows) {
  const hist = {};
  for (const row of rows) {
    const date = row.date;
    const name = row.song_name;
    if (!date || !name) continue;
    const rank = parseInt(row.rank);
    if (!rank) continue;
    if (!hist[name]) hist[name] = {};
    const entry = { rank, streams: parseInt(row.streams) || 0 };
    const pr = parseInt(row.previous_rank); if (pr > 0) entry.previous_rank = pr;
    const pk = parseInt(row.peak_rank);     if (pk > 0) entry.peak_rank = pk;
    const td = parseInt(row.total_days);    if (td > 0) entry.total_days = td;
    hist[name][date] = entry;
  }
  return hist;
}

/* ════════════════════════════════════════
   DATA LOADING
════════════════════════════════════════ */
async function loadHist(rgn) {
  if (S[rgn].loaded) return;
  const cfg = REGIONS[rgn];
  const proms = [fetch(cfg.csvUrl())];
  if (cfg.coversUrl) proms.push(fetch(cfg.coversUrl).catch(() => null));
  else               proms.push(Promise.resolve(null));
  const [rc, rcovers] = await Promise.all(proms);
  if (!rc.ok) throw new Error(`Cannot load CSV (${rgn})`);
  S[rgn].hist = csvToHist(parseCSV(await rc.text()));
  if (rcovers?.ok) Object.assign(S[rgn].imgCache, await rcovers.json());
  const s = new Set();
  for (const dates of Object.values(S[rgn].hist))
    for (const d of Object.keys(dates)) s.add(d);
  S[rgn].avail  = [...s].sort();
  S[rgn].loaded = true;
}

async function loadDay(rgn, date) {
  if (S[rgn].dayCache[date]) return S[rgn].dayCache[date];
  try {
    const r = await fetch(REGIONS[rgn].dayUrl(date));
    if (!r.ok) return null;
    const data = await r.json();
    S[rgn].dayCache[date] = data;
    for (const row of data)
      if (row.image_url && row.track_name && !S[rgn].imgCache[row.track_name])
        S[rgn].imgCache[row.track_name] = row.image_url;
    return data;
  } catch { return null; }
}

/* ════════════════════════════════════════
   COMPUTATIONS
════════════════════════════════════════ */
function songsOn(rgn, date) {
  const out = [];
  for (const [name, dates] of Object.entries(S[rgn].hist)) {
    const e = dates[date];
    if (e) out.push({ name, ...e });
  }
  return out.sort((a, b) => a.rank - b.rank);
}

function outs(rgn, date) {
  const d = new Date(date + 'T00:00:00');
  d.setDate(d.getDate() - 1);
  const yest = d.toISOString().slice(0, 10);
  const today = new Set(songsOn(rgn, date).map(s => s.name));
  return songsOn(rgn, yest).filter(s => !today.has(s.name));
}

function tierDays(rgn, name) {
  const vals = Object.values(S[rgn].hist[name] || {});
  return Object.fromEntries(
    [5,10,15,20,30,50,100,200].map(t => [t, vals.filter(v => v.rank <= t).length])
  );
}

function songStats(rgn, name) {
  const entries = Object.entries(S[rgn].hist[name] || {});
  if (!entries.length) return null;
  let peak = Infinity, best = 0;
  for (const [, v] of entries) {
    if (v.rank < peak) peak = v.rank;
    if ((v.streams || 0) > best) best = v.streams;
  }
  return { peak, best, total: entries.length };
}

function songHist(rgn, name) {
  return Object.entries(S[rgn].hist[name] || {})
    .map(([date, v]) => ({ date, ...v }))
    .sort((a, b) => a.date.localeCompare(b.date));
}

function isActive(rgn, name) {
  const h = songHist(rgn, name);
  if (!h.length) return false;
  return Date.now() - new Date(h[h.length-1].date).getTime() < 10 * 86400_000;
}

/* ════════════════════════════════════════
   BUILD CHART TABLE
════════════════════════════════════════ */
const SPOTIFY_SVG = `<svg width="46" height="46" viewBox="0 0 24 24" fill="white" style="flex-shrink:0;opacity:.95">
  <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/>
</svg>`;

function buildChart(rgn, date, songs, outSongs, extra) {
  const df = fmtDate(date);
  let body = '';

  songs.forEach((s, i) => {
    const ex = extra[s.name] || {};
    const prevRank = ex.previous_rank != null ? ex.previous_rank : s.previous_rank;
    // For old dates without per-day JSON, infer re-entry from ts_history.json
    const exTotalDays = ex.total_days != null ? ex.total_days : (s.total_days ?? null);
    const [ct, cc] = chgLabel(s.rank, prevRank, exTotalDays);
    const ts = s.streams;
    const d1 = prevStream(rgn, s.name, date, 1);
    const d7 = prevStream(rgn, s.name, date, 7);
    const dp = (ts && d1) ? (ts - d1) / d1 * 100 : null;
    const wp = (ts && d7) ? (ts - d7) / d7 * 100 : null;
    const strk = ex.streak    ? `${Math.round(ex.streak)}d`     : '—';
    const tot  = (ex.total_days ?? s.total_days) ? `${Math.round(ex.total_days ?? s.total_days)}d` : '—';
    const pk   = s.peak_rank;
    const img  = S[rgn].imgCache[s.name] || ex.image_url;
    const art  = img
      ? `<img class="track-art" src="${img}" loading="lazy" alt="">`
      : `<div class="track-art-ph">♪</div>`;
    const pkH  = pk
      ? `<span class="peak-pill${pk === 1 ? ' p1' : ''}">#${pk}</span>` : '—';
    const cls  = s.rank === 1 ? 'gold' : (i % 2 !== 0 ? 'odd' : '');

    body += `<a class="song-row ${cls}" href="#${rgn}/song/${encodeURIComponent(s.name)}">
      <div class="col-pos">#${s.rank}</div>
      <div class="col-chg ${cc}">${ct}</div>
      <div class="col-track">${art}<div><div class="track-name">${s.name}</div><div class="track-artist">Taylor Swift</div></div></div>
      <div class="col-num">${fmtN(ts)}</div>
      <div class="col-num ${pCls(dp)}">${fmtPct(dp)}</div>
      <div class="col-num ${pCls(wp)}">${fmtPct(wp)}</div>
      <div class="col-num">${strk}</div>
      <div class="col-num">${tot}</div>
      <div class="col-num">${pkH}</div>
    </a>`;
  });

  if (outSongs.length) {
    body += `<div class="out-sep">↩ Left the chart</div>`;
    outSongs.forEach(s => {
      const img = S[rgn].imgCache[s.name];
      const art = img
        ? `<img class="track-art" src="${img}" loading="lazy" alt="">`
        : `<div class="track-art-ph">♪</div>`;
      body += `<div class="out-row">
        <div><span class="out-badge">OUT</span></div>
        <div class="col-chg" style="font-size:11px;font-weight:600;color:var(--muted)">#${s.rank}</div>
        <div class="col-track">${art}<div><div class="track-name">${s.name}</div><div class="track-artist">Taylor Swift</div></div></div>
      </div>`;
    });
  }

  return `<div class="chart-card">
    <div class="chart-hdr">
      ${SPOTIFY_SVG}
      <div>
        <div class="chart-hdr-title">Taylor Swift · ${REGIONS[rgn].title}</div>
        <div class="chart-hdr-sub">Daily Chart · ${df}</div>
      </div>
    </div>
    <div class="col-heads">
      <span>POS</span><span>CHG</span><span>TRACK</span>
      <span class="r">STREAMS</span><span class="r">D%</span><span class="r">W%</span>
      <span class="r">STREAK</span><span class="r">TOTAL</span><span class="r">PEAK</span>
    </div>
    ${body}
    <div class="chart-ftr">
      <span class="chart-ftr-handle">@swiftiescharts</span>
      <span class="chart-ftr-meta">${df} &middot; ${songs.length} track${songs.length !== 1 ? 's' : ''}</span>
    </div>
  </div>`;
}

/* ════════════════════════════════════════
   ROUTING
════════════════════════════════════════ */
function route() {
  killCharts();
  const hash = decodeURIComponent(location.hash.slice(1)) || '';
  if (hash === 'history') { renderHistoryPage(); return; }
  const { rgn, rest } = parseHash();
  if (rest.startsWith('song/')) renderSong(rgn, rest.slice(5));
  else                          renderToday(rgn);
}

/* ════════════════════════════════════════
   PAGE: TODAY
════════════════════════════════════════ */
async function renderToday(rgn) {
  navActive('today');
  const app = document.getElementById('app');
  app.innerHTML = `<div class="loading"><div class="spinner"></div> Loading…</div>`;

  await loadHist(rgn);
  const latestDate = S[rgn].avail[S[rgn].avail.length - 1];
  const dayData    = await loadDay(rgn, latestDate);
  const extra      = {};
  if (dayData) for (const r of dayData) extra[r.track_name] = r;

  const avail = S[rgn].avail;

  app.innerHTML = `
    <div class="region-toggle-bar">
      <button class="rgn-btn ${rgn==='global'?'active':''}" data-r="global">🌍 Global</button>
      <button class="rgn-btn ${rgn==='fr'    ?'active':''}" data-r="fr">🇫🇷 France</button>
      <button class="rgn-btn ${rgn==='us'    ?'active':''}" data-r="us">🇺🇸 US</button>
      <button class="rgn-btn ${rgn==='uk'    ?'active':''}" data-r="uk">🇬🇧 UK</button>
    </div>
    ${buildChart(rgn, latestDate, songsOn(rgn, latestDate), outs(rgn, latestDate), extra)}
    <div class="calendar-section">
      <div class="cal-label">Browse by date</div>
      <div class="date-row" style="margin-bottom:16px">
        <button class="nav-btn" id="hprev">←</button>
        <input type="date" class="date-input" id="dpick"
          value="${latestDate}" min="${avail[0]}" max="${latestDate}">
        <button class="nav-btn" id="hnext" disabled>→</button>
        <span style="font-size:11px;color:var(--muted);margin-left:8px">
          ${avail.length.toLocaleString()} dates · ${avail[0]} → ${latestDate}
        </span>
      </div>
      <div id="hchart"></div>
    </div>`;

  /* Region toggle */
  document.querySelectorAll('.rgn-btn').forEach(b => {
    b.addEventListener('click', () => { location.hash = `${b.dataset.r}/chart`; });
  });

  /* Calendar navigation */
  let curIdx = avail.length - 1;
  const dpick = document.getElementById('dpick');
  const hprev = document.getElementById('hprev');
  const hnext = document.getElementById('hnext');
  if (curIdx <= 0) hprev.disabled = true;

  async function loadCalDate(d) {
    document.getElementById('hchart').innerHTML =
      `<div class="loading"><div class="spinner"></div></div>`;
    const dd = await loadDay(rgn, d);
    const ex = {};
    if (dd) for (const r of dd) ex[r.track_name] = r;
    document.getElementById('hchart').innerHTML =
      buildChart(rgn, d, songsOn(rgn, d), outs(rgn, d), ex);
  }

  hprev.onclick = async () => {
    if (curIdx <= 0) return;
    curIdx--;
    dpick.value    = avail[curIdx];
    hprev.disabled = curIdx <= 0;
    hnext.disabled = false;
    await loadCalDate(avail[curIdx]);
  };
  hnext.onclick = async () => {
    if (curIdx >= avail.length - 1) return;
    curIdx++;
    dpick.value    = avail[curIdx];
    hnext.disabled = curIdx >= avail.length - 1;
    hprev.disabled = false;
    await loadCalDate(avail[curIdx]);
  };
  dpick.addEventListener('change', async e => {
    const v = e.target.value;
    const nearest = avail.includes(v) ? v
      : avail.reduce((a, b) =>
          Math.abs(new Date(b) - new Date(v)) < Math.abs(new Date(a) - new Date(v)) ? b : a);
    curIdx         = avail.indexOf(nearest);
    dpick.value    = nearest;
    hprev.disabled = curIdx <= 0;
    hnext.disabled = curIdx >= avail.length - 1;
    await loadCalDate(nearest);
  });
}


/* ════════════════════════════════════════
   PAGE: SONG
════════════════════════════════════════ */
async function renderSong(rgn, name) {
  navActive(rgn, '');
  await loadHist(rgn);
  const app = document.getElementById('app');
  app.innerHTML = `<div class="loading"><div class="spinner"></div> Loading…</div>`;

  const h = songHist(rgn, name);
  if (!h.length) {
    app.innerHTML = `<div class="empty">Song not found: <em>${name}</em></div>`;
    return;
  }

  if (!S[rgn].imgCache[name]) await loadDay(rgn, h[h.length - 1].date);

  const st  = songStats(rgn, name);
  const tr  = tierDays(rgn, name);
  const act = isActive(rgn, name);
  const last = h[h.length - 1];
  const img  = S[rgn].imgCache[name];

  const art = img
    ? `<img class="song-art" src="${img}" alt="${name}">`
    : `<div class="song-art-ph">♪</div>`;

  const sub = [
    act && last.rank ? `Currently #${last.rank}` : null,
    `Peak #${st.peak}`,
    `${st.total} days on chart`,
  ].filter(Boolean).join(' · ');

  const tierHtml = [5,10,15,20,30,50,100,200].map(t => `
    <div class="tier-card">
      <div class="tier-lbl">Top ${t}</div>
      <div class="tier-val ${tr[t] > 0 ? 'has' : ''}">${tr[t]}</div>
    </div>`).join('');

  // History table — most recent first, max 150
  const rev = [...h].reverse().slice(0, 150);
  let histHtml = '';
  rev.forEach((entry, i) => {
    const prev   = h[h.length - 2 - i];
    const rd     = prev ? prev.rank - entry.rank : null;
    const rdH    = rd == null ? '—'
      : rd > 0 ? `<span style="color:var(--up)">▲${rd}</span>`
      : rd < 0 ? `<span style="color:var(--dn)">▼${Math.abs(rd)}</span>`
      : `<span style="color:var(--muted)">═</span>`;
    const sp   = prev?.streams;
    const dpct = (entry.streams != null && sp != null) ? entry.streams - sp : null;
    const pk   = entry.peak_rank ? `#${entry.peak_rank}` : '—';
    histHtml += `
    <div class="hist-row ${i === 0 && act ? 'hl' : ''}">
      <span>${entry.date}</span>
      <span style="text-align:center;display:block">#${entry.rank}</span>
      <span class="r">${fmtN(entry.streams)}</span>
      <span class="r ${pCls(dpct)}">${dpct == null ? '—' : (dpct >= 0 ? '+' : '') + fmtN(dpct)}</span>
      <span class="r">${rdH}</span>
      <span style="text-align:center;display:block">${pk}</span>
    </div>`;
  });

  app.innerHTML = `
  <div class="breadcrumb">
    <a href="#${rgn}/chart">Charts</a> <span>›</span> <span>${name}</span>
  </div>

  <div class="song-hdr">
    ${art}
    <div>
      <div class="song-name">${name}</div>
      <div class="song-artist">Taylor Swift</div>
      <div class="song-sub">${sub}</div>
    </div>
  </div>

  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-val gold">#${st.peak}</div>
      <div class="stat-lbl">Peak Rank</div>
    </div>
    <div class="stat-card">
      <div class="stat-val">${st.total}</div>
      <div class="stat-lbl">Days on Chart</div>
    </div>
    <div class="stat-card">
      <div class="stat-val green">${fmtN(st.best)}</div>
      <div class="stat-lbl">Best Streams</div>
    </div>
    <div class="stat-card">
      <div class="stat-val">${act && last.rank ? '#' + last.rank : '—'}</div>
      <div class="stat-lbl">Current Rank</div>
    </div>
  </div>

  <div class="section-hdr">Days in chart by tier</div>
  <div class="tier-grid">${tierHtml}</div>

  <div class="chart-box">
    <div class="chart-box-title">📈 Rank over time <small>(lower = better)</small></div>
    <div class="chart-wrap"><canvas id="cRank"></canvas></div>
  </div>

  <div class="chart-box">
    <div class="chart-box-title">🎵 Daily streams over time</div>
    <div class="chart-wrap"><canvas id="cStr"></canvas></div>
  </div>

  <div class="section-hdr">Full history (${h.length} dates)</div>
  <div class="hist-table">
    <div class="hist-head">
      <span>Date</span>
      <span style="justify-content:center">Rank</span>
      <span class="r">Streams</span>
      <span class="r">Δ Streams</span>
      <span class="r">Δ Rank</span>
      <span style="justify-content:center">Peak</span>
    </div>
    ${histHtml}
    ${h.length > 150 ? `<div class="hist-more">Showing latest 150 of ${h.length} entries</div>` : ''}
  </div>`;

  /* Chart.js */
  const labels  = h.map(e => e.date);
  const ranks   = h.map(e => e.rank);
  const streams = h.map(e => e.streams);
  const sparse  = labels.length > 120;

  const base = {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
  };

  const r = new Chart(document.getElementById('cRank').getContext('2d'), {
    type: 'line',
    data: { labels, datasets: [{ data: ranks,
      borderColor: '#1db954', backgroundColor: 'rgba(29,185,84,.1)',
      borderWidth: 2, tension: 0.3, fill: true,
      pointRadius: sparse ? 0 : 3, pointHoverRadius: 5,
    }]},
    options: { ...base,
      scales: {
        y: { reverse: true, grid: { color: 'rgba(0,0,0,.05)' },
          ticks: { font: { size: 11 }, callback: v => `#${v}` } },
        x: { grid: { display: false },
          ticks: { font: { size: 10 }, maxTicksLimit: 10, maxRotation: 0 } },
      },
      plugins: { ...base.plugins, tooltip: { callbacks: {
        title: c => c[0].label,
        label: c => `Rank #${c.parsed.y}`,
      }}},
    },
  });

  const s = new Chart(document.getElementById('cStr').getContext('2d'), {
    type: 'line',
    data: { labels, datasets: [{ data: streams,
      borderColor: '#7e57ff', backgroundColor: 'rgba(126,87,255,.1)',
      borderWidth: 2, tension: 0.3, fill: true,
      pointRadius: sparse ? 0 : 3, pointHoverRadius: 5,
    }]},
    options: { ...base,
      scales: {
        y: { grid: { color: 'rgba(0,0,0,.05)' },
          ticks: { font: { size: 11 },
            callback: v => v >= 1e6 ? `${(v/1e6).toFixed(1)}M`
              : v >= 1e3 ? `${(v/1e3).toFixed(0)}K` : v,
          }},
        x: { grid: { display: false },
          ticks: { font: { size: 10 }, maxTicksLimit: 10, maxRotation: 0 } },
      },
      plugins: { ...base.plugins, tooltip: { callbacks: {
        title: c => c[0].label,
        label: c => `${(c.parsed.y || 0).toLocaleString()} streams`,
      }}},
    },
  });

  cjs.push(r, s);
}

/* ════════════════════════════════════════
   PAGE: HISTORY (charted songs by album)
════════════════════════════════════════ */
let discoCache = null;

async function renderHistoryPage() {
  navActive('history');
  const app = document.getElementById('app');
  app.innerHTML = `<div class="loading"><div class="spinner"></div> Loading…</div>`;

  if (!discoCache) {
    try {
      const r = await fetch('../site/data/songs.json');
      discoCache = (await r.json()).songs || [];
    } catch(e) {
      app.innerHTML = `<div class="empty">Failed to load data.<br><span style="font-size:11px">${e.message}</span></div>`;
      return;
    }
  }

  let rgn   = 'global';
  let query = '';

  async function render() {
    await loadHist(rgn);
    const latestDate  = S[rgn].avail[S[rgn].avail.length - 1];
    const histEntries = Object.entries(S[rgn].hist);
    const chartLookup = new Map(histEntries.map(([n]) => [n.toLowerCase(), n]));

    const resolveChartName = song => chartLookup.get(song.title.toLowerCase()) || null;

    const todaySet = new Set(
      histEntries.filter(([, h]) => h[latestDate] != null).map(([n]) => n.toLowerCase())
    );

    function getSongData(chartName) {
      const h = S[rgn].hist[chartName] || {};
      const dates = Object.keys(h).sort();
      if (!dates.length) return null;
      const lastDay = dates[dates.length - 1];
      return {
        lastDay,
        pk:       Math.min(...dates.map(d => h[d].rank)),
        lastRank: h[lastDay].rank,
        total:    h[lastDay].total_days || dates.length,
      };
    }

    /* Group by album — keep only charted songs */
    const TRACK_TYPES = new Set(['track', 'standalone']);
    const albumMap = new Map();
    for (const song of discoCache) {
      if (!TRACK_TYPES.has(song.type)) continue;
      const chartName = resolveChartName(song);
      if (!chartName) continue; // never charted → skip
      const album = song.primary_album || 'Other';
      if (!albumMap.has(album)) albumMap.set(album, { cover: null, songs: [] });
      const entry = albumMap.get(album);
      if (!entry.cover && song.image_url) entry.cover = song.image_url;
      entry.songs.push({ song, chartName });
    }

    /* Apply search filter per album */
    const q = query.toLowerCase();

    /* Sort albums: in-chart first (desc by count), then by album's latest LAST DAY desc */
    const albums = [...albumMap.entries()].map(([album, { cover, songs }]) => {
      const filtered = q ? songs.filter(({ song }) => song.title.toLowerCase().includes(q)) : songs;
      const inChartCount = songs.filter(({ chartName }) => todaySet.has(chartName.toLowerCase())).length;
      const albumLastDay = songs.reduce((max, { chartName }) => {
        const st = getSongData(chartName);
        return st && st.lastDay > max ? st.lastDay : max;
      }, '');
      return { album, cover, songs: filtered, inChartCount, albumLastDay };
    }).filter(({ songs }) => songs.length > 0);

    albums.sort((a, b) => {
      if (a.inChartCount !== b.inChartCount) return b.inChartCount - a.inChartCount;
      return b.albumLastDay.localeCompare(a.albumLastDay);
    });

    const albumsHtml = albums.map(({ album, cover, songs, inChartCount }) => {
      const coverEl = cover
        ? `<img class="disco-album-cover" src="${cover}" alt="" loading="lazy">`
        : `<div class="disco-album-cover-ph">💿</div>`;

      /* Sort: in chart first, then by LAST DAY desc */
      const sorted = [...songs].sort((a, b) => {
        const aIn = todaySet.has(a.chartName.toLowerCase());
        const bIn = todaySet.has(b.chartName.toLowerCase());
        if (aIn !== bIn) return (bIn ? 1 : 0) - (aIn ? 1 : 0);
        const aSt = getSongData(a.chartName);
        const bSt = getSongData(b.chartName);
        return (bSt?.lastDay || '').localeCompare(aSt?.lastDay || '');
      });

      const songRows = sorted.map(({ song, chartName }, i) => {
        const isToday = todaySet.has(chartName.toLowerCase());
        const st = getSongData(chartName);
        const badge = isToday
          ? `<span class="disco-song-chart today">in chart</span>`
          : `<span class="disco-song-chart charted">charted</span>`;
        const pkBadge = !isToday && st
          ? `<span class="pk-badge">PK #${st.pk}</span>` : '';
        const rank    = st ? `#${st.lastRank}` : '—';
        const lastDay = st ? st.lastDay : '—';
        const total   = st ? `${st.total}d` : '—';

        return `<a class="disco-song has-chart" href="#${rgn}/song/${encodeURIComponent(chartName)}">
          <span class="disco-song-num">${i + 1}</span>
          <span class="disco-song-title" style="display:flex;align-items:center;gap:6px">${pkBadge}${song.title}</span>
          ${badge}
          <span class="disco-cell r">${rank}</span>
          <span class="disco-cell r">${total}</span>
          <span class="disco-cell muted r">${lastDay}</span>
        </a>`;
      }).join('');

      return `<div class="disco-album-card">
        <div class="disco-album-hdr">
          ${coverEl}
          <div>
            <div class="disco-album-name">${album}</div>
            <div class="disco-album-meta">${songs.length} charted · ${inChartCount} in chart today</div>
          </div>
        </div>
        <div class="disco-tbl-head">
          <span>#</span><span>Song</span><span>Status</span>
          <span class="r">Rank</span><span class="r">Days</span><span class="r">Last Day</span>
        </div>
        ${songRows}
      </div>`;
    }).join('');

    app.innerHTML = `
      <div class="disco-toolbar">
        <input class="disco-search" id="histSearch" placeholder="Search songs…" value="${query.replace(/"/g,'&quot;')}">
        <div class="disco-rgn">
          <button class="disco-rgn-btn ${rgn==='global'?'active':''}" data-r="global">🌍 Global</button>
          <button class="disco-rgn-btn ${rgn==='fr'    ?'active':''}" data-r="fr">🇫🇷 France</button>
          <button class="disco-rgn-btn ${rgn==='us'    ?'active':''}" data-r="us">🇺🇸 US</button>
          <button class="disco-rgn-btn ${rgn==='uk'    ?'active':''}" data-r="uk">🇬🇧 UK</button>
        </div>
      </div>
      <div class="disco-albums">${albumsHtml || '<div class="empty">No results.</div>'}</div>`;

    document.getElementById('histSearch').addEventListener('input', e => {
      query = e.target.value.trim();
      render();
    });
    document.querySelectorAll('.disco-rgn-btn').forEach(b => {
      b.addEventListener('click', () => { rgn = b.dataset.r; render(); });
    });
  }

  render();
}

/* ════════════════════════════════════════
   INIT
════════════════════════════════════════ */
async function init() {
  try {
    const { rgn } = parseHash();
    await loadHist(rgn);
    window.addEventListener('hashchange', route);
    route();
  } catch(e) {
    document.getElementById('app').innerHTML = `
    <div class="empty">
      <div style="font-size:32px;margin-bottom:12px">⚠️</div>
      Failed to load chart data.<br>
      <span style="font-size:11px;color:var(--muted)">${e.message}</span>
    </div>`;
  }
}

init();
