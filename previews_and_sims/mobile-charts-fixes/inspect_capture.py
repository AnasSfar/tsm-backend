"""Puts the TS Top Songs share section in capture mode (class + 1280px, like
ShareImageButton) and dumps computed layout of a few rows. Read-only."""
import json
from playwright.sync_api import sync_playwright

URL = "http://localhost:5173/amcharts/apple-music?section=taylor_swift&tab=ts_top_songs"
JS = """() => {
  const btn = document.querySelector('.share-image-btn');
  let node = btn; while (node && !node.querySelector('.am-chart-table')) node = node.parentElement;
  node.classList.add('is-share-image-capturing'); node.style.width='1280px'; node.style.maxWidth='none'; node.style.overflow='visible';
  const rows = [...node.querySelectorAll('.songs-list .am-song-grid')].slice(0, 16);
  const pick = (el) => { const cs = getComputedStyle(el); return {cls: el.className, display: cs.display, gtc: cs.gridTemplateColumns, width: el.getBoundingClientRect().width}; };
  const card = rows[0].parentElement, list = card.parentElement;
  const title = rows[14].querySelector('.row-song-title');
  return {node: node.className, list: pick(list), card: pick(card), rows: [rows[0], rows[2], rows[14]].map(pick),
          titleWS: getComputedStyle(title).whiteSpace, cell: pick(rows[14].querySelector('.chart-cell-song'))};
}"""
with sync_playwright() as p:
    b = p.chromium.launch(); ctx = b.new_context(**p.devices["iPhone 13"]); page = ctx.new_page()
    page.goto(URL, wait_until="networkidle", timeout=60000); page.wait_for_timeout(2000)
    print(json.dumps(page.evaluate(JS), indent=1)); b.close()
