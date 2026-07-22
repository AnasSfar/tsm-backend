---
name: collector-billboard
description: "Work safely on collectors/billboard: Billboard scraping, TayBoard/Swift Top weekly charts, Swift Top 100/albums/eras variants, generated images, R2 exports, history CSVs, and chart scoring from streams/charts/Apple Music inputs. Use before auditing, debugging, running, or modifying Billboard/TayBoard collector code."
---

# Collector Billboard

Read `CONTEXTE.md` before changing or running anything under
`collectors/billboard`.

Use `data-rules` before changing scores, histories, exports, or generated public
stats. Use `image-gen` before changing generated TayBoard images.

Safe checks:

```powershell
python .\collectors\billboard\swift_top_100.py --dry-run
python .\collectors\billboard\swift_top_100.py --help
python .\collectors\billboard\scrape_billboard.py
```

Run networked scraping/upload only when intended.
