---
name: style-rules
description: Règles CSS/design du frontend TSM en version condensée (tokens, thèmes, breakpoints, modules à réutiliser, checklist). Suffit pour des retouches ; pour un gros chantier design, lire STYLE.md en entier. À charger avant d'écrire ou modifier du CSS/JSX visuel.
---

# Règles CSS/design TSM (condensé)

Source de vérité : `tsm-frontend/frontend/src/styles/STYLE.md`. Ce condensé suffit pour des retouches ; **gros chantier design (nouvelle page, nouveau thème, refonte) → lire STYLE.md en entier.**

## Règles d'or (non négociables)
1. **Aucune couleur en dur** — tokens `var(--…)` de `variables.css` + `color-mix()` pour les variantes. Exception assumée : `components/imageTemplates/` (export PNG, couleurs fixes, classes `.imgst-`).
2. Thème uniquement via `body[data-theme]` / `body[data-color-scheme]` posés par App.jsx — jamais de manipulation directe ailleurs.
3. **CSS pur** : pas de framework, préprocesseur, ni CSS-in-JS. Fichier CSS importé par son composant propriétaire ; le global passe par `globals.css` (ordre d'import important).
4. **Réutiliser avant de créer** (voir modules ci-dessous).
5. Tout nouveau style doit marcher en **light + dark sur les 16 thèmes** (les tokens le garantissent gratuitement ; override dark manuel seulement pour un fond en dur justifié).
6. Mobile obligatoire : desktop-first, breakpoints standard **900 / 700-640 / 600 / 480-360** px. Pas de scroll horizontal au niveau page (tableaux larges → conteneur `overflow-x:auto`). Inputs ≥16px sur mobile (anti-zoom iOS), cibles tactiles ≥36px.

## Nommage & organisation
- Classes kebab-case **préfixées par feature** : `landing-`, `adm-`, `imgstudio-`, `imgst-`, etc.
- Sous-dossiers `ttpd/` et `showgirl/` = mondes séparés, ne pas y importer les conventions globales.

## Look & feel
- Glassmorphism (surfaces translucides + `--glass-blur`), police **Inter** ; décoratives cantonnées : Bebas Neue, DM Sans, TT Modernoir, ACsteelfish — **pas de nouvelle font**.
- Or = rang #1 (`--gold-1/2/3`) ; deltas : vert hausse / rouge baisse / NEW / RE ; hovers courts.

## Thèmes
- Ajouter un thème = **4 endroits** : bloc light + bloc dark dans `themes.css`, entrée `ALL_THEME_OPTIONS` dans `utils/anniversaries.js`, allow-list `THEMES` dans `api/routes/site_settings.py`.
- `theme-custom` = injecté au runtime (`utils/customTheme.js`), jamais statique. ⚠️ Piège de cascade : le bloc light injecté arrive après le scaffold dark de `variables.css` à spécificité égale → le bloc dark injecté doit redéfinir **tous** les tokens.

## Modules partagés à réutiliser (ne pas dupliquer)
- Public : `.page`, `.section-card`, `.stat-card`, `.ranking-table`, `.song-row-card`, `.delta`, `.table-wrap`, `.toolbar`, `.sort-menu`, `.milestone-chip`, `.focus-overlay` ; composants `RankChange`, `Sparkline`, `RegionFlag` (drapeau pays via flagcdn ; `il`→drapeau Palestine `ps`, `global`→globe).
- Liste de `<SongRow>` : scope `.streams-songs-wrap` (Streams.css) = grid desktop + cartes `.song-row-mobile` ≤600px automatiques (SongRow rend les 2 DOM). Utilisé par Streams, SongDetail streams (`sgd-`, `SongDetail.css`) et AlbumDetail (`albd-`, `AlbumDetail.css`). Jamais de layout mobile maison en `::before`.
- Carte mobile "Marquee" (≤700px) : visuel de `.song-row-mobile*` défini **une fois** en haut de `Streams.css` (cover 70px, padding carte 11px, rang badgé sur la pochette sans #1 spécial, daily en Bebas Neue, total micro-label — densité réduite le 2026-08-31, était cover 82px/padding 13px). Sous le daily, pilule(s) = classe `.song-row-mobile-daily-chip` **obligatoire sur chaque span** (CSS class-based depuis le 2026-08-31, plus de `:last-child` → un span sans la classe perd le style pilule, piège déjà vécu). Pattern 2-pilules (absolu + %) ajouté le 2026-08-31, modifiers `.positive`/`.negative`. **4 consommateurs JSX indépendants à garder en phase** : `SongRow.jsx` (Streams/SongDetail/AlbumDetail), `Streams.jsx` bloc `.streams-album-mobile-card` (widget Top Albums home, markup à la main, `row.streamChange`/`streamChangePct`), `Albums.jsx` page `/albums` (`.album-row-mobile-daily-chip`, `row.change`/`changePct`), `YouTube.jsx` (vues : `dailyChange`/`daily_change_pct`, 1 pilule neutre si `metric.isPeriod`). Le `.delta` de rang est `absolute` en coin haut-droite de la carte (`top:11px; right:11px`), `.delta.neutral` masqué ; best-day = étoile or + `.best-day-mobile-label` micro-capitales grises. Détail : `STYLE.md` §6.
- Admin : primitives de `adminUI.jsx` + patterns `adm-` (→ skill `admin-work`).
- **Nav mobile** (≤900px) : bottom tab bar app-like — `BottomTabBar.jsx`/`.css` (Home/Charts/Players + "More" → bottom sheet `tabbar-sheet-*` pour Eras Gallery/Journalist Department). Top bar réduit à logo + contexte page + rangée d'icônes circulaires assorties `.nav-icon-btn` (Admin/About/Langue/Thème, 28px, même `color-mix`) + bannière CTA glitter en ligne 2 si promo admin active. `LanguagePicker.jsx` (bouton globe + panel `langpick-*`) remplace le `<select>` natif — panel ancré en `left` clampé au viewport (jamais `right`, ça le pousse hors écran si le bouton n'est pas tout à droite). Token `--tsm-tabbar-height` (0 desktop, ~58px+safe-area mobile) consommé par `Footer.css`/`tables.css` pour la clearance du bas de page — plus de footer disclaimer fixe (retiré, le texte légal vit sur `/about`).

## Checklist avant livraison
tokens only · light+dark OK · mobile 600px OK · classes préfixées · modules réutilisés · pas de nouvelle font · `npx vite build` passe · deploy Vercel requis pour voir en prod (→ skill `deploy`).

## Maintenance (obligatoire)
Nouveau token, thème, module partagé, font ou breakpoint → mets à jour `STYLE.md` (et ce condensé) dans la même session.
