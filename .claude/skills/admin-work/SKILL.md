---
name: admin-work
description: Travailler sur la page Admin du frontend TSM (Admin.jsx, modules, adminUI) ou ajouter/modifier une section de settings du site (site_settings.py + module admin). Contient le workflow complet et les patterns — évite de relire Admin.jsx (~3000 lignes) en entier.
---

# Travail sur l'Admin TSM

Référence complète : `tsm-frontend/frontend/src/pages/ADMIN_CONTEXT.md` — **lire uniquement la section du module concerné**, pas tout le fichier.

## Règles dures
- Primitives obligatoires depuis `components/adminUI.jsx` : `AdminCard`, `Field`, `Toggle`, `StatusChip`, `SaveBar`, `ConfirmButton`, `ScheduleFields`, `Icon`. **Ne pas recréer d'équivalents.**
- `StatusChip` : tones **live / warn / off uniquement** (pas d'autres valeurs).
- Segmented control : `.adm-segmented` > `.adm-segment.active`. Boutons : `.adm-btn --primary/--soft/--ghost`. CSS admin : classes préfixées `adm-` dans `styles/Admin.css`.
- Auth : toutes les écritures passent par `X-News-Token` (backend `require_admin_token`).
- Planification : `isWithinSchedule` de `utils/schedule.js` (`start_at`/`end_at`, chaîne vide = illimité) + `ScheduleFields` côté UI + pattern « hiddenReason » (callout warn expliquant pourquoi un contenu activé n'est pas visible : pas commencé / expiré / incomplet).

## Pattern module admin
- Gros module → **fichier séparé** `components/XxxModule.jsx` (exemples : `CustomThemeModule.jsx`, `NewsAdminEditor`). Petit module → inline dans Admin.jsx.
- Enregistrement dans `Admin.jsx` : ① item nav `{ id, label, icon }` dans le bon groupe du sidebar ; ② rendu conditionnel `{section === "xxx" && <XxxModule token={token} onDirtyChange={handleDirtyChange} />}`.
- Dirty-state : baseline JSON (`JSON.stringify` du form au chargement), comparaison à chaque édition, `onDirtyChange(bool)` remonte au guard de navigation, `SaveBar` en bas.
- Save : `patchSiteSettings({ ma_section: form }, token)` depuis `api/client.js` — dispatche déjà `SITE_SETTINGS_UPDATED_EVENT` donc App.jsx ré-applique tout seul ; **re-baseliner depuis la réponse normalisée** (pas depuis le form local).

## Ajouter une section de settings (checklist bout en bout)
1. **Backend** `api/routes/site_settings.py` :
   - ajouter `"ma_section"` à `PATCHABLE_SECTIONS` ;
   - écrire `_normalize_ma_section(data)` (défauts sûrs, caps de longueur, entrées invalides supprimées silencieusement) et la brancher dans `_normalize_settings` (couvre GET/POST/PATCH/backups/restore d'un coup) ;
   - règle métier bloquante → `_validate_payload` (HTTP 400 avec message clair).
   - Valeurs CSS/URL : sanitizer **miroir front/back** (modèle : `CUSTOM_THEME_TOKEN_KEYS`, `_CSS_VALUE_RE` rejette `{};<>@`, `url(` interdit sauf clés URL avec regex same-origin).
2. **Frontend** : module admin (pattern ci-dessus).
3. **Si ça touche le rendu public** : brancher dans `applySiteSettings` (App.jsx) ; injection de CSS uniquement via `textContent` (jamais innerHTML).
4. Vérif : `cd tsm-frontend/frontend; npx vite build` puis → skill `deploy` (front + API partent ensemble).

## Pièges
- Le PATCH est par section : sauver un module ne clobber PAS les autres sections — ne pas renvoyer tout l'objet settings.
- Un backup pré-feature restauré passe par `_normalize_settings` → la nouvelle section retombe sur ses défauts, pas de crash à gérer côté client.
- Prod = bucket R2 `taylor-data` ; en local `.env` → `taylor-app` (les settings locaux ne sont pas ceux de prod).

## Maintenance (obligatoire)
Après tout changement admin (module, section de settings, endpoint), mets à jour `ADMIN_CONTEXT.md` dans la même session ; si le workflow décrit ici change, mets aussi cette skill à jour.
