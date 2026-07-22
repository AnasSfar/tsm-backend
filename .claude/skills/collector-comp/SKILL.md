---
name: collector-comp
description: "Work safely on collectors/comp: shared chart/card/table rendering components used by Spotify Charts, Spotify Streams, Apple Music, Billboard/TayBoard, image export frames, discography helpers, formatting, and cover cache utilities. Use before modifying shared visual components or rendering helpers."
---

# Collector Components

Read `CONTEXTE.md` before changing anything under `collectors/comp`.

Use `image-gen` or visual QA workflows when the change affects generated images.
Because these components are shared, verify at least one caller from each
affected collector family.

Safe checks depend on the caller; prefer a targeted image generation command and
inspect the output.
