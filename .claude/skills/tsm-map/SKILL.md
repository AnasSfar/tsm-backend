---
name: tsm-map
description: Carte du code TSM (les deux repos). À utiliser AVANT tout Grep/Glob exploratoire quand on cherche où vit une fonctionnalité, quel fichier fait quoi, ou comment les données circulent (backend → R2 → frontend). Évite de ré-explorer le code à chaque session.
---

# Carte du code TSM

Deux repos qui travaillent ensemble. **Ne pas ré-explorer ce qui est décrit ici — les chemins sont vérifiés.**

Pour le détail fichier par fichier de tsm-backend (rôle + options CLI de chaque script), voir **`REPO_CONTEXT.md`** à la racine du repo — lire la section concernée au lieu de fouiller `collectors/`/`scripts/`.

## Flux de données global

```
tsm-backend (Python, local, Task Scheduler)  →  R2 (bucket prod: taylor-data)  →  tsm-frontend API (FastAPI/Vercel)  →  React
```

- Bucket R2 **prod = `taylor-data`** (R2_APP_BUCKET non défini sur Vercel). Le `.env` **local** de tsm-frontend pointe sur `taylor-app` → les settings/uploads locaux ≠ prod.
- `tsm-backend/website/` = site statique **LEGACY, interdit** sauf demande explicite (règle CLAUDE.md).
- Préfixes/clés R2 : source de vérité = `tsm-backend/scripts/r2_keys.py`, miroir tenu à la main côté frontend = `tsm-frontend/api/data/r2_keys.py`. Quelles données R2 sont pérennes vs cache jetable → skill `scripts-maintenance` § "R2 : données pérennes vs cache".

## tsm-backend (`c:\Users\sfara\Documents\GitHub\tsm-backend`)

- CLI : `python -m tsm daily` et `python -m tsm collect charts` (code dans `tsm/cli.py`). Launchers racine : `run_daily.bat`, `run_all_charts.bat`.
- `collectors/` : `spotify/` (streams + charts), `apple_music/`, `billboard/`, `youtube/`, `comp/`, `website/`.
- Pipeline streams : `collectors/spotify/streams/update_streams.py` ; outils dans `collectors/spotify/streams/tools/scripts/` (`history_store.py`, `spotify_api.py`, `reconcile_gap_catchup.py`, `generate_albums_image.py`, `post_albums_twitter.py` hors week-end).
- Catalogue maître : `db/discography/artist.json` ; cache covers : `db/discography/track_cover_cache.json`.
- Ops détaillées → skill `pipeline-ops`.

## tsm-frontend (`c:\Users\sfara\Documents\GitHub\tsm-frontend`)

- `frontend/` = React 19 + Vite, build vers `../public`. `api/` = FastAPI (`api/index.py`, routes dans `api/routes/` : `site_settings.py`, `leaderboard.py`, `daily.py`, `news.py`, `journalist.py` (fact-checks + tips anonymes + votes helpful + watchlist « comptes à signaler »/Twitter notes (community notes d'autrui à aller noter SUR Twitter, badge consigne helpful/not_helpful) + traductions DeepL des notes de `/journalist-department`), `image_proxy.py`, `admin_upload.py` (Media Library, tokens admin OU journalist ; les 302 `/api/landing-media/` ont un cache court + cache-buster `landingMediaSrc()` — piège cache 7 j corrigé juillet 2026), `version.py` (`/api/version`, dates de dernier snapshot par collector — streams/charts/tayboard/apple music/youtube, poll léger cache CDN 5-10min), `home_highlights.py` (`/api/home-highlights`, précalcule côté serveur le pool de highlights de la Charts Gallery — réutilise `get_version`/`get_charts`/`get_apple_music` + les loaders directement en appel Python plutôt que HTTP interne — évite au client de fan-out sur 6+ endpoints), `data/precompute_cache.py` (cache R2 générique `read_fresh_cache`/`write_cache`, clé `cache/*.json` + horodatage `_cache_generated_at` — utilisé par `version.py` et `home_highlights.py` depuis 2026-07-28 : `get_charts`/`get_apple_music` refont un matching chanson complet sur tout l'historique CSV à chaque appel, donc ces deux routes lisent d'abord ce cache (5 min) avant de recalculer ; `signal_updated_at` reste lu en direct hors cache pour ne pas retarder la bannière live), etc.).
- **Qui écrit vraiment ce cache** : depuis 2026-07-28, c'est `tsm-backend/scripts/generate_home_highlights.py` qui régénère `cache/home_highlights.json`/`cache/version.json` (best-effort, en fin de `update_streams.py`/`run_all_charts.py`/`run_apple_music.py`/`swift_top_100.py`/`swift_top_albums.py`) — le calcul côté frontend (`read_fresh_cache` miss → recompute → `write_cache`) n'est qu'un filet de sécurité si le backend n'a pas encore tourné ou a échoué. Le script backend n'a PAS porté le matching flou de `api/routes/charts.py` : pour le highlight `chart_1`/mouvements NEW/RE, il lit directement `db/charts_history_global.csv` (track_id/movement déjà résolus par le collector à la collecte) plutôt que de dupliquer `_pick_best_song_match`.
- **Types de highlights (2026-08-05)** : `most_streamed`, `biggest_gainer`, `total_daily`, `milestone`, `surpass`, `best_day_since` + `oldest_record` (record `kind="since"` au `days_since` max, séparé du pick principal pour ne pas être écrasé par un `best_ever` du jour), `top_album`/`top_era`/`tayboard_1` (masqués si le chart TayBoard a plus de 2 jours vs `latest_date`), `apple_music_1`, `chart_1`/`chart_new`/`chart_re`, `regional_climb` (plus gros bond de rang jour/jour, seuil 20 places, `global`/`fr`/`us`/`uk` confondus). Le rendu/label/lien par type vit côté frontend dans `components/ChartsHighlights.jsx` (`LABEL_KEYS`/`LINK_BY_TYPE`/`localizeHighlight`) — toujours garder les deux fichiers en phase quand un type change.
- **Perf `/api/home-highlights` et `/api/version`** : `api/config.py::_get_r2_client` est maintenant `@lru_cache` (2026-08-05) — `boto3.client()` se reconstruisait sur CHAQUE requête, même en cas de hit du cache R2. Toute route qui appelle `get_r2_client()`/`get_r2_app_client()` en bénéficie. Le gros du "ça met du temps à s'afficher" restant est côté client : `Home.jsx` fait un fetch CSR après montage (donc skeleton → 1 aller-retour réseau + cold start serverless Python incompressible) plutôt qu'un SSR/prefetch.
- Déploiement : push sur `main` (GitHub AnasSfar/tsm-frontend) → Vercel (config `vercel.ts`, front + API ensemble). → skill `deploy`.
- Dev local : `dev.bat` racine (uvicorn port 8003 + Vite port 3000 ; fallback data disque via `TSM_BACKEND_ROOT=..\tsm-backend`).

### Fichiers clés frontend (`frontend/src/`)

| Fichier | Rôle |
|---|---|
| `App.jsx` | Routing, application des settings (`applySiteSettings`), thème : `activeTheme = forcedTheme ?? routeTheme ?? userTheme ?? themeMode`, classes `body.page-*` via `PageClassManager`, timer de bornes du thème custom |
| `store/useStore.js` | Zustand ; `partialize` = sous-ensemble persisté (le reste = session) |
| `api/client.js` | Appels API ; `patchSiteSettings` dispatche `SITE_SETTINGS_UPDATED_EVENT` |
| `pages/Admin.jsx` (~3000 l.) | Console admin (`/admin/console`) — **lire `pages/ADMIN_CONTEXT.md` (section ciblée) avant d'y toucher** → skill `admin-work`. `/admin` = page de choix `AdminGate.jsx` ; `/admin/journalist` = `JournalistAdmin.jsx` (token journalist séparé) |
| `pages/ImageStudio.jsx` | Générateur d'images PNG (templates dans `components/imageTemplates/`) |
| `pages/Home.jsx` (Charts Gallery, `/`) | Tuiles collectors triées par date de dernier snapshot (`/api/version` via `/api/home-highlights`) + `components/ChartsHighlights.jsx` (carrousel de highlights). Un seul fetch `/api/home-highlights` pour toute la section (voir `api/routes/home_highlights.py` ci-dessus) — ne pas réintroduire de fetch par composant, le pool brut est calculé serveur et juste traduit/shufflé côté client |
| `components/adminUI.jsx` | Primitives admin : AdminCard, Field, Toggle, StatusChip (tones live/warn/off UNIQUEMENT), SaveBar, ConfirmButton, ScheduleFields, Icon |
| `components/CustomThemeModule.jsx` | Thème temporaire (éditeur de tokens) |
| `components/MediaLibraryModule.jsx` | Media Library (extraite d'Admin.jsx) — rendue par la console ET `/admin/journalist` |
| `utils/anniversaries.js` | `ALL_THEME_OPTIONS` (15 thèmes) |
| `utils/customTheme.js` | `theme-custom` : tokens, dérivation dark, injection `<style id="tsm-custom-theme">` |
| `utils/schedule.js` | `isWithinSchedule` (chaîne vide = borne illimitée) |
| `styles/STYLE.md` | **Règlement CSS/design — obligatoire avant tout travail de style** → skill `style-rules` |
| `styles/variables.css` | Tokens `:root` (light) + scaffold dark `body[data-color-scheme="dark"]` |
| `styles/themes.css` | Blocs statiques `body[data-theme="theme-x"]` |
| `styles/globals.css` | Point d'entrée CSS global (ordre d'import important) |

### Settings du site
Stockés dans R2 `site_settings.json`, servis/modifiés par `api/routes/site_settings.py` : PATCH **par section** (`PATCHABLE_SECTIONS`), normalisation `_normalize_settings`, auth admin `X-News-Token` (`require_admin_token`), backups auto.

## Pièges connus
- La commande de rebuild graphify du CLAUDE.md **échoue** (module non installé) — ne pas la lancer.
- PowerShell 5.1 : stderr des exe natifs (vite, git) affiché en `NativeCommandError` = bruit, vérifier le vrai résultat (« ✓ built »).

## Maintenance (obligatoire)
Si ton changement contredit ou complète cette carte (fichier déplacé, nouveau module, flux modifié), mets à jour cette skill ET `REPO_CONTEXT.md` dans la même session.
