# Piste VPS OVH pour les collectors — journal d'essai (2026-07-29)

## Pourquoi cet essai

Le pipeline streams tourne actuellement en local via le Planificateur de
tâches Windows (voir mémoire `tsm-streams-pipeline-ops`). Deux problèmes
distincts ont motivé l'idée d'un VPS :

1. **Connexion internet locale instable selon l'endroit où se trouve Anas** —
   cause racine confirmée d'un mode de panne où Cloudflare WARP reste
   "Connected" mais "Network: unstable", faisant geler
   `fetch_playcount_api` (corrigé côté code le 2026-07-08 dans
   `collectors/spotify/streams/tools/scripts/spotify_api.py`, mais la cause
   locale reste).
2. **WARP existe dans le pipeline pour contourner des 429** — ajouté à
   l'origine parce que l'IP résidentielle d'Anas recevait des erreurs 429
   (rate limit) de l'API Spotify. Ce n'est pas un outil de confidentialité,
   c'est un contournement anti-rate-limit.

Hypothèse testée : un VPS OVH (IP fixe, datacenter) pourrait-il remplacer ou
compléter l'exécution locale pour le collector Spotify streams ?

## Méthode

Script de test autonome (`vps_429_test.py`, pas commité dans le repo — vit en
local dans le scratchpad Claude) qui reproduit l'appel réel utilisé par
`fetch_playcount_api` :

- Récupère un Bearer + client-token Spotify via `open.spotify.com/get_access_token`
  en réutilisant les cookies de session (`spotify_session.json`, le même
  fichier que `_fetch_tokens_via_http` en prod).
- Envoie ensuite N requêtes GraphQL (`api-partner.spotify.com/pathfinder/v2/query`,
  operation `getTrack`) en parallèle (8 workers, comme en prod) sur de vrais
  track IDs Taylor Swift.
- Compte les codes HTTP retournés (200 / 429 / 403 / erreurs réseau).

Infra de test : OVHcloud Public Cloud, projet d'essai "TSM TEST", instances
jetables détruites/recréées à chaque incident.

## Essais et résultats

### Instance #1 — Ubuntu 26.04, IP `57.130.72.33`

- Test sans WARP : **`403 URL Blocked` (Error 54113)** dès l'étape de
  récupération du token (`get_access_token`), avant même d'atteindre la
  logique d'auth/cookies. Confirmé par un `curl` direct : réponse Varnish
  "URL Blocked" — c'est un blocage réseau au niveau du WAF/CDN de Spotify,
  pas un problème de cookies expirés.
- Installation de `cloudflare-warp` via le dépôt apt Cloudflare (repo `noble`,
  fonctionne malgré l'instance en Ubuntu 26.04 "resolute").
- `systemctl enable --now warp-svc` puis `warp-cli connect` : **coupe
  immédiatement l'accès SSH** (la session en cours ET les nouvelles
  connexions). Cause : WARP en mode full-tunnel fait sortir tout le trafic
  par le tunnel, cassant le chemin de retour vers la connexion entrante sur
  l'IP publique (asymétrie de routage).
- Le service WARP étant activé au démarrage (`enable`), un redémarrage à
  chaud puis à froid de l'instance n'a **pas** restauré l'accès — WARP se
  reconnectait tout seul au boot. Instance abandonnée (supprimée).

### Instance #2 — AlmaLinux 10, IP `57.130.72.77`

- Recréée par erreur avec l'image AlmaLinux au lieu d'Ubuntu (le nom
  d'utilisateur SSH n'est donc pas `ubuntu` mais `almalinux`).
- Test sans WARP : **même blocage 403 "URL Blocked"**, confirmant que ce
  n'est pas spécifique à une IP ou une instance précise, mais à la plage
  IP/l'ASN datacenter OVH en général.
- Installation de `cloudflare-warp` (rpm) : **échec**, dépendances GUI
  manquantes (`libappindicator-gtk3`, `webkit2gtk4.1`) non résolues sur cette
  image minimale AlmaLinux 10. Pas creusé plus loin (EPEL, paquets
  alternatifs) — abandon de cette piste OS.

### Instance #3 — Ubuntu, IP `57.130.72.156`

- Test sans WARP : **3e confirmation** du 403 "URL Blocked".
- Cette fois, précautions prises avant de toucher à WARP :
  - Mot de passe local défini sur le compte `ubuntu` (fallback console VNC),
    SSH restant strictement en authentification par clé.
  - `warp-svc` installé mais **désactivé explicitement** au démarrage
    (`systemctl disable warp-svc`), pour que le filet de sécurité "reboot"
    fonctionne cette fois si WARP recasse l'accès.
  - Séquence connect → test → disconnect lancée en tâche détachée
    (`nohup setsid ... & disown`) pour survivre à la coupure SSH attendue.
- Résultat : comme prévu, la connexion WARP a de nouveau coupé tout accès
  SSH entrant (confirmant que c'est inhérent au mode full-tunnel de WARP, pas
  au réglage `systemctl enable`). Après ~5 minutes d'attente, l'accès n'était
  toujours pas restauré → redémarrage à froid demandé et effectué. WARP étant
  désactivé au démarrage cette fois (`systemctl disable warp-svc`), le reboot
  a bien restauré l'accès SSH immédiatement (daemon WARP non relancé,
  confirmé par `warp-cli status` → "Unable to connect to the daemon").

- Log récupéré (`~/warp_test_output.log`), déroulé complet (5 secondes entre
  connect et fin) :
  1. `warp-cli connect` → `Success`.
  2. `warp-cli status` (sans `--accept-tos`) → échoue avec un message ToS —
     bug du script de test, pas un vrai souci réseau.
  3. Test des 429/403 lancé ~5s après le connect → **`403 URL Blocked`, le
     même blocage que sans WARP**.
  4. `warp-cli disconnect` (sans `--accept-tos`) → échoue pour la même raison
     ToS. **C'est ce qui a laissé le tunnel actif pendant les ~5 minutes
     suivantes** (pas un blocage du test lui-même) — le reboot a nettoyé ça.

  Résultat pas encore concluant à ce stade (tunnel peut-être pas stable en
  5s) → retest fait juste après, voir ci-dessous.

### Instance #3 (suite) — retest WARP propre

Script corrigé (`--accept-tos` sur `status`/`disconnect`, polling du statut
jusqu'à `Connected`, puis 15s d'attente supplémentaire avant de lancer le
test) :

```
warp-cli connect                → Success
poll status (1er essai)         → "Status update: Connected / Network: healthy"
+ 15s de stabilisation
status final                    → "Connected / Network: healthy" confirmé
test 429/403 (60 req, 8 workers)→ 403 URL Blocked — IDENTIQUE au test sans WARP
warp-cli disconnect             → "Success" / "Disconnected" proprement
```

Cette fois l'accès SSH n'a **pas** été coupé pendant le test (contrairement
aux essais précédents) — la coupure SSH observée avant semble donc
intermittente/liée au timing, pas garantie à chaque connexion.

**Verdict définitif : même avec un tunnel WARP pleinement établi et
"healthy", Spotify renvoie le même 403 "URL Blocked" qu'sans WARP.** WARP ne
débloque pas l'accès à l'API Spotify depuis une IP de sortie OVH — les IP de
sortie WARP sont vraisemblablement elles-mêmes cataloguées comme trafic
VPN/proxy par le système anti-bot de Spotify, au même titre que l'IP OVH nue.

## Conclusions (à date, incomplet)

Confirmé :

- **Une IP OVH nue est bloquée par le WAF de Spotify** (403, avant même
  l'auth) — reproduit sur 3 IP/instances différentes. Un VPS OVH sans
  solution de contournement réseau ne peut pas parler à l'API Spotify
  utilisée par le collector streams.
- **WARP à travers ce même VPS OVH ne débloque pas l'accès** — testé avec un
  tunnel confirmé "Connected / healthy" et stabilisé 15s, même 403 qu'sans
  WARP. Donc **WARP n'est pas une solution pour faire tourner le collector
  Spotify streams depuis un VPS OVH** — c'est probablement l'IP de sortie
  WARP elle-même qui est cataloguée VPN/proxy par l'anti-bot Spotify.
- WARP en mode full-tunnel a un comportement instable vis-à-vis de l'accès
  SSH entrant sur la même IP publique — coupure observée sur 2 essais sur 3,
  pas systématique mais un risque réel à connaître si WARP est utilisé sur
  une machine gérée à distance.

**Conclusion pratique : un VPS OVH classique n'est pas une option viable
pour le collector Spotify streams (WARP inclus) tel que testé.** Le blocage
vient du WAF de Spotify qui reconnaît/filtre les plages IP hosting/VPN
connues (OVH ET les sorties WARP), pas d'un problème de configuration
réseau récupérable côté script.

## Test des autres collectors (même instance #3, IP `57.130.72.156`)

Après le verdict négatif sur Spotify, test rapide des autres collectors
depuis la même IP OVH, pour voir s'ils sont concernés par le même type de
blocage anti-bot :

### YouTube Data API

Reproduit `fetch_video_stats()` de `collectors/youtube/core/api.py` (clé
`YOUTUBE_API_KEY` réelle, 3 vidéos officielles Taylor Swift) :

```
HTTP 200 — vues recuperees normalement pour les 3 videos test
```

**Aucun blocage.** Logique : c'est une API Google officielle authentifiée
par clé, pas soumise au même filtrage anti-bot IP qu'un site scrapé.

### Apple Music (MusicKit)

Reproduit `fetch_musickit_token()` + `fetch_top_songs()` de
`collectors/apple_music/core/token.py` et `ts_page.py` (page publique
`music.apple.com/fr/new` → extraction JWT → appel
`amp-api-edge.music.apple.com`) :

```
GET music.apple.com/fr/new              → HTTP 200
GET .../assets/index~*.js               → HTTP 200
Token MusicKit extrait                  → OK
GET amp-api-edge.../top-songs           → HTTP 200, 10 morceaux recus
```

**Aucun blocage.** Le token public MusicKit s'extrait et l'API catalogue
répond normalement depuis l'IP OVH.

### Billboard

Pas testé — décision explicite de ne pas tester (`scrape_billboard.py`
tourne juste après Spotify streams dans le pipeline local, pas prioritaire).

### Spotify Charts (confirmation du blocage, 2026-07-30)

Testé séparément de Streams, sur la même instance (`57.130.72.156`) :
`collectors/spotify/charts/run_all_charts.py --date 2026-07-28 --no-warp
--no-post --force`.

- Premier essai : interrompu par une coupure SSH transitoire (process resté
  orphelin côté serveur, tué manuellement).
- Deuxième essai, en tâche détachée avec logs : **aucune sortie loggée
  pendant 39 minutes** — piège de buffering stdout Python (bufferisé par
  bloc quand la sortie va vers un fichier, pas un TTY), pas un vrai freeze.
  `strace` sur le process a montré une vraie activité réseau répétée : DNS
  + handshake TLS complet vers `open.spotify.com`, résolu sur une IP
  **Fastly** (`151.101.207.42`) — le même CDN qui avait donné le 403 "URL
  Blocked" sur le test Streams.
- Troisième essai avec `python3 -u` (stdout non bufferisé) + suivi live via
  `tail -F` : log clair, **`[CHECK] token indisponible (Bearer token
  introuvable via HTTP direct)`, retry toutes les 10s, boucle infinie**
  (17 tentatives observées avant arrêt manuel). Même mécanisme que Streams :
  la récupération HTTP directe du bearer token via cookies échoue depuis
  l'IP OVH.

**Verdict : Spotify Charts est aussi bloqué depuis une IP OVH**, pas
seulement Streams — cohérent avec un blocage au niveau du WAF/CDN Fastly
sur les propriétés Spotify en général (`open.spotify.com` en tout cas),
pas spécifique à un seul endpoint API.

**Conclusion intermédiaire : YouTube et Apple Music fonctionnent sans souci
depuis un VPS OVH. Spotify (streams + charts) est bloqué, confirmé sur les
deux collectors désormais.**
Un VPS OVH est donc une option viable dès maintenant pour déporter les
collectors YouTube et Apple Music du Planificateur de tâches local, même si
Spotify doit rester en local (ou attendre une solution de proxy résidentiel).

## Pistes pour la suite

- **Abandonner la piste VPS OVH + WARP pour le collector Spotify streams** —
  testé et invalidé (IP OVH nue bloquée, IP de sortie WARP bloquée aussi).
- Si l'option VPS reste désirée pour Spotify, il faudrait une IP de sortie
  **résidentielle** (proxy résidentiel dédié/rotatif, pas un VPN grand public
  type WARP) — non testé ici, coût et fiabilité à évaluer séparément.
- Le blocage 403 ne concerne à ce stade que Spotify (streams + charts, qui
  utilisent tous les deux WARP comme fallback réseau). Les collectors
  YouTube, Apple Music et Billboard ne référencent pas WARP dans leur code —
  un VPS OVH pourrait rester utile pour ces collectors-là, qui n'ont pas ce
  problème de blocage anti-bot documenté. À tester séparément si l'idée d'un
  VPS reste intéressante pour alléger le Planificateur de tâches local.
- Le vrai problème d'origine (connexion internet locale instable selon
  l'endroit) reste entier pour Spotify streams — la piste VPS ne le résout
  pas. Revenir à une solution locale (stabiliser la connexion, ou accepter
  les retries/`manual_trusted` existants pour les jours ratés, voir
  [[tsm-streams-pipeline-ops]]) reste l'option la plus réaliste à court terme.

## Déploiement réel — YouTube + Apple Music sur VPS OVH (2026-07-30)

Suite aux tests concluants (aucun blocage), YouTube et Apple Music ont été
migrés en prod sur l'instance OVH (`57.130.72.156`, Ubuntu, Europe/Paris).

Setup :

- Deploy Key GitHub dédiée (`tsm-vps-deploy`, write access, scopée à ce
  repo) pour cloner + push depuis le VPS sans exposer la clé perso d'Anas.
- Repo cloné dans `~/tsm-backend`, venv Python avec seulement les deps
  nécessaires à ces deux collectors + Playwright/Chromium (requis par
  `generate_country_card_images.py` côté Apple Music).
- `.env` réduit aux clés réellement utilisées : `YOUTUBE_API_KEY`,
  `UPLOAD_TO_R2`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
  `R2_BUCKET`.
- Fuseau horaire VPS réglé sur `Europe/Paris`.
- Validation en deux temps avant bascule réelle, pour ne rien dupliquer tant
  que les tâches Windows tournaient encore :
  1. YouTube en `--dry-run` (vraie API, aucune écriture) → 661 vidéos, stats
     réelles reçues.
  2. Apple Music en sandbox (`TSM_DATA_DATE=2020-01-01`,
     `APPLE_MUSIC_SKIP_EXPORT=1`, `UPLOAD_TO_R2=0`) → données réelles reçues
     (US 2 songs/7 albums/5 videos, FR 0/1/1), écrites dans un dossier
     snapshot isolé puis nettoyées.
- Une fois les deux tâches Windows Task Scheduler désactivées par Anas : run
  réel des deux collectors sur le VPS.
  - YouTube : a détecté que la donnée du jour existait déjà (job Windows
    déjà passé le matin même) → skip automatique, aucun doublon écrit. La
    première vraie écriture/push git depuis le VPS se fera au prochain cron
    (06:05 Europe/Paris le lendemain).
  - Apple Music : run complet réussi de bout en bout (4 sous-scripts, export,
    génération d'images via Chromium, upload R2 — 728 fichiers
    `history-by-song` uploadés). Aucune erreur.
- Détail des scripts wrapper et du crontab final : voir
  `REPO_CONTEXT.md` section « Déploiement VPS OVH ».

**Statut : YouTube et Apple Music tournent officiellement en prod sur le VPS
OVH depuis le 2026-07-30, en remplacement du Planificateur de tâches
Windows pour ces deux collectors.** Spotify (streams + charts) et Billboard
restent en local.

## Incident — Apple Music a publié des NEW faux le premier run VPS (2026-07-30)

**Cause** : `.gitignore` exclut **tous** les `.csv` du repo (`*.csv`), et
seul `db/**/*.json` est ré-inclus — pas `db/**/*.csv`. Résultat : `git clone`
sur le VPS n'a récupéré ni `db/apple_music_*.csv` ni l'historique quotidien
`snapshots/apple_music_charts/YYYY/MM/YYYY-MM-DD/*.csv` (tout gitignored,
jamais commité — contrairement à YouTube, qui force `git add -f` sur ses CSV
dans `git_ops.py`). Le premier run Apple Music sur le VPS n'avait donc
**aucun historique de la veille** pour calculer `previous_rank` →
`load_previous_ranks`/`rewrite_for_snapshot` ont traité tout le catalogue
comme sans précédent, publiant des NEW faux sur ~728 fichiers R2
(`history-by-song/`) + les exports globaux. Violation directe de la règle
data-rules « Apple Music : jamais de NEW pour une chanson déjà sortie ».

**Détection** : repérée par Anas directement sur le chart global affiché
(NEW partout), pas par une alerte automatique — **aucun garde-fou dans le
code n'empêche ce cas** (contrairement à Streams où un historique manquant
bloque plutôt qu'il n'invente une donnée).

**Correctif appliqué** :
1. Cron Apple Music coupé immédiatement sur le VPS (éviter de répéter
   l'erreur au créneau de 18h).
2. Historique réel (`snapshots/apple_music_charts/2026/06` +
   `2026/07`, sans les sous-dossiers images, + `db/apple_music_*.csv`)
   packagé en tar.gz (28 Mo compressés) depuis la machine Windows, transféré
   et extrait sur le VPS.
3. Apple Music relancé pour de vrai sur le VPS avec l'historique en place →
   `previous_rank` correctement rempli (vérifié sur
   `apple_music_global.csv` : plus de vide/NEW, vrais rangs précédents).
   Cette re-publication a écrasé les mauvaises données sur R2.
4. Cron Apple Music réactivé.

**Point d'attention structurel** : `snapshots/` (l'historique quotidien brut,
utilisé par Streams ET Apple Music pour le contexte veille/gap) n'est
**versionné nulle part** — ni dans git (gitignored), ni synchronisé
automatiquement entre le Windows local et le VPS. C'était un backfill
ponctuel, pas une synchro continue : comme Apple Music tourne désormais
exclusivement sur le VPS, il va accumuler son propre historique correctement
à partir de maintenant (chaque run devient le "previous day" du suivant).
Le risque ne peut réapparaître que si le VPS est recréé/redéployé sans
répéter ce backfill — **toute recréation de l'instance VPS doit repartir de
ce même transfert d'historique avant le premier run réel**, pas juste un
`git clone`.

## Décommissionnement du VPS (2026-08-17)

**Le VPS OVH (instance `57.130.72.156`, projet "TSM TEST") a été supprimé et
la résiliation demandée le 2026-08-17**, décision d'Anas après avoir constaté
le coût réel (`b3-16` ≈ 0,1023 €/h ≈ 74 €/mois en continu, jugé disproportionné
pour 2 crons légers qui tournent quelques minutes toutes les 4h/24h). Aucune
tentative de downsize (`b3-8` ou plus petit) n'a été faite avant la
suppression — l'instance est déjà détruite.

**Retour à l'état pré-VPS** :

- Les deux tâches Windows Task Scheduler (`TSM Apple Music Every 4 Hours`,
  `TSM YouTube Videos Daily`) ont été réactivées (elles n'avaient jamais été
  supprimées, juste désactivées le 2026-07-30).
- YouTube : aucune perte — le VPS committait/pushait ses CSV sur `main` à
  chaque run (contrairement à Apple Music), donc `git pull` en local a
  suffi à rattraper tout l'historique du 30/07 au 17/08 (commits
  `youtube views YYYY-MM-DD`).
- Apple Music : les CSV ne sont jamais commités (gitignored, ni en local ni
  sur le VPS). Historique du 30/07 au 17/08 reconstruit depuis R2
  (`apple-music/snapshots/`, jamais supprimé) via
  `scripts/sync_apple_music_snapshots_from_r2.py --start 2026-07-30 --end
  2026-08-17 --force --apply`, pour que `previous_rank` ne reparte pas de
  zéro au premier run local (même incident que le 30/07, sens inverse).
- **Découverte en passant** : `.github/workflows/run-apple-music.yml` avait
  gardé son trigger `schedule` actif tout du long malgré le commentaire du
  crontab VPS ("équiv. ancien run-apple-music.yml") qui indiquait qu'il
  était censé être remplacé — il tournait donc potentiellement en parallèle
  du VPS depuis le 30/07, risque de race sur les mêmes clés R2. Désactivé
  (schedule commenté, `workflow_dispatch` gardé pour lancement manuel) en
  même temps que la réactivation du local.

**Conclusion** : la piste VPS pour YouTube/Apple Music est abandonnée pour
raison de coût (pas technique — les deux collectors fonctionnaient très bien
depuis l'IP OVH, voir tests plus haut). Si l'idée revient un jour, viser un
flavor plus petit (`b3-8` ou une ligne moins chère) dès le départ plutôt que
`b3-16`.
