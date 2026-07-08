---
name: deploy
description: Déployer le site TSM (frontend + API) sur Vercel — workflow, vérifs avant/après, différences prod/local (buckets R2). À utiliser quand un changement dans tsm-frontend doit partir en prod, ou quand l'utilisateur demande « déploie » / « c'est en prod ? ».
---

# Déploiement TSM

## Le principe
- **Rien n'est visible en prod tant que ce n'est pas déployé.** Toujours le rappeler à la fin d'une feature.
- Déploiement = commit + push sur `main` de `AnasSfar/tsm-frontend` → Vercel build automatiquement. **Front et API partent ensemble** (même repo : build `tasks/vercel-build.sh` → `public/`, fonction serverless `api/index.py`, config dans `vercel.ts`).
- `tsm-backend` ne se déploie pas : il tourne en local (Task Scheduler) et push ses données vers R2 ; son `git push` ne sert qu'à versionner.

## Avant de pousser
```powershell
cd c:\Users\sfara\Documents\GitHub\tsm-frontend\frontend
npm run check   # hygiene + vite build ; a minima: npx vite build
```
- PowerShell 5.1 affiche le stderr de vite en `NativeCommandError` — c'est du bruit : le build est bon si « ✓ built in X.XXs » apparaît.
- Ne jamais committer/pusher sans demande explicite de l'utilisateur (règle générale git).

## Pousser
```powershell
cd c:\Users\sfara\Documents\GitHub\tsm-frontend
git add <fichiers précis> ; git commit -m "..." ; git push
```
Vercel déclenche le build sur le push (ignore-command : `tasks/vercel-ignore.sh`).

## Prod vs local — piège R2
| | Bucket R2 | Conséquence |
|---|---|---|
| Prod (Vercel) | `taylor-data` (`R2_APP_BUCKET` non défini) | settings/site réels |
| Local (`.env`) | `taylor-app` | les settings et uploads faits en local ne sont PAS ceux de prod |

Donc : tester un setting en local ne prouve pas l'état prod ; les réglages admin de prod se font sur le site déployé.

## Après le deploy
1. Ouvrir le site : la feature est visible ? (hard refresh si assets cachés — `/assets/` est immutable 1 an, mais les noms sont hashés).
2. Si changement API : tester l'endpoint concerné (ex. PATCH settings depuis /admin avec le token).
3. Si changement de settings normalisés : vérifier qu'un GET renvoie la nouvelle shape.

## Maintenance (obligatoire)
Si le workflow de deploy change (build, buckets, config Vercel), mets cette skill à jour dans la même session.
