# Eras Tour homepage hero collage

`panel-01` .. `panel-11` are the portrait photos from the hero strip on
https://tstheerastour.taylorswift.com/ , one per era in **album order**. Downloaded
2026-08-31 from the `assets.dmi.umgapps.com` CDN at their **original resolution** —
no 4K version exists; these are the largest files the site serves. `panel-12`
(Showgirl) is a derived composite, not from that site — see below.

| File | Source URL | Resolution |
|------|-----------|------------|
| panel-01.jpg | https://assets.dmi.umgapps.com/assets/taylor-swift/tour-site/landing-images/1.jpg | 735×1018 |
| panel-02.jpg | https://assets.dmi.umgapps.com/assets/taylor-swift/landing-images/1678544276252-2-v2.jpg | 1467×1884 |
| panel-03.jpg | https://assets.dmi.umgapps.com/assets/taylor-swift/tour-site/landing-images/3.jpg | 735×1018 |
| panel-04.jpg | https://assets.dmi.umgapps.com/assets/taylor-swift/tour-site/landing-images/4.jpg | 735×1018 |
| panel-05.jpg | https://assets.dmi.umgapps.com/assets/taylor-swift/tour-site/landing-images/5.jpg | 735×1018 |
| panel-06.jpg | https://assets.dmi.umgapps.com/assets/taylor-swift/tour-site/landing-images/6.jpg | 735×1018 |
| panel-07.jpg | https://assets.dmi.umgapps.com/assets/taylor-swift/tour-site/landing-images/7.jpg | 735×1018 |
| panel-08.jpg | https://assets.dmi.umgapps.com/assets/taylor-swift/tour-site/landing-images/8.jpg | 735×1018 |
| panel-09.jpg | https://assets.dmi.umgapps.com/assets/taylor-swift/landing-images/1678544281122-9-v2.jpg | 1467×1884 |
| panel-10.jpg | https://assets.dmi.umgapps.com/assets/ts/ttpd/1715195830303-new-midnights.jpg | 735×1018 |
| panel-11.jpg | https://assets.dmi.umgapps.com/assets/ts/ttpd/1715168073245-ttpd.jpg | 735×1018 |
| panel-12.jpg | *derived* — see "Showgirl panel" below | 735×1018 (upscaled) |

## Showgirl panel (derived — not authentic Eras Tour art)

The Eras Tour key-art shoot predates *The Life of a Showgirl* (2025), so there is no
12th portrait in this grainy tinted style. `panel-12.jpg` is a **styled composite**:
the official "bathtub" promo photo (`panel-12-source.jpg`, cropped from
`taylorswift.com/wp-content/uploads/sites/2529/2024/12/img-tloas.jpg`, only 672×420)
cropped to portrait, desaturated, run through a duotone ramp built on the site's
Showgirl accent `#f97316` (`SHOWGIRL_THEME.accent`) + film grain so it sits next to
panels 01–11. It is noticeably softer than the rest (source upscale).

Rebuild it with `python scripts/build_showgirl_panel.py` (tune `FACE_CX` / the `DUOTONE`
ramp in that script), then re-run `build_eras_strip.py`.

## Horizontal / landscape

`landscape-keyart-1200x630.jpg` — the one official landscape image on the site
(`assets.dmi.umgapps.com/assets/taylor-swift/meta/1678567321897-share.jpg`): the
colour collage + "TAYLOR SWIFT / THE ERAS TOUR" logo lockup on white (the OG/share
image). There is no other landscape Taylor photography on the site.

The site's own B&W 2-row collage (playlist-generator background) was dropped — it
was 11 photos in no particular order. `collage-6x2.jpg` / `collage-6x2-bw.jpg`
below replace it with our 12 panels in album order.

Not downloaded (redundant): `tour-site/landing-images/2.jpg`, `9.jpg`, `10.jpg` are
lower-res sepia/blue colour-grades of poses already in `panel-02`, `panel-09` and the
big B&W portrait. The support-act headshots on the site (Sabrina Carpenter, Gracie
Abrams, Paramore, HAIM, MUNA, …) are the openers, not Taylor.

## Composed "all eras" images

All 12 panels in **album order** (debut → Showgirl), built by
`python scripts/build_eras_strip.py` (from the tsm-frontend repo root):

| File | Layout | Size |
|------|--------|------|
| `../headers/all-eras.jpg` | single-row strip, web | ~2200px wide |
| `all-eras-full.jpg` | single-row strip, hi-res | ~4400px wide |
| `collage-6x2.jpg` | 6×2 grid, per-era colour tints | 1920×886 |
| `collage-6x2-bw.jpg` | 6×2 grid, black & white | 1920×886 |

`--labels` burns the era name + year onto each panel of the strip; `--gap N` adds
white separators; `--width N` sets the hi-res strip width.

The React component is `frontend/src/components/ErasStripHeader.jsx` (exports the
`ERAS_STRIP` array + a `<ErasStripHeader />` with `labels` / `linkToMuseums` /
`grayscale` / `activeSlug` props). Not mounted anywhere yet.

## Licensing

Official Taylor Swift / UMG promotional images — internal/fan use only, not licensed stock.

To get ~4K versions you would need to run these through an AI upscaler
(Real-ESRGAN, Topaz Gigapixel, etc.) — the source detail caps at the sizes above.
