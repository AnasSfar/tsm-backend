"""Default-style song card CSS — used by post_weekend_song_gainers.py.

Pure template: takes pre-computed values from song_card.py's orchestrator
(render_song_card) and returns the CSS text. No dependency back on
song_card.py, so this can't create an import cycle with the orchestrator.
"""
from __future__ import annotations


def build_css(*, gradient: str, title_font_size: int, body_gap: int) -> str:
    return f"""
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
  font-family:Inter,-apple-system,'Helvetica Neue',Arial,sans-serif;
  width:920px;height:480px;
  background:{gradient};
  position:relative;overflow:hidden;color:#fff;
}}
body:before{{
  content:"";position:absolute;inset:0;
  background:
    linear-gradient(90deg,rgba(4,10,16,.62) 0%,rgba(4,10,16,.42) 49%,rgba(4,10,16,.08) 100%),
    radial-gradient(circle at 18% 85%,rgba(255,255,255,.20),rgba(255,255,255,0) 36%);
}}
.layout{{height:480px;position:relative;z-index:1}}
.cover-col{{
  position:absolute;right:20px;top:40px;width:400px;height:400px;
  overflow:hidden;border-radius:30px;
  box-shadow:0 24px 50px rgba(0,0,0,.42),0 0 0 1px rgba(255,255,255,.18);
}}
.cover,.cover-ph{{width:400px;height:400px;object-fit:cover;display:block}}
.cover-ph{{background:#172421}}
.info-col{{
  position:absolute;left:32px;top:36px;bottom:42px;width:450px;
  display:flex;flex-direction:column;
}}
.hdr-row{{display:flex;align-items:center;gap:12px;width:100%;flex-shrink:0}}
.body-col{{flex:1;min-height:0;display:flex;flex-direction:column;justify-content:center;gap:{body_gap}px;margin-top:7px}}
.logo{{width:33px;height:33px;flex-shrink:0}}
.hdr-label{{
  color:rgba(255,255,255,.92);font-size:15px;font-weight:900;
  letter-spacing:.12em;text-transform:uppercase;
}}
.mode-badge{{
  margin-left:auto;color:rgba(255,255,255,.88);
  background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.22);
  border-radius:999px;padding:8px 13px;
  font-size:13px;font-weight:900;letter-spacing:.08em;text-transform:uppercase;
  white-space:nowrap;
}}
.title{{
  color:#fff;font-size:{title_font_size}px;font-weight:950;
  line-height:1.14;letter-spacing:0;flex-shrink:0;
  max-width:442px;display:-webkit-box;-webkit-line-clamp:2;
  -webkit-box-orient:vertical;overflow:hidden;
  text-shadow:0 3px 18px rgba(0,0,0,.28);
  margin-bottom:-5px;
}}
.stats{{display:flex;gap:12px;margin-top:2px;width:100%}}
.stat{{
  background:rgba(7,14,22,.58);border:1px solid rgba(255,255,255,.20);
  border-radius:18px;padding:11px 16px 10px;flex:1;min-width:0;
  box-shadow:0 12px 30px rgba(0,0,0,.18);
}}
.stat-lbl{{
  font-size:10px;font-weight:900;letter-spacing:.1em;
  text-transform:uppercase;color:rgba(255,255,255,.58);margin-bottom:4px;
}}
.stat-val{{font-size:28px;font-weight:950;color:#fff;line-height:1;white-space:nowrap}}
.stat-val.medium{{font-size:25px}}
.stat-val.long{{font-size:22px}}
.chg{{font-size:12px;font-weight:900;margin-top:5px;letter-spacing:.02em}}
.chg.new,.chg.re,.chg.up{{color:#7ee787}}
.chg.down{{color:#fca5a5}}
.chg.flat{{color:rgba(255,255,255,.52)}}
.extra{{
  color:rgba(255,255,255,.76);font-size:15px;font-weight:750;
  max-width:430px;margin-top:-2px;margin-bottom:1px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}}
.ftr{{
  position:absolute;bottom:16px;left:35px;right:35px;z-index:2;
  display:flex;justify-content:space-between;
}}
.ftr-l{{font-size:12px;color:rgba(255,255,255,.52);font-weight:700}}
.ftr-brand{{display:flex;align-items:center;gap:7px}}
.tsm-logo{{width:21px;height:21px;object-fit:contain;border-radius:4px}}
"""
