# Contexte Collector Apple Music

## Role

`collectors/apple_music` collecte les charts Apple Music Taylor Swift:

- global songs;
- Taylor Swift artist page;
- country songs/albums/music-videos via `country_all.py`;
- genre songs/albums via `genre_all.py`;
- images de snapshots et cards pays;
- export JSON puis upload R2.

Le pipeline coeur (`run_apple_music.py`) ne poste pas sur X. `--no-post` existe
dans le runner mais n'est pas un controle de publication Twitter. Ne fait
jamais de commit/push git (seul l'upload R2 distribue la donnee).
**Exception depuis le 2026-09-24** : `post_new_release_progression.py`, lance
en fin de chaque chaine (`--platform apple_music` : depuis le 2026-09-25 demarre PAR
`run_apple_music.py` des que `global.py` + `country_all.py` ont fini (`POST_INPUTS`, sans attendre
genre_all / ts_page_all), en parallele du reste et de export/upload/images
— posts ~HH:02-03 au lieu de ~HH:18 ; plus de ligne dans `run_apple_music.bat`,
`--platform itunes` dans `collectors/itunes/run_itunes.bat`), poste sur X — voir section « Progression horaire des
nouvelles sorties » plus bas.

Scheduler : prod tourne **en local via le Planificateur de taches Windows**
(`TSM Apple Music Every 4 Hours` — nom historique desormais trompeur, action =
`run_apple_music_hidden.vbs` -> `run_apple_music.bat`, repeat **toutes les
heures depuis le 2026-09-24**, avant ca 2h). Ni GitHub Actions ni VPS.
**`build_scraped_at()` arrondit toujours au dernier creneau de
`APPLE_MUSIC_SNAPSHOT_HOURS` (defaut code = heures paires seulement)** — le
simple changement de cadence du Planificateur ne suffisait pas : sans
override, les runs des heures impaires auraient juste re-arrondi vers le
creneau pair precedent (pas de nouvelle donnee, juste une recollecte gaspillee
sur l'API Apple). Fix applique le 2026-09-24 : `.env` fixe
`APPLE_MUSIC_SNAPSHOT_HOURS=0,1,...,23` (et `ITUNES_SNAPSHOT_HOURS` pareil,
cf. skill `collector-itunes`) pour que chaque heure produise reellement son
propre snapshot.
**Garde-fous des 2 collecteurs (2026-09-25, recap)** :
- verrou mono-instance + heure sautee (sortie 75) + alertes ntfy sur tout echec : `collectors/spotify/core/run_guard.py` ;
- sortie **3** = echec deja alerte par Python ; tout AUTRE code non nul fait alerter le `.bat` lui-meme via
  `%SystemRoot%\System32\curl.exe` -> ntfy (crash avant que Python puisse alerter : import, interpreteur) ;
  teste : 0/3/75 -> rien, 1/9009 -> alerte ;
- iTunes : feed US/UK (`ITUNES_CRITICAL_STOREFRONTS`) de moins de `ITUNES_MIN_CRITICAL_FEED_ENTRIES` (50)
  entrees = echec (reessai puis abandon + alerte), jamais publie comme « Taylor absente du chart » ;
- **Toujours reessayer (proprio 2026-09-25 : « si quelque chose echoue on reessaie toujours »)** :
  `run_guard.retry_step` (3 essais, `RUN_RETRY_ATTEMPTS`) sur collecte iTunes / export / upload des 2
  collecteurs ; Apple Music relance chaque collecteur bloquant en echec (et relance les posts si c'etait
  global.py/country_all.py) ; posts X via `post_with_retries` (3 essais, 30 s, `APPLE_MUSIC_DEBUT_POST_ATTEMPTS`
  / `_RETRY_WAIT`) ; guet : fetch express 5 essais, collecte complete 3 essais. L'alerte ne part qu'apres le
  dernier echec. **Exception** : post « non confirme apres clic » = peut-etre en ligne -> JAMAIS repost
  (doublon), compte comme poste + alerte haute « verifie le compte ».
- posts contenant un DEBUT : priorite 0 et attente du slot X jusqu'a 15 min (`APPLE_MUSIC_DEBUT_FIRST_POST_*`) ;
- controle pre-sortie (`release_watch.preflight`, a l'ouverture de la fenetre de guet) : collectes < 90 min,
  feed iTunes US + API lookup joignables, > 2 Go libres, profil Chrome X present -> UNE notif « armed » (basse)
  ou la liste des problemes (haute). La connexion X elle-meme n'est pas verifiable sans navigateur : un post
  rate reste alerte + retente.
**Garde-fous 2026-09-25** (`collectors/spotify/core/run_guard.py`, partage avec iTunes) : verrou
mono-instance `tools/locks/run_apple_music.lock` (PID vivant = heure sautee + alerte, sortie 75 ; jamais
vole sur l'age : un run endormi par la veille reste proprietaire), alertes ntfy sur echec des collecteurs
bloquants / export / upload R2 / crash, et la notification Global en echec ne bloque PLUS l'upload R2
(avant : `sys.exit(1)` = heure absente du site). Le runner attend la fin des posts avant de rendre le verrou.
**Depuis le 2026-09-24 (soir, veille de The Encore) : iTunes tourne EN PARALLELE d'Apple Music**, plus apres — demande proprietaire « updated independently, whoever is ready first is posted first ». `run_apple_music.bat` lance d'abord `start "" /b cmd /d /c "<chemin absolu>\collectors\itunes
un_itunes.bat"` (chaine iTunes = `run_itunes.py` puis `post_new_release_progression.py --platform itunes`, log de post `collectors/apple_music/post_new_release_progression_itunes.log`), puis Apple Music puis `post_new_release_progression.py --platform apple_music` (log `post_new_release_progression.log`). Les posts iTunes partent ~HH:03 au lieu de ~HH:20. **Piege** : avec un chemin relatif, le cmd enfant ne trouvait pas le `.bat` (teste) -> chemin absolu + `/d` obligatoires. Verifie : la chaine iTunes continue si le `.bat` parent se termine avant. Course sans risque : cache du jeton MusicKit ecrit atomiquement (`core/token.py`), `resolve_storefronts` a un fallback fixe, `export_itunes.py` ecrit `itunes.json` en tmp + `os.replace` (lu par `generate_home_highlights.py` cote Apple Music).
Historique : depuis le 2026-09-09, `run_apple_music.bat` lancait `collectors/itunes/run_itunes.py`
juste apres Apple Music (charts d'achats iTunes Store — collecteur separe, skill
`collector-itunes`, meme cadence, log `collectors/itunes/run_itunes.log`,
tourne quel que soit le code de sortie d'Apple Music). Ne pas casser la 2e
ligne du `.bat` en modifiant la 1re. **Les deux appels python du `.bat`
utilisent `-u` (stdout non bufferise) depuis le 2026-09-24** — sans ca, le
log paraissait fige/mort pendant tout le run (10-20 min de silence), meme
piege deja rencontre et corrige sur Spotify Streams (skill `pipeline-ops`).
**Diagnostic "export R2 manquant" (2026-09-24)** : verifie sur R2 (pas
seulement le log local) que le run de minuit uploade bien chaque jour
(`apple-music/snapshots/<date>T00:00:00+02:00.json` present sur toutes les
~2 dernieres semaines a la date de cette note) — le vrai probleme observe
etait des trous eparpilles (~1-3 creneaux/jour sur 12, dus a des cycles qui
debordent sur le creneau suivant + `MultipleInstances=IgnoreNew` qui saute
alors silencieusement le trigger suivant), pas un trou systematique a
minuit. **Correction 2026-09-25 (audit)** : IgnoreNew ne s'applique en fait JAMAIS — `run_apple_music_hidden.vbs` lance le `.bat` sans attendre (`shell.Run ..., 0, False`), la tache se termine en ~1 s ; le Planificateur ne voit donc ni chevauchement ni blocage, et les runs horaires peuvent se chevaucher (constate le 24 a 21:24). Les trous observes venaient surtout de la mise en veille du laptop (couvercle). Garde-fou iTunes : verrou mono-instance dans `run_itunes.py` (voir skill `collector-itunes`). Apple Music : pas encore de verrou. Si un site parait ne pas avoir la derniere donnee juste apres
l'heure pile, le run est probablement juste encore en cours (bufferise avant
le fix ci-dessus) plutot que casse. Pour verifier directement sans attendre
le prochain run visible sur le site : lister `apple-music/snapshots/` /
`itunes/snapshots/` sur R2 via `core/r2.py::get_r2_client()` +
`list_objects_v2` pagine (`ContinuationToken`, >1000 objets desormais).
Tente sur GitHub Actions le 2026-08-28 (`run-data-only-collectors.yml` +
`scripts/ci_data_collector_gate.py`), re-bascule en local le 2026-08-29 :
le `schedule:` natif de GitHub est trop peu fiable pour une cadence 2h
(retarde au top de l'heure, runs droppes sous charge). Les workflows
`run-apple-music.yml` ET `run-data-only-collectors.yml` restent
`workflow_dispatch` manuel + `disabled` cote GitHub, comme escape hatch de
rattrapage quand le PC est eteint (utilise `ci_data_collector_gate.py` pour
router les inputs). Historique VPS OVH (2026-07-30 -> 2026-08-17) :
`REPO_CONTEXT.md` section « Deploiement VPS OVH » et `OVH.md`.

## Progression horaire des nouvelles sorties (`post_new_release_progression.py`, ajoute 2026-09-24)

Cas d'usage : 4 titres inedits (Patient Zero, Cleveland!, Pink Clouding,
Babylon — The Encore, `db/discography/albums/the_life_of_a_showgirl.json`,
`release_date: 2026-09-25`) sans historique de la veille -> les marqueurs
jour-a-jour habituels (`core/csv_utils.py::load_previous_ranks`, toujours
« vs hier ») ne peuvent rien afficher le jour de sortie. Ce script maintient
son **propre** etat « dernier rang vu » par (titre, chart, pays) au lieu de
reutiliser le champ `previous_rank` des CSV (dont la semantique differe deja
entre Global — vs hier — et pays/iTunes — vs le cycle precedent) :

- **Refonte nuit du 2026-09-24 (veille de The Encore, suite a 2 audits)** —
  remplace les regles ci-dessous la ou elles divergent :
  - **Sortie detectee automatiquement** (proprio : « the debut is the first time
    they appear at the charts or are able to be bought ») : `release_date` du
    catalogue ne rend un titre que *candidat* (48 h avant -> fenetre + 48 h apres).
    La fenetre de 72 h demarre au 1er cycle ou le lookup public iTunes
    (`itunes.apple.com/lookup?id=...&country=<pays cles>`) dit `isStreamable` ou
    donne un `trackPrice`, ou ou le titre apparait sur un chart. Memorise dans
    l'etat (`__released_at|<cle>`). Apple injoignable ET pas d'id connu -> repli
    `release_date + APPLE_MUSIC_DEBUT_FALLBACK_HOURS` (defaut 6). Le `releaseDate`
    d'Apple sur ces lignes est faux (09-24T07:00Z) -> jamais utilise.
  - **Matching par id Apple OU titre** : `KNOWN_APPLE_IDS` (ids iTunes des 4 titres
    Encore, lookup de l'album 6814997249). Les ids Apple Music et iTunes d'un meme
    titre peuvent differer (Ophelia 1833328840 vs 6814997402) et les editions
    clean/explicit ont chacune le leur -> le titre reste en repli ; meilleur rang
    retenu quand les 2 editions chartent.
  - **Etat = dernier rang POSTE** : mis a jour seulement apres un post reussi ->
    un post rate (slot X occupe, echec navigateur) est retente au cycle suivant
    (avant : etat sauve avant le post = debut perdu). `NEW` = jamais poste sur ce
    chart -> plus de crash `delta=None`.
  - **`--dry-run`** (texte seulement) et **`--no-post`** (card rendue) n'ecrivent
    plus rien (ni etat ni lock) — avant ils mangeaient les vrais posts.
  - **Volume** (`post_due`, revu 2026-09-25 jour J : le chart iTunes est LIVE, il bouge
    chaque minute) : debut sur un chart cle ou nouveau #1 = poste immediatement ;
    autre nouveau peak (quel que soit le rang, meme si tous les autres ont baisse) =
    immediatement, sauf si ce titre a ete poste il y a moins de
    `APPLE_MUSIC_DEBUT_PEAK_GAP_MINUTES` (15) -> attend, et le post suivant porte tous les
    peaks atteints entre-temps (un post au lieu de #5->#4->#3 en trois) ; sinon
    une montee au plus toutes les `APPLE_MUSIC_DEBUT_MIN_GAP_HOURS` (defaut 3) par
    (titre, plateforme) ; baisses seules jamais postees sauf
    `APPLE_MUSIC_DEBUT_POST_DROPS=1`.
  - **Tweet** (`build_tweet_text`, proposition 2026-09-24 en attente de
    validation proprio) : emoji d'album (`album_emoji` -> ❤️‍🔥) au lieu de 🎧, rang
    reel du meilleur evenement en tete, autres marches groupes par rang, puis sur
    CHAQUE tweet une ligne mondiale `🌍 Now #1 in N countries, top 10 in M and charting
    in K on <plateforme> worldwide.` sur TOUS les storefronts du CSV (paliers egaux
    omis, rien si < 2 pays) — valide proprio 2026-09-25. Jamais de card mono-pays :
    la card liste toujours tous les charts cles du titre. Ex. :
    `❤️‍🔥 | "Babylon" debuts at #1 on iTunes in the US — also #1 in the UK & France, #2 in Canada, #3 in Australia.`
  - **Card album Global** : seulement si un titre Encore est deja sur le Global
    (sinon elle postait une card d'anciens titres entre 02:00 et la vraie sortie).
    Tweet : `❤️‍🔥 | "<album affiche>" songs on the Global Apple Music chart right now:`.
  - **Isolation + alertes ntfy** (`NTFY_TOPIC_APPLE_MUSIC`) : chaque titre / la card
    album en try/except ; alerte sur crash, post rate, « sortie detectee » (chaine
    iTunes), et titre sorti depuis >= 2 h mais absent de tous les charts iTunes cles.
  - **Ajouts 2026-09-25 (proprio : « ajoute ce qui n'y figure pas »)** :
    - **Card album iTunes** (chaine iTunes) : l'edition (`KNOWN_ALBUM_IDS`, cle = titre
      affiche, ids explicit 6814997249 + clean 6814995859, repli titre exact — une
      ancienne edition « The Life of a Showgirl » ne matche jamais) sur le Top Albums
      iTunes des pays cles (sources `kind="album"`, `itunes_top_albums.csv`), memes
      regles que les titres (debut / #1 / peak immediat, sinon 3 h, pas de baisse
      seule), ligne mondiale sur le Top Albums. Tweet :
      `❤️‍🔥 | "The Life of a Showgirl: The Encore" debuts at #1 on the iTunes albums chart in the US — also ...`
    - **Card Global normale Apple Music** (chaine Apple Music, `_post_global_snapshot`) :
      tous les titres de Taylor sur le Global (`generate_snapshot_images --region global`)
      postee a chaque changement du Global pendant la fenetre, AVANT la card album
      Global. 1er passage = baseline sans post (`global_snapshot|global`). Tweet :
      `🌍 | Taylor Swift songs on the Global Apple Music chart right now:`.
    - **Card Pop par marche cle (proprio 2026-09-25, `_post_pop_snapshot`)** : les charts
      genre n'etaient suivis par AUCUN post (US Pop #2, CA/AU Pop #1 le jour de l'Encore,
      rien poste). Post SEPARE (choix proprio, pas des lignes dans la card par titre), une
      card par pays = `generate_snapshot_images --region <cc> --genre Pop` (meme card que
      le Global). Pays : `APPLE_MUSIC_DEBUT_POP_COUNTRIES` (defaut = marches cles). Ne
      poste QUE sur un evenement fort d'un titre en fenetre (regle validee proprio) : #1,
      debut dans le top 10, entree dans le top 10, nouveau peak dans le top 10. Tout
      mouvement hors top 10 (UK/FR #41-#199) = rien. Etat `pop|<titre>|<cc>` = {rank, peak}
      du dernier cycle vu ; avance sans post quand pas d'evenement, seulement apres un
      post reussi sinon (retry au cycle suivant) ; lock `pop_snapshot_<cc>_<scraped_at>`.
      Tweet (prefixe = drapeau du pays depuis 2026-09-25, `_flag_emoji`, emoji album en repli) : `🇨🇦 | "Patient Zero" is now #1 on the Apple Music Pop chart in Canada!` /
      `debuts at #2 ... in the US.` / `hits a new peak of #N` / `enters the top 10 ... at #N`,
      puis `Also: "X" #3, ...`, `Taylor Swift songs on the Apple Music Pop chart in <pays>
      right now:` + lien amcharts.
    - **Thread des chansons (proprio 2026-09-25)** : quand >= 2 chansons sont a poster dans
      un meme cycle d'une plateforme, elles partent en UN thread natif
      (`_post_group` -> `post_image_thread`, chaque post avec sa card) : 1er post =
      `🧵 | "<album>"'s songs on the Apple Music charts.` (ou iTunes) + le tweet de la
      meilleure chanson (lien retire d'abord, puis le « — also ... » si > 275), replies = les
      autres chansons. **Des qu'une chanson declenche un post, le thread embarque TOUTES les
      chansons en fenetre** (proprio : « since it's a thread every song should go in ») — celles
      qui n'ont pas bouge / sont retenues par `post_due` y vont avec leur rang actuel
      (`is now #N`, jamais « hits »), lock par chanson ignore pour elles (lock du thread), et
      tout le thread est trie par importance (le 1er post = la meilleure chanson, pas forcement
      celle qui a declenche). Jamais de thread fait uniquement de chansons immobiles.
      Album card, Global, Pop, carte album iTunes restent des posts
      independants. Une seule chanson = post normal. X non confirme apres le clic
      (`thread non confirme apres clic`) = jamais reposte (meme regle que post_with_image).
    - **Card album Apple Music (proprio 2026-09-25)** : sources `am_albums_<cc>`
      (`apple_music_country_albums.csv`), meme card/texte que la card album iTunes
      (« hits #1 on the Apple Music albums chart in the US — also ... »).
    - **NEW = vraiment absent avant** : sans etat pour un chart, le rang precedent vient du
      dernier cycle reel du CSV (`_previous_cycle_rank`) ; l'album Encore etait deja classe
      des 00:00 -> la 1re preview marquait tout « NEW » (faux). Un chart jamais poste compte
      comme `moved` (1er post meme si rang inchange vs le cycle CSV precedent).
    - **Drapeau en prefixe des posts mono-region (proprio 2026-09-25)** : card Pop par pays et
      card album iTunes par pays -> `🇺🇸 |` au lieu de l'emoji album. `_flag_emoji(cc)` =
      2 indicateurs regionaux depuis le code storefront (`gb` -> 🇬🇧) ; code pas a 2 lettres ->
      emoji album. Windows affiche ces drapeaux en lettres « US » (pas de police drapeau) : c'est
      l'affichage local, X rend le drapeau — ne pas « corriger ». Nom de pays dans le texte =
      nom complet (`country_label`), jamais le code (« in DE »). Posts multi-regions (cards
      par titre) et Global (🌍) inchanges.
    - **5 marches cles surlignes sur les cards (proprio 2026-09-25)** : ligne teintee accent +
      barre a gauche + ★ (`tr.oct-key`, `KEY_COUNTRIES`).
    - Au debut : iTunes = 4 titres + 1 album ; Apple Music = 4 titres + card Global +
      card album Global (quand le Global se met a jour).
    - Garde-fou longueur : poids X (lien = 23, emoji = 2), extras retires au-dela de 275.
      **Et** longueur brute <= 275 : `twitter.py::_validate_tweet_lengths` refuse tout texte
      > 280 caracteres BRUTS (sans ponderation du lien) — le tweet album Encore (283 bruts,
      ~265 pour X) a echoue 3x le 2026-09-25 a 15h. Le plus strict des deux decide.
  - **Toutes les regions sur la card (proprio 2026-09-25)** : la card liste TOUS les pays ou le titre
    est classe (iTunes : jusqu'a ~170 ; Apple Music : Global + tous les pays), comme la share image du
    site — `build_sources` prend tous les pays du CSV du jour. Seuls les marches cles (+ Global,
    `"key": True`) DECLENCHENT un post et nourrissent le texte ; les autres sont dans la card et la
    ligne mondiale. Noms de pays : `core/card_theme.py::COUNTRY_NAMES` complet (175). **Storefront `il` = « Occupied Palestine » + drapeau de la
- **Incident 2026-09-25 10:00 (corrigé)** : la chaîne Apple Music croyait l'Encore « pas encore sorti » (API lookup Apple encore `isStreamable=false`/sans prix 5 h après la sortie, titres absents de tous les charts AM car pas encore de streams comptés) → aucun titre en fenêtre → **card Global jamais postée**. Correctifs : (1) `resolve_released_at` reprend la sortie détectée par l'autre chaîne (état `__released_at|<key>` des deux fichiers d'état) ; (2) 1er passage Global = comparaison au cycle Global précédent du CSV (`_previous_global_signature`, sinon la veille) → posté s'il diffère, au lieu d'une simple baseline muette. Ne jamais se fier à l'API lookup seule pour « sorti ».
- **Ordre des pays à rang égal** (cards + texte des tweets de `post_new_release_progression`, proprio 2026-09-25) : comme le site (`tsm-frontend/frontend/src/utils/marketWeights.js`) — plus gros store d'abord selon `AM_MARKET_WEIGHTS` (importé de `ts_page_all.MARKET_WEIGHTS`, table identique à TayBoard et au frontend, 36 pays, défaut 0.08), puis code pays ; Global toujours en tête (`_market_order`). Si la table change, la changer aux 3 endroits.
    Palestine (`ps`)** partout (convention du site : `i18n.js::regionLabel`, `RegionFlag.jsx`) — jamais « Israel ». Perf : `_index`
    (lecture unique par CSV, cles de correspondance precalculees, lignes par pays) ; drapeaux en cache
    disque `tools/cache/flags/` (gitignore). Mise en page (`_column_count`, choisie par le proprio sur
    des rendus reels) : 1 col <= 20 lignes, 2 cols 21-60, 3 cols au-dela (~30 lignes max par colonne,
    jamais 4 : 168 lignes = 3x56, encore ~portrait) ; largeurs `CARD_WIDTH_BY_COLS` 780/1000/1500 ;
    rendu x2 au-dela de 24 lignes. Reference : 59 lignes = 2000x2330, 71 = 3000x1944, 168 = 3000x4004
    (~1 Mo). Variantes : `previews_and_sims/apple-music-debut-progression/card_sizes.py` / `card_rule.py`. Rendu reel verifie :
    `previews_and_sims/apple-music-debut-progression/real_cards_allregions.py`.
  - **Vitesse (2026-09-25, « je veux être le premier à poster »)** :
    - ordre de post = importance (`_importance` : un #1 d'abord, US d'abord, puis meilleur rang ;
      a egalite l'album passe avant le titre) — 2 passes : decider, trier, poster ;
    - mode express `run_platform(..., express={song, album})` utilise par
      `collectors/itunes/release_watch.py` : lignes des 5 pays cles seulement, jamais de ligne
      mondiale partielle, pas de cards Global ; le run complet suivant ne reposte que ce qui a bouge ;
    - `platform_lock` (verrou OS `tools/locks/new_release_progression_<platform>.run.lock`,
      attente max `APPLE_MUSIC_DEBUT_LOCK_WAIT` = 1200 s) serialise le post horaire et l'express
      (etat partage, jamais de mise a jour perdue). Sim : `.../express.py`.
  - Simulation de reference : `previews_and_sims/apple-music-debut-progression/release_night.py`
    (les anciens `simulate.py` / `audit_*.py` monkeypatchent `_build_card_and_tweet`,
    qui n'existe plus : scinde en `build_tweet_text` + `render_card`).
- **Detection (version initiale, remplacee — voir ci-dessus)** : `core/discography.py::iter_catalog_tracks()` (factorise
  depuis `generate_snapshot_images.py` le meme jour) -> tout titre dont
  `release_date` tombe dans les dernieres `APPLE_MUSIC_DEBUT_WINDOW_HOURS`
  (defaut 72h) avant `now`, jamais dans le futur. Matching par titre
  (`song_key_candidates`), pas par `apple_music_id` (absent pour un titre tout
  juste sorti).
- **Sources verifiees** : Apple Music Global + Apple Music/iTunes pour
  `APPLE_MUSIC_DEBUT_KEY_COUNTRIES` (defaut `us,gb,fr,ca,au`) — lit
  directement le CSV du jour (`apple_music_daily_csv`/`itunes_daily_csv`),
  prend le cycle le plus recent present (`_latest_cycle_rows`), independant
  pour chaque source (Apple Music et iTunes arrondissent `scraped_at`
  separement, pas garanti identique a la seconde pres).
- **Apple Music et iTunes SEPARES : une card + un post par (titre,
  plateforme, cycle)** (3e correction proprietaire 2026-09-24 : « itunes
  needs to be seperated from apple music »). `PLATFORMS` dans le script ;
  chaque source porte un champ `platform` (`apple_music` = Global + pays
  cles, `itunes` = pays cles). Le declenchement « a bouge » est evalue par
  plateforme (un mouvement iTunes seul ne reposte pas la card Apple Music),
  lock/slug PNG incluent la plateforme, lien du tweet = `amcharts/applemusic`
  ou `amcharts/itunes`. Tableau "Region/Ranking" (lignes = "Global", "United
  States"…, sans prefixe plateforme), kicker "Apple Music · New Release
  Progress" / "iTunes · New Release Progress".
- **Cover + titre d'album** : cover = `db/discography/covers.json` pour
  l'album (via `comp.discography.build_cover_map`, donc la cover The Encore
  pour Showgirl), fallback sur l'`image_url` par titre du catalogue (qui
  pointe encore sur la cover standard). Nom d'album affiche via
  `comp.discography.display_title_for_album` (-> "The Life of a Showgirl:
  The Encore"). Le theme couleur reste calcule sur le vrai nom catalogue.
- **Dans une plateforme, une seule card par titre par cycle, listant TOUS ses
  charts** (decision proprietaire 2026-09-24, 2e correction — la 1re version postait une card
  distincte par (titre, chart, cycle), rejetée : « on mets toutes les régions
  dans une seule card... comme ça » en pointant `country_cards/`). Post
  seulement si **au moins un** chart de ce titre a bougé ce cycle (sinon
  volume ingerable) ; la card montre alors la ligne de **tous** les charts où
  le titre apparaît actuellement (pas seulement ceux qui ont bougé), triés
  bougé-d'abord puis par rang — premiere apparition = badge "(NEW)", sinon
  delta vs dernier rang connu dans l'etat
  (`tools/json/new_release_progression_state_<platform>.json` — un fichier par
  plateforme depuis le 2026-09-24 au soir, les 2 process tournent en parallele), jamais egal -> pas
  inclus dans le déclenchement (mais toujours affiché dans la ligne du
  tableau s'il apparaît).
- **Card = la share image du site (4e correction proprietaire 2026-09-24 :
  « match the frontend's actual design »)**. `_build_progression_card_html`
  est un portage HTML/CSS du `.overall-song-block` du frontend tel que capture
  par `ShareImageButton` (`is-share-image-capturing`) :
  `pages/AppleMusic.jsx::AppleMusicOverallBlock` / `pages/ITunes.jsx` —
  header cover 52px + titre + album, a droite rangee logo + "APPLE MUSIC"
  (`public/icons/apple-music-logo.webp`) ou logo + "ITUNES" (logo officiel
  Wikimedia `ITunes_logo.svg` stocke dans
  `collectors/apple_music/itunes_logo.svg` — le site n'a pas d'asset iTunes ;
  le proprietaire a demande la meme rangee que Apple Music au lieu des
  pastilles "N COUNTRIES / #1 / top N" de la page iTunes) + ligne date ·
  heure du cycle (ex. "Sep 25, 2026 · 2:00 AM CEST") + "vs <heure du cycle
  precedent>" + **toujours** 4 pastilles (meme a 0, demande proprietaire
  2026-09-24) : "X #1" (accent si >0), "X top 10", "X top 50", "X charting"
  (nb de charts de la plateforme ou le titre apparait),
  tableaux Chart|Country / Ranking decoupes comme `splitPlacementsIntoColumns`
  (1 col <6 lignes, 2 cols >=6, 4 cols >=12), drapeaux flagcdn (globe pour
  Global, via `comp.img_fetch`), deltas `(▲ n)`/`(▼ n)`/`(NEW)` et **rien si
  inchange** (comme le site), footer "Apple Music Charts - <date> - <heure>" /
  "iTunes Store Charts - <date>" + "@swiftiescharts" (a la place du
  "THE TAYLOR SWIFT MUSEUM : A Taylor Swift fan project" du site, demande
  proprietaire) + logo `public/logo.png` en **mask recolore en --text**
  (`.brand-logo` du site — le logo est blanc, un `<img>` brut est invisible
  sur fond clair : c'etait le trou blanc du footer des anciennes cards).
  Tokens = `:root` par defaut de `variables.css`. **Accent = couleur de la
  cover** (demande proprietaire 2026-09-24 : pas de violet Apple Music / vert
  iTunes fixes) via `_cover_accent` -> `comp.chart_card._cover_palette`
  (meme extraction que les cards Spotify Charts, lisible sur fond clair ;
  ex. cover The Encore -> #7c062e, cover standard Showgirl -> #9c7a29).
  Sert aux noms de pays, pastille "#1", badges NEW / NEW PEAK. Fleches
  ▲/▼ restent vert/rouge (convention du site).
  Valeurs CSS recopiees de `Charts.css`/`calendar.css`/`ITunes.css`/
  `RegionFlag.css` : si la card du site change, resynchroniser ce gabarit.
  Largeur 780px (1 col) / 860px (2 cols) / 1280px (4 cols). Lit des fichiers
  de `tsm-frontend/frontend/public` (repo voisin, deja requis par
  `core/card_theme.py`). L'ancien gabarit (theme par album de
  `generate_country_card_images.py`, kicker, pill date, @swiftiescharts) n'est
  plus utilise par ce script.
- **Colonne PEAK** (demande proprietaire 2026-09-24) : meilleur rang du
  titre sur CE chart depuis sa sortie = min de (rang actuel, toutes les
  lignes de tous les cycles des CSV journaliers du jour de sortie (Paris) a
  aujourd'hui — `_peak_since_release`, via `source["path_for"](date)` —, champ
  `peak` deja stocke dans l'etat). Donnees reelles uniquement, jamais
  inferees. Tableau = Chart|Country / Ranking / Peak.
  **Badge "NEW PEAK"** (pastille couleur accent dans la cellule Peak) quand
  le rang actuel bat strictement le peak d'AVANT ce cycle (CSV hors lignes du
  `scraped_at` courant + `peak` de l'etat). **1re apparition sur un chart**
  (aucun rang anterieur, ni dans l'etat ni dans les CSV — `is_first_run`) :
  badge **"NEW"** dans la cellule Peak (+ "(NEW)" dans Ranking), jamais
  "NEW PEAK" (demande proprietaire 2026-09-24). Un titre deja vu dans les CSV
  mais absent de l'etat (1er run du script tardif) n'est PAS un "NEW".
  **Badge "RE-PEAK"** (proprio 2026-09-25) : rang == peak d'avant ce cycle ET le
  dernier rang poste etait plus bas (`re_peak`) — meme pastille que NEW PEAK.
  (Depuis la refonte de la nuit du 24, `NEW` = jamais poste sur ce chart.)
- **Post X** : compte `@swiftiescharts` (meme session que le chart Global,
  `collectors/spotify/charts/global/tools/json/twitter_session.json`),
  `TWITTER_POST_PRIORITY` = `APPLE_MUSIC_DEBUT_POST_PRIORITY` (defaut **1**,
  niveau « tweets charts » du bareme data-rules — n'attend pas derriere les
  posts streams/charts, mais ne coupe pas la file early priority 0). Lock par
  (titre, plateforme, cycle) dans `tools/locks/new_release_progression/`.
- **S'eteint tout seul** : plus aucun titre dans la fenetre -> le script sort
  immediatement sans rien lire/poster. Aucune tache planifiee dediee, aucun
  nettoyage manuel a faire apres le week-end de sortie.
- **Emoji dans les prints** : le script force `sys.stdout.reconfigure(encoding="utf-8")`
  au demarrage — sans ca, `print()` d'un tweet contenant un emoji (ex. 🎧,
  U+1F3A7, hors cp1252) plante le script des qu'il tourne via le `.bat`
  (stdout redirige vers un fichier = codepage console Windows par defaut, pas
  UTF-8). Piege decouvert en testant ce script, a garder present pour tout
  futur script du pipeline qui imprime un tweet/texte avec emoji.
- **Card album Global en plus (demande proprietaire 2026-09-24)** : pour
  chaque album ayant un titre dans la fenetre, le script poste aussi la card
  `generate_snapshot_images.py --region global --album <album>` (tous les
  titres de l'album sur le Global, ex. Encore + Ophelia/Opalite, rangs reels,
  deltas **vs dernier snapshot de la veille** — donc les titres Encore restent
  "NEW" tout le jour de sortie, contrairement aux cards par titre qui
  comparent au cycle precedent). Declenchement : seulement si le classement
  de l'album sur le Global (signature {titre: rang}) differe du dernier poste
  (`album_snapshot|<album>|global` dans l'etat) — le Global ne bouge
  qu'environ 1x/jour, poster chaque heure l'image identique serait du spam.
  Etat ecrit seulement apres un post reussi (un slot X occupe retente au
  cycle suivant). Lock `album_snapshot_<album>_<scraped_at>.lock`, PNG
  `album_snapshot_<album>_<scraped_at>.png` dans `OUT_DIR`, tweet
  (texte remplace la nuit du 2026-09-24, voir « Refonte » ci-dessus)
  + lien `amcharts/applemusic`. Poste apres les cards par titre du cycle.
  Simulee dans `previews_and_sims/apple-music-debut-progression/simulate.py`
  (fixtures/<date>/ + copie reelle du Global 09-24 comme baseline).
- Logs dedies : `collectors/apple_music/post_new_release_progression.log` (Apple Music + card album)
  et `post_new_release_progression_itunes.log` (iTunes). `--platform {all,apple_music,itunes}`
  (defaut `all` = les deux a la suite, pour un run manuel ; la card album Global ne part que
  dans le run `apple_music`).

## Entrypoint

Commande principale:

```powershell
python .\collectors\apple_music\run_apple_music.py
```

Le runner lance, avec le meme `--scraped-at`:

1. `global.py`
2. `ts_page.py`
3. `ts_page_all.py` (collecte brute, chaque cycle -> `*_raw.csv`)
4. `finalize_ts_top_songs_daily.py` (agrege la veille en 1 ligne/chanson si pas deja fait -> CSV canonique)
5. `country_all.py`
6. `genre_all.py`
7. `scripts/export_apple_music.py`
8. `generate_country_card_images.py`
9. `generate_snapshot_images.py` — depuis le 2026-09-24 : sous-titre =
   "Chart Snapshot · <date · heure du snapshot> · vs <date · heure du snapshot
   compare>" (heure de Paris, ex. "11:00 AM CEST"), footer = date + heure.
   Toutes les regions comparent au **dernier snapshot du jour precedent**
   (recalcule dans le script depuis les CSV, recherche jusqu'a 7 j en arriere —
   meme semantique que `previous_rank`, verifie identique sur US/genres ; avant,
   seul Global le recalculait). Flag manuel `--album <nom|slug|sous-chaine>`
   (ex. `--album showgirl`) : garde seulement les titres de l'album (matching
   par `title` du catalogue `db/discography/albums/*.json`, toutes sections,
   via `core/discography.py::resolve_album_filter` — jamais `base_title`, sinon
   un filtre Fearless TV attraperait "Love Story" original), rangs du chart
   inchanges, titre = nom d'album affiche (`display_title_for_album`), PNG
   suffixe (`global_the-life-of-a-showgirl.png`) pour ne pas ecraser l'image
   standard. Le runner ne passe pas `--album` (usage manuel uniquement).
   **Colonne PEAK (cards `--album` uniquement, 2026-09-24)** :
   `make_peak_resolver` = meilleur rang sur ce chart parmi **tous les cycles**
   collectes depuis le jour de sortie du titre (meme semantique que les cards
   pays de `post_new_release_progression`), badge `NEW` (1re apparition
   depuis la sortie) / `NEW PEAK` (bat tous les cycles precedents) / `RE-PEAK`
   (egale le peak en revenant d'un rang plus bas au cycle precedent, proprio
   2026-09-25 — aussi valable avec les peaks de reference), pastille
   rouge Apple Music. Historique lu dans `snapshots/apple_music_charts/`
   puis, a defaut, dans le dossier legacy `data/<jour>/apple_music/`
   (`legacy_apple_music_daily_csv`, 2026-03-22 -> 2026-06-04, memes colonnes ;
   les 5 premiers jours ont `chart_type` vide -> accepte comme Global).
   Historique partiel (titre sorti avant `APPLE_MUSIC_HISTORY_START` =
   2026-03-22, ex. Ophelia/Opalite, ou jour de sortie sans snapshot) -> peak
   calcule sur les jours collectes seulement, affiche `#N` + legende grise
   `best in 2026` (decision proprietaire 2026-09-24 ; a partir de 2027 la
   legende devient `since Mar 22, 2026`, et un jour de sortie manquant pour
   un titre plus recent donne `since <1er jour collecte>`), et JAMAIS
   de badge NEW/NEW PEAK (le titre a pu debuter/culminer plus haut avant
   notre historique) — valide proprietaire 2026-09-24. **Exception Global
   (2026-09-25)** : si le titre est dans `db/apple_music_global_alltime_peaks.json`
   (peaks Global de tous les temps fournis par le proprio : les 12 titres de
   Showgirl standard, sortis le 2025-10-03), peak = min(reference, jours collectes),
   sans legende `best in`, et `NEW PEAK` si le rang bat les deux. Donnee de
   reference, jamais fusionnee dans l'historique collecte. Le meme fichier porte
   aussi `best_2026_jan1_jun1` (Kworb top 200, fourni le 2026-09-25) — purement
   informatif, ne change aucun badge (le peak de tous les temps est toujours <=).
   Aucun jour collecte
   -> tiret. Les dates du
   catalogue sont des timestamps ISO -> on garde `[:10]`.
   **Depuis le 2026-09-25 (proprio) : colonne PEAK sur TOUTES les cards**
   (regions/genres/Global, plus seulement `--album`) -> badges NEW PEAK / RE-PEAK
   sur les cards de region (US Pop, Global...) et donc sur les posts Pop/Global.
   Titre a historique partiel : toujours jamais NEW/NEW PEAK, mais badge
   **`BEST IN 2026`** (choix proprio) quand le rang bat son meilleur des jours
   collectes (ou `BEST SINCE <DATE>` hors 2026 / release sans snapshot) ; la
   legende grise `best in 2026` reste sous le peak. Perf : les lignes
   (scraped_at, song, rank) des jours PASSES sont cachees par chart dans
   `collectors/apple_music/tools/cache/peak_rows/<region>_<genre>/<jour>.json`
   (gitignore, reconstructible) — sans ca une card Pop relisait ~20 Mo de CSV
   genre par jour depuis le 22 mars (35 s/card ; 6,8 s cache chaud).
10. `scripts/upload_ap_r2.py`, sauf `UPLOAD_TO_R2=0`

Options runner:

- `--no-post`: flag legacy sans effet Twitter reel.
- `--no-images`: saute les images.
- `--force-images`: regenere les images.

## Scripts

Scripts combines quotidiens:

- `country_all.py`: songs + albums + music-videos par pays en un appel quand
  possible; fallback per-type si l'appel combine est rejete.
- `genre_all.py`: songs + albums par genre.
- `ts_page_all.py` (2026-08-17): variante composite de `ts_page.py`, agregee
  sur tous les storefronts decouverts (`core/storefronts.resolve_storefronts`,
  ~167 pays) au lieu d'un seul. Score par storefront = `500/rank**0.75` *
  poids marche (meme courbe et meme table `AM_MARKET_WEIGHTS` que le scoring
  Apple Music de TayBoard, `collectors/billboard/swift_top_100.py`, dupliquees
  localement expres pour eviter un import cross-collector — us=1.00,
  gb=0.70, jp=0.55, de/fr/ca=0.50, ... defaut 0.08 pour les marches non
  listes), somme -> classement global. Sans cette ponderation un #1 dans un
  marche ou TS est marginale compterait comme un #1 US ; garder les deux
  tables synchronisees si TayBoard change la sienne. Depuis le 2026-09-19,
  TS Top Songs applique en plus `APPLE_MUSIC_TS_IMPORTANT_STOREFRONT_BOOST`
  (defaut x3) aux 11 storefronts affiches sur les fiches chanson
  (`us,gb,fr,ca,au,de,jp,br,mx,it,es`) : le rang global reste tous pays, mais
  doit etre beaucoup plus ancre dans les marches que l'utilisateur peut
  inspecter, au lieu d'etre propulse par la longue traine de petits stores.
  Ecrit un CSV **separe**
  (`apple_music_ts_top_songs_global.csv`) : `ts_page.py` garde son fichier
  single-storefront intact car c'est la seule source lue par le scoring
  TayBoard (`_weekly_apple_music_ts_points`) — brancher ce dernier sur le
  composite fausserait ce score deja calibre.
  **Agregation par ISRC** (fallback `apple_music_id` si ISRC vide, depuis
  2026-09-10) : Apple sert un `apple_music_id` different par catalogue regional
  pour le meme master 2014-era (Wildest Dreams / Blank Space / Style / Shake It
  Off... ont un id US, un international, un Japon-deluxe, tous meme ISRC).
  Keyer sur l'id eclatait la chanson en 2-3 entrees concurrentes, chacune ne
  portant que ses storefronts et une fraction du score marche (Wildest Dreams
  sortait #10 en portant gb/au/de mais US/CA/JP vides car sur un autre id).
  L'ISRC identifie le master independamment de la sortie -> fusionne les
  repackagings deluxe/platinum/EP, garde Taylor's Versions / remixes / lives
  separes (ISRC propre). ~13 % du catalogue etait fragmente. L'`apple_music_id`
  ecrit au CSV = celui du fragment le mieux classe (peut changer d'un jour a
  l'autre ; le fallback previous_rank par nom couvre ce cas). Verifie : 1 seul
  merge discutable (Teardrops on My Guitar absorbe son "Radio Single Remix" car
  Apple leur met le meme ISRC — accepte).
  **Fusion chart (2026-09-25)** : par storefront, les titres TS presents dans le chart Apple Music Top Songs du pays (top `CHART_LIMIT`=200, meme endpoint que `country_all.py`, recupere dans le meme cycle) passent **en tete, dans l'ordre du chart**, puis le reste de la liste page-artiste dans son ordre (dedup par `apple_music_id` ou ISRC) — `merge_chart_into_top_songs`. Raison : la vue `top-songs` de la page artiste Apple met des heures a integrer une nouvelle sortie (Showgirl Encore : #1-#4 du chart UK, absents de la page artiste UK jusqu'a l'apres-midi → #105-#282 au TS Top Songs). TS Top Songs est notre chart, on peut melanger. `ts_page.py` (input TayBoard) n'est PAS concerne. Chart en 400 = pas de chart dans ce pays → liste page-artiste seule.
  Pagination plafonnee a
  `APPLE_MUSIC_TS_GLOBAL_DEPTH` (**defaut 400 = 4 pages/storefront depuis le
  2026-09-10**, avant 200 — le catalogue TS complet fait ~675 titres/storefront,
  mais au-dela du rang ~400 le score composite se joue a des fractions de
  point = bruit, pas du signal ; rang 400 ~= 6 pts sur ~500 pour le rang 1).
  Le classement global est l'**union** des tops de tous les storefronts : une
  chanson est classee des qu'elle apparait dans les N premiers d'au moins un
  storefront ; une colonne storefront a `–` = pas dans le top N de ce pays ce
  jour-la (0 point la, jamais une estimation).
  **Architecture collecte/publication (refonte 2026-09-19)** : `ts_page_all.py`
  n'a plus de gate horaire — il collecte a **chaque** cycle Apple Music (00h,
  02h, ..., 22h, comme le reste d'Apple Music), mais ecrit dans un fichier
  **brut** distinct (`apple_music_ts_top_songs_global_raw.csv`, jamais lu par
  l'export/le site). Un nouveau script, `finalize_ts_top_songs_daily.py`,
  tourne juste apres dans `run_apple_music.py` (meme liste `SCRIPTS`) et
  cible **la veille** par rapport a la date du run — des qu'un jour est
  termine (son dernier cycle 22h est passe), le prochain run (celui de 00h du
  lendemain) agrege TOUS les cycles bruts de ce jour en **une seule ligne
  finale par chanson** dans le CSV canonique
  (`apple_music_ts_top_songs_global.csv`, celui que l'export lit) — logique
  d'agregation dans `core/ts_top_songs_daily.py::compute_final_rows` (score
  jour = somme de `500/rank**0.75` sur tous les cycles ou la chanson est
  apparue ce jour-la, fusion par ISRC/apple_music_id comme avant). Idempotent :
  si le CSV canonique du jour a deja des lignes, `finalize_ts_top_songs_daily.py`
  ne fait rien (sauf `--force`) — l'appeler a chaque cycle est donc sans
  risque, il ne travaille reellement qu'une fois, au premier cycle du
  lendemain. `previous_rank` chaine toujours uniquement sur le jour
  calendaire precedent (jamais intra-jour), donc finaliser un jour ne
  recalcule que l'annotation `previous_rank` du jour suivant, jamais son
  classement.
  **Consequence** : le jour en cours (aujourd'hui) n'a **aucune** ligne dans
  le CSV canonique tant qu'il n'est pas termine — le site continue d'afficher
  le dernier jour COMPLET jusqu'a ce que le run de minuit du lendemain
  finalise. Ancien design (gate `APPLE_MUSIC_TS_GLOBAL_HOUR`, une seule
  collecte/jour publiee immediatement) abandonne le meme jour : il ne
  refletait que le cycle tombant sur l'heure du gate, pas la journee entiere.
  **Backfill historique (2026-09-19, one-off)** :
  `backfill_ts_top_songs_daily_final.py` a applique retroactivement la meme
  agregation sur tout l'historique (2026-07-30 -> 2026-09-18, ou chaque jour
  avait 4 a 45 cycles concurrents faute de la separation brut/final
  ci-dessus) ; garde un `.bak` par fichier modifie. Ne pas rejouer sauf bug
  trouve dans l'agregation — le pipeline live n'en a plus besoin.
  **Piege frontend (corrige le 2026-09-18, toujours vrai)** :
  `scripts/export_apple_music.py` construit `applemusic.json["dates"]` comme
  union de TOUTES les sources (global chart, TS composite, country/genre
  charts) ; ces dernieres tournent bien toutes les 2h en continu, et
  `_mirror_flat_current_to_latest` recopie le composite TS (inchange) sur
  chaque heure de cette union pour que l'API `/api/apple-music?date=` ne
  renvoie jamais un bucket vide a une heure sans vrai run — ce mirroring
  reste necessaire cote API, donc `dates` continuera de compter chaque cycle
  de 2h meme si le composite TS lui-meme ne change qu'une fois/jour.
  `applemusic.json["ts_top_songs_dates"]` (dates reelles, non mirrorees, du
  CSV composite canonique — 3e valeur de retour de `build_top_songs`,
  capturee avant tout mirroring) reste la source de verite pour l'affichage :
  `AppleMusic.jsx` scope son selecteur d'heure (`apple_snapshot_time`) dessus
  quand `tab === "ts_top_songs"`, donc l'onglet TS Top Songs Global n'affiche
  jamais plus d'une heure/jour, y compris pour aujourd'hui si le jour n'est
  pas encore finalise (il n'a alors aucune entree du tout, cf. ci-dessus).

Scripts legacy/manuels:

- `country_charts.py`
- `country_albums.py`
- `music_video_charts.py`
- `genre_charts.py`
- `genre_album_charts.py`
- `global_albums.py`
- `top_music_videos.py`

Outils partages:

- `core/http.py`: session HTTP/retries.
- `core/token.py`: MusicKit token cache/refresh.
- `core/csv_utils.py`: previous ranks et rewrite idempotent.
- `core/export.py`: lancement optionnel export.
- `core/storefronts.py`: decouverte storefronts.
- `core/r2.py`: upload R2 si change.
- `core/ts_top_songs_daily.py` (2026-09-19): agregation brut-cycles -> classement final du jour pour TS Top Songs Global, partagee par `finalize_ts_top_songs_daily.py` et `backfill_ts_top_songs_daily_final.py`.

## Donnees et sorties

CSV principaux dans `db/` (ou le dossier snapshot du jour, `DB_DIR` est date-dependant):

- `apple_music_ts_top_songs.csv` (single-storefront `us`, input TayBoard uniquement)
- `apple_music_ts_top_songs_global_raw.csv` (2026-09-19, ecrit par `ts_page_all.py` a chaque cycle 2h ; jamais lu par l'export — donnees internes seulement)
- `apple_music_ts_top_songs_global.csv` (composite final, **une ligne par chanson par jour**, ecrit uniquement par `finalize_ts_top_songs_daily.py` une fois le jour termine ; alimente l'onglet site "TS Top Songs" via `export_apple_music.py`/`upload_ap_r2.py` — aucune ligne pour le jour en cours tant qu'il n'est pas finalise)
- `apple_music_global.csv`
- `apple_music_genre_charts.csv`
- `apple_music_country_charts.csv`
- `apple_music_country_albums.csv`
- `apple_music_genre_album_charts.csv`
- `apple_music_music_video_charts.csv`
- `apple_music_ts_top_videos.csv`

Exports:

- `runtime/exports/web/site/data/applemusic.json`
- `runtime/exports/web/site/data/applemusic_history.json`
- objets R2 `apple-music/snapshots/`
- objets R2 `history-by-song/`

Les CSV sont la source complete. Les JSON frontend peuvent etre fenetres ou
precalcules.

### TS Top Songs « live » du jour (2026-09-24)

Decision produit : au lieu d'attendre le lendemain pour voir le classement TS
Top Songs du jour, le site montre un classement **cumule du jour en cours**
(cycle 1, puis 1+2, ... jusqu'au dernier) — `ts_top_songs_live.py`, meme
`compute_final_rows()` que la finalisation, donc apres le dernier cycle il EST
le final (test d'identite dans `previews_and_sims/ts-top-songs-live/simulate.py`,
a relancer apres toute modif de `core/ts_top_songs_daily.py`). Regles :
- toujours etiquete « en cours » (N mises a jour, heure de la derniere) ;
  jamais publie/utilise comme jour final (TayBoard, posts, notifs, finalize
  lisent le CSV canonique uniquement) ;
- mouvements = vs dernier jour FINAL (meme reference que le final) ;
- cle separee `ts_top_songs_live` dans `applemusic.json`, **ne pas utiliser
  `ts_top_songs.date` comme date du final** : l'export mirrore le final sur
  l'heure courante, cette cle vaut « aujourd'hui HH:00 » -> `final_date` vient
  de `ts_top_songs_dates` (vraies dates de mise a jour) ;
- front : live par defaut des 3 cycles (`TS_LIVE_MIN_CYCLES`, AppleMusic.jsx),
  final avant ; jamais sur une date passee choisie dans le calendrier.

### Objets history-by-song + gzip R2 (2026-09-24)

`upload_ap_r2.py::finalize_payload` applique maintenant, par source :
1. **Dedup des points identiques** : `read_csv()` unit `db/`, `_archive/` et
   tous les CSV quotidiens, la meme ligne y figurait 2-3 fois (27 % des
   points Global) -> la fiche chanson affichait des hits en double (247
   chansons concernees).
2. **Allegement** : `image_url`, `url`, `apple_music_id`, `album_name`,
   `song_name`, `storefront_ranks` ne sont gardes que sur les points portant
   la date la plus recente avec `image_url` (ex aequo inclus) — le seul que
   lit `tsm-frontend api/routes/apple_music.py::_song_history_rows_from_r2`
   pour les metadonnees. Si l'API se met a lire un de ces champs sur d'autres
   points, revoir `_POINT_META_FIELDS`.
3. **Gzip** (`upload_json_if_changed(..., compress=True)`, `ContentEncoding:
   gzip`) pour les per-song, `data/applemusic_history.json` et (2026-09-24)
   `apple-music/history-by-date/*.json` sauf `index.json` (4,2 Mo -> ~300 Ko ;
   `/api/apple-music` en lit 2 a 5 par requete froide : heure courante +
   comparaison yesterday/last). `applemusic.json` et snapshots restent en
   clair. Un objet deja au bon hash mais pas encore gzippe est re-uploade une
   fois (`upload_json_if_changed` regarde `ContentEncoding`) ; `scripts/r2.py`
   gzippe le meme prefixe (`_GZIP_KEY_PREFIXES`), sinon le dernier uploader
   passe ferait rebasculer le format. Migration faite le 2026-09-24 (97
   objets, sortie API verifiee identique par hash, local + prod). Le hash
   `Metadata.sha256` porte toujours sur le JSON **non compresse** (meme
   serialisation que `scripts/r2.py`, donc pas de re-upload en boucle).
   Lecteur : `tsm-frontend api/data/loader.py::_r2_json` degzippe sur les
   octets magiques `1f 8b` (deploye AVANT l'activation cote backend — ordre a
   respecter pour tout futur objet compresse). Tout nouveau lecteur boto3 de
   ces cles doit faire pareil (boto3 ne decompresse pas).
Verifie avant activation : les 705 objets reconstruits ancien/nouveau format
passes par la vraie fonction de l'API donnent une sortie identique, hors
suppression des doublons ; 1 028 Mo -> 330 Mo en clair -> **16,8 Mo gzip**
(Fate of Ophelia 52 Mo -> 0,77 Mo). Canari en prod (objet `Style` gzippe)
OK.

## Variables

- `APPLE_MUSIC_COUNTRIES`: limite les storefronts (`us,fr,gb,...`).
- `APPLE_MUSIC_CHART_LIMIT`: profondeur des charts, souvent 200.
- `APPLE_MUSIC_WORKERS`: concurrence par collecteur (defaut **32** depuis le
  2026-09-24, avant 12). Benchmark lecture seule du jour : aucun 429 a 12/32/64,
  debit plafonne ~44 req/s des 32 (bande passante), 64 n'ajoute que de la latence.
- `APPLE_MUSIC_FAILURE_RETRY_ROUNDS` (defaut 2) : storefronts/paires en echec
  re-tentes (`core/http.py::retry_failed`, pause 2s/4s, 8 workers) AVANT d'etre
  comptes `skipped` dans `country_all`, `genre_all`, `ts_page_all`. Un blip
  DNS ne coute plus un storefront (ni le composite TS entier au-dela de 5 %).
- `APPLE_MUSIC_PARALLEL_SCRIPTS` (defaut 1) : collecteurs en groupes paralleles.
- `APPLE_MUSIC_R2_WORKERS` (defaut 24) : concurrence de `upload_ap_r2.py`.
- `APPLE_MUSIC_TIMEOUT`
- `APPLE_MUSIC_RETRY_TOTAL`
- `APPLE_MUSIC_RETRY_BACKOFF`
- `APPLE_MUSIC_SKIP_EXPORT`: mis a `1` par le runner pendant les sous-scripts.
- `UPLOAD_TO_R2=0`: skip upload R2.

Token cache:

```text
collectors/apple_music/tools/json/apple_music_token.json
```

## Regles data

- Donnee absente ou ambigue: bloquer/loguer, ne pas publier comme complete.
- `previous_rank` doit venir du dernier snapshot d'un jour distinct precedent,
  pas d'un rerun du meme jour.
- `rewrite_for_snapshot` doit rester idempotent par `scraped_at`.
- `scraped_at` (depuis 2026-08-30) est timezone-aware : `2026-08-30T14:00:00+02:00`
  (`build_scraped_at()` dans `run_apple_music.py`). L'offset vient de
  `APPLE_MUSIC_SNAPSHOT_TZ` (Europe/Paris). Ne pas revenir à une heure murale
  nue : le frontend s'en sert pour convertir le snapshot dans le fuseau du
  visiteur. Comparaisons internes = `scraped_at[:10]` uniquement, garder ça.
- `apple_music_id` prime sur le titre; ne pas elargir a "meme titre = meme
  chanson" pour les donnees modernes.
- Comme l'historique Apple Music est incomplet, une chanson deja sortie ne doit
  pas etre marquee `NEW` par inference. Le code limite NEW aux releases
  recentes selon la fenetre explicite.

## Commandes utiles

Run complet:

```powershell
python .\collectors\apple_music\run_apple_music.py
```

Sandbox sans export:

```powershell
$env:TSM_DATA_DATE="2020-01-01"
$env:APPLE_MUSIC_SKIP_EXPORT="1"
$env:PYTHONPATH="$PWD;$PWD\collectors\apple_music"
python .\collectors\apple_music\country_all.py --countries fr us --scraped-at 2020-01-01T12:00:00
```

Export/upload:

```powershell
python .\scripts\export_apple_music.py
python .\scripts\upload_ap_r2.py --dry-run
```

## Highlights Charts Gallery

Depuis 2026-07-28, `run_apple_music.py` appelle en best-effort (jamais
bloquant) `scripts/generate_home_highlights.py --quiet` juste apres
`maybe_upload_to_r2()`. Regenere `cache/home_highlights.json` et
`cache/version.json` sur R2 (lus par `tsm-frontend/api`).

**Piege vecu 2026-09-24 (runs 15h-17h)** : le refresh highlights etait appele
APRES `job.result()` des images ; un `UnicodeEncodeError` (stdout du .bat en
cp1252, relais d'une sortie enfant contenant U+FFFD) faisait planter
`main()` avant -> highlights bloques sur l'heure precedente alors que
`/api/version` affichait la nouvelle. Fix : stdout/stderr reconfigures en
UTF-8 en tete de `run_apple_music.py`, `PYTHONIOENCODING=utf-8` dans
`child_env()`, refresh highlights juste apres l'upload (avant d'attendre les
images), echec/crash d'un job image jamais fatal.

**Best rank since (2026-08-14)** : `collectors/apple_music/best_rank_since.py`
detecte le meilleur rang Global Top 100 d'une chanson depuis au moins 14 jours
(reutilise `core.rank_since.compute_rank_since`, meme primitif que Spotify
Charts). Lit l'union `db/apple_music_global.csv` + snapshots quotidiens via
`apple_music_daily_csv_paths` (deja utilise ailleurs, ne pas reinventer),
regroupe les scrapes multiples d'un meme jour en gardant le dernier
`scraped_at` (meme regle que `export_apple_music.py::window_rows()`). **Ne
declenche jamais `kind="best_ever"`** (toujours appele avec
`release_date=None, history_start_date=None`) — l'historique local ne remonte
qu'a quelques mois (2026-06-05 sur cette machine, VPS prod depuis
2026-07-30), donc pas assez profond pour revendiquer un record "de tous les
temps" en confiance ; meme principe que la regle NEW ci-dessus. **Exception
2026-09-25** : titres de `db/apple_music_global_alltime_peaks.json` qui battent
tout notre historique -> `_reference_record` : `best_ever` si rang < peak de tous
les temps, `since 2026-01-01` si rang < `best_2026_jan1_jun1` (hors top 200 = 201),
sinon rien (date inconnue). Le highlight
produit (`type="best_rank_since", source="apple_music"`) reste affiche 14
jours apres declenchement — mecanisme dans `generate_home_highlights.py`, pas
ici. Seuils/decisions produit → skill `data-rules` § "Home highlights".

## Pieges

- **`.gitignore` exclut tous les `.csv` du repo, et `db/apple_music_*.csv`
  n'est jamais force-ajoute** (contrairement a YouTube qui fait
  `git add -f` dans `git_ops.py`). Un `git clone` frais (nouvelle machine,
  VPS, CI) n'a donc AUCUN historique CSV/snapshot Apple Music. Consequence
  observee le 2026-07-30 sur le VPS OVH : sans `snapshots/apple_music_charts/`
  ni `db/apple_music_*.csv`, `previous_rank` ne trouve rien a comparer et le
  catalogue entier part en NEW faux, publie sur R2 sans garde-fou (pas de
  blocage automatique comme pour Streams). Avant tout premier run reel sur
  une nouvelle machine : copier `snapshots/apple_music_charts/` (au moins
  les ~35 derniers jours, `PREV_RANK_WINDOW_DAYS=30` dans `core/csv_utils.py`)
  et `db/apple_music_*.csv` depuis une machine qui a l'historique. Detail :
  `OVH.md` a la racine du repo.
- Les imports `from core...` dependent du `PYTHONPATH` injecte.
- `TSM_DATA_DATE` et chemins de config peuvent etre evalues a l'import.
- Un subset de pays sur la vraie date peut produire un snapshot partiel.
- Un changement de schema CSV doit etre reporte dans export, upload R2, images
  et frontend si necessaire.
- **Bug corrige (2026-08-02) : notif ntfy global marquait NEW au lieu de RE**
  (`generate_snapshot_images.py`). La fonction cherchait le titre dans
  `runtime/exports/web/site/data/songs.json` / `website/site/data/songs.json`
  (exports runtime gitignores, jamais alimentes sur le VPS Apple Music car ils
  viennent de l'export Spotify qui n'y tourne pas), avec un fallback vers
  `db/discography/songs.json` qui est quasi vide (le vrai catalogue vit dans
  `db/discography/albums/*.json`). Resultat : catalogue "connu" vide sur le
  VPS -> toute reentree (`previous_rank` absent) etiquetee NEW dans la notif
  seulement (CSV/JSON restaient corrects via `previous_rank`). Corrige en
  lisant `release_date` depuis `db/discography/albums/*.json` +
  `songs.json`/`features.json`/`misc.json` (committes, toujours presents apres
  clone) et en appliquant la meme fenetre de 21 jours que
  `tsm-frontend/api/routes/apple_music.py` (`_is_recent_release`) au lieu
  d'un simple test d'appartenance au catalogue.
- **Bug corrige (2026-08-29) : `--notify-global-only` echouait sur
  `ModuleNotFoundError: playwright` quand playwright/Pillow ne sont pas
  installes, et l'echec bloquait l'upload R2**
  (`run_apple_music.notify_global_update` -> `sys.exit(1)`). Vu sur le
  workflow `run-data-only-collectors.yml` (deps minimales) apres le retrait
  de `--no-post` : `generate_snapshot_images.py` importait
  `collectors.comp.tables_image` (playwright + Pillow) au niveau module meme
  pour le chemin notif-only qui n'envoie qu'un texte ntfy. Corrige en rendant
  l'import `tables_image` lazy (dans `_rows_html` et `generate()`). En local
  le probleme ne se voit pas (venv complet), mais garder l'import lazy.
- **Bug corrige (2026-09-24) : trou de plusieurs heures dans le selecteur
  d'heure du site (ex. 2AM-7AM invisible) malgre un pipeline local qui tourne
  bien chaque heure et un upload R2 qui reussit.** Cause : `export_apple_music.py`
  construisait `dates` (JSON principal) et `history_dates` (JSON historique,
  d'ou `applemusic_history_dates/index.json` que `get_apple_music()` cote API
  privilegie sur les dates du JSON principal) uniquement a partir des lignes
  CSV physiquement ecrites + le timestamp du run EN COURS. Or une heure ou
  aucun des 7 types de chart n'a change n'ecrit aucune ligne CSV (dedup
  volontaire, cf. `[skip] snapshot identique`) — cette heure n'existe alors
  que dans SA PROPRE execution (mirroring `_mirror_*_current_to_latest` vers
  `latest_any`), jamais persistee. Le run suivant recalcule `dates`/`history_dates`
  from scratch et perd silencieusement cette heure, bien que son snapshot
  individuel (`apple-music/snapshots/<heure>.json`) reste sur R2 pour
  toujours (jamais supprime, juste plus reference). Sous l'ancienne cadence
  2h c'etait rare (1-3 creneaux/jour, lu comme du bruit dans le piège
  ci-dessus) ; en cadence horaire (depuis le 2026-09-24), plusieurs heures
  consecutives sans le moindre changement (nuit) sont courantes -> trou
  visible de plusieurs heures. **Fix** : les deux listes recuperent
  maintenant en plus les dates de l'export precedent (`prev_data`/`prev_history_data`,
  deja charges pour le backfill de `previous_rank`) avant d'etre recalculees,
  **mais seulement celles du jour courant** (`d[:10] == today_day`, corrige le
  meme jour apres audit). La 1re version reprenait toutes les dates sans
  filtre : des le lendemain, les heures de la veille (dont `window_rows` ne
  garde que le dernier snapshot par chart) seraient restees dans l'index en
  pointant sur le DERNIER snapshot du jour sous une etiquette plus ancienne,
  et plus rien ne serait jamais sorti de l'index (meme sous `HISTORY_CUTOFF`).
  Pour le jour courant c'est sur : `history_value_for_day()` retombe sur la
  cle la plus proche <= heure demandee du meme jour (toujours presente, le 1er
  run du jour ecrit toujours). Les jours passes gardent donc leur forme
  compactee d'avant (quelques heures/jour), c'est voulu.
  **Pendant cote `upload_ap_r2.py::upload_snapshot_jsons` (meme jour)** :
  `_snapshot_payload` ne lisait que la cle exacte -> toute heure sans ligne
  CSV propre partait sur R2 avec des listes vides, ecrasant le bon objet
  mirrore (vu : 03h-06h et 09h entierement vides, 02h/07h/08h partiels ; les
  jours passes avaient deja des objets partiels avant ce fix). Maintenant :
  meme repli "derniere cle <= heure, meme jour" (jamais une cle plus tardive),
  pas d'upload si aucun chart n'a de donnee, et **un jour passe n'est jamais
  re-uploade s'il existe deja** sur R2 (le recalcul a partir de l'export
  compacte serait plus pauvre que ce qui a ete uploade ce jour-la). Les 8
  snapshots du 2026-09-24 ont ete re-uploades corrects a la main.
  **Rattrapage ponctuel 2026-09-24** : heures 03h-06h du jour
  deja perdues avant le fix (le carry-forward ne peut pas resusciter ce que
  le fichier precedent n'avait deja plus) -> reinjectees a la main dans
  `applemusic.json`/`applemusic_history.json` locaux avant un rerun d'export +
  upload R2. Si un autre trou de plusieurs heures apparait malgre ce fix,
  verifier `runtime/exports/web/site/data/applemusic_history_dates/index.json`
  (source que le site utilise en priorite) plutot que seulement
  `applemusic.json`.
- **Trou "run manquant" du 2026-09-24T10:00 : pas un bug du script, PC en
  veille.** Contrairement au piege ci-dessus (heure existante mais non
  persistee), ce cas n'a produit AUCUNE donnee nulle part (ni CSV local, ni
  objet R2) : `Get-ScheduledTaskInfo` a rapporte `NumberOfMissedRuns: 1` et le
  journal `Microsoft-Windows-TaskScheduler/Operational` n'a aucune entree
  entre le run de 9h00 et celui de 11h00 (donc le trigger de 10h n'a jamais
  ete lance). Cause trouvee dans le journal `System` : le PC est parti en
  veille a 09:25 (`Sleep Reason: Button or Lid`), reveil bref a 09:55 suivi
  d'une re-hibernation immediate, reveil complet seulement a 10:06 — trop
  tard pour le trigger de 10h malgre `WakeToRun=True` sur la tache. Rien a
  reparer cote export : une heure sans le moindre run n'a pas de snapshot R2
  a retrouver. **Fix applique le 2026-09-24** : `powercfg /change
  standby-timeout-ac 0`, `standby-timeout-dc 0`, `hibernate-timeout-ac 0`,
  `hibernate-timeout-dc 0` (plus aucune veille auto, secteur ou batterie). Si
  un trou similaire (aucun objet R2 du tout sur un creneau, contrairement au
  cas "objet R2 present mais absent de `dates`") revient malgre ca, verifier
  d'abord le journal `System` (`Get-WinEvent -LogName System -Id 1,42`) pour
  un evenement veille/reveil avant de suspecter le code.
- **Upload R2 bloque a l'infini, cycle 11h du 2026-09-24 (corrige le meme
  jour).** `upload_ap_r2.py` est reste ~10 min fige dans la phase per-song :
  py-spy montrait les 8 workers bloques dans `ssl.sendall` (put_object),
  aucune sortie dans le log. Causes et fixes :
  - **Aucun timeout boto3** (`upload_ap_r2.py`, `core/r2.py`,
    `upload_itunes_r2.py`, `generate_home_highlights.py`) -> tous ont
    maintenant `Config(connect_timeout=10, read_timeout=60, retries standard
    x3)`. **Piege** : urllib3 applique le `connect_timeout` a tout l'envoi, et
    un body `bytes` part en UN `sendall` dont le timeout couvre le fichier
    entier -> avec 10s, tout objet > ~50 Mo (`applemusic_history.json` 125
    Mo, per-song Fate of Ophelia 52 Mo) echouerait a chaque fois. D'ou
    `Body=io.BytesIO(...)` partout : envoi par blocs de 16 Ko, timeout par bloc
    (un vrai blocage coupe en 10s, un gros fichier passe). Teste : 125 Mo
    envoyes en 192s sans erreur (~0,65 Mo/s depuis ce PC).
  - **Aucun timeout sur les sous-process** de `run_apple_music.py` /
    `run_itunes.py` -> `run_child()` avec plafond par etape (collecteurs
    1200s/900s, export 900s, upload 1800s, images 900s, notif 300s,
    highlights 600s) ; `live_trigger.py` 600s par script. Un enfant bloque ne
    peut plus bloquer indefiniment (NB : IgnoreNew ne joue pas, cf. correction
    2026-09-25 plus haut).
  - **Stdout des enfants bufferise** (le `-u` du `.bat` ne couvre que le
    parent) -> `child_env()` fixe `PYTHONUNBUFFERED=1`.
  - **Une image ratee bloquait l'upload R2** -> les images ne font plus que
    logger (cosmetique, la donnee passe avant). Un upload rate lance quand
    meme highlights + live projection, puis sort en code d'erreur.
  - `post_new_release_progression.py` pouvait attendre 30 min par tweet le
    slot `@swiftiescharts` (defaut `TWITTER_POST_LOCK_TIMEOUT`) -> nouveau
    kwarg `slot_timeout` dans `collectors/spotify/core/twitter.py::post_with_image`
    (defaut inchange pour les autres appelants), ici
    `APPLE_MUSIC_DEBUT_POST_SLOT_TIMEOUT` (defaut 180s) ; slot occupe = post
    saute (WARN), pas de blocage.
  - **Mode « Last snapshot » du site (tsm-frontend, 2026-09-24)** : Apple ne
    rafraichit pas chaque chart chaque heure, et le mirroring
    `_mirror_*_current_to_latest` recopie un chart inchange sur l'heure du
    run -> comparer a « l'heure d'avant » donnait souvent un chart compare a
    lui-meme (aucun mouvement affiche). L'API
    (`api/routes/apple_music.py::_last_distinct_bucket_payloads`) remonte
    maintenant, par famille de chart, jusqu'au dernier snapshot dont les
    POSITIONS different (signature (entree, rang), pas le contenu brut : la
    copie mirroree differe sur des champs annexes) ; heure reelle exposee dans
    `compared_to_by_bucket` et affichee sous chaque carte. Ne pas supprimer le
    mirroring pour « simplifier » : l'onglet TS Top Songs du jour n'a que la
    cle mirroree.
  - **`ts_page_all.py` / `finalize_ts_top_songs_daily.py` non bloquants**
    (`NON_BLOCKING_SCRIPTS`, cycle 12h du meme jour) : un echec DNS sur 12/168
    storefronts a fait echouer `ts_page_all.py` (garde anti-composite partiel,
    normal), et le runner sortait alors AVANT l'export -> heure entiere
    absente du site alors que global/pays/genres etaient complets. Leur
    sortie (CSV brut / finalisation idempotente de la veille) n'est jamais lue
    par l'export horaire, donc echec = WARN et on continue. Les autres
    collecteurs restent bloquants.
  - **Per-song allegé le meme jour** (voir section « Objets history-by-song »
    ci-dessous) : ~1 Go/h -> ~17 Mo pour les 705 objets.
  - **Acceleration SANS rien sauter (2026-09-24, demande explicite : « plus
    vite » = paralleliser, jamais supprimer une etape/donnee)** : collecteurs
    en 4 groupes paralleles (~3m20 -> ~41s mesures, ecritures desactivees),
    workers 12 -> 32, retry des echecs au lieu du skip,
    `csv_utils.load_previous_ranks` ne lit plus 30 jours x2 (walk-back jusqu'au
    1er jour passe avec snapshot + cache process ; resultats identiques
    verifies sur 21 cas, genre 12s -> 0,5s), images en parallele de l'upload,
    `upload_ap_r2` : slugify/normalize memoises + dedup sans `json.dumps` par
    point (705 objets identiques octet pour octet, build 52s -> 31s), build
    per-song en fond pendant les premieres phases. Jeton MusicKit : ecriture
    atomique du cache (processus paralleles).
  - Diagnostic rapide si le log parait fige : `Get-CimInstance Win32_Process`
    filtre sur `upload_ap_r2|run_apple_music` (nom de process = `python3.13.exe`,
    pas `python.exe`), puis `py-spy dump --pid <PID>`.
- **Alerte "R2 upload failed ... site not updated this hour" en boucle,
  2026-09-25 07h-13h (corrige le meme jour).** Rien a voir avec les nouvelles
  sorties : sous charge, des handshakes TLS vers R2 tombaient
  (`SSLEOFError UNEXPECTED_EOF_WHILE_READING`). `head_object_safe` avalait
  l'erreur -> le garde « jour passe deja sur R2 » de `upload_snapshot_jsons`
  croyait le snapshot absent -> reconstruisait et re-PUTait des snapshots du
  2026-08-26 au 09-04 (plus pauvres que l'original, cf. ci-dessus ; plusieurs
  ont ete reecrits ce matin-la, ceux du 09-24 non) ; ces PUT echouaient
  aussi -> 3 tentatives -> crash avant history-by-date/CSV/per-song, donc
  heure absente du site. Fix : `remote_object_exists()` (True/False seulement
  si R2 a repondu, 404 = absent, erreur reseau x3 = None) ; un jour passe n'est
  re-uploade que sur un vrai 404, jamais sur un doute. Ne pas reutiliser
  `head_object_safe` pour une decision « absent donc j'ecris ».
  **Suite, cycle 14h du meme jour : heure 15h entierement sautee.** Les SSL EOF
  sont revenus dans la phase history-by-date (fichiers du 08-27, inchanges) :
  `upload_json_if_changed` utilisait aussi `head_object_safe` -> PUT inutile ->
  echec -> exception ; puis le process est reste FIGE 30 min (sortie Python qui
  attend les threads workers bloques) jusqu'au kill 1800s, 2e essai rate, 3e
  lance a 14:59 -> le run de 14h tenait encore le verrou a 15h -> run de 15h
  abandonne (pas de collecte ni de post Apple Music a 15h ; iTunes, chaine
  separee, a poste normalement). Fixes : `head_object_or_raise` (404 = absent,
  erreur reseau x3 = exception, jamais « change ») ; echec reseau sur un fichier
  d'un JOUR PASSE (snapshots + history-by-date) = `[warn] ... skipped` (deja sur
  R2), seuls le jour courant et `index.json` restent bloquants (`_is_past`) ;
  `__main__` fait `os._exit` apres un crash (plus d'attente des threads).
  Pourquoi Apple Music « plus lent » qu'iTunes ce jour-la : pas la collecte (les
  posts partent apres global.py + country_all.py, ~HH:01), mais ce run bloque.
  Meme jour : l'alerte ntfy de l'upload rate partait tout en fin de run (apres
  images, live projection jusqu'a 600s, `_wait_posts` jusqu'a 1800s) -> recue
  vers HH:40. Elle part maintenant juste apres l'echec de l'upload ; le code de
  sortie reste pose a la fin.
- **Live projection trigger (ajoute 2026-09-23)** : `run_apple_music.py::main()`
  appelle `collectors/billboard/live_trigger.py::trigger_live_projection()`
  juste apres `regenerate_home_highlights_cache()`, uniquement sur le chemin
  de succes complet (scripts + export + upload R2 OK). Best-effort, jamais
  bloquant. Voir `collector-billboard/CONTEXTE.md` § "Live projection" pour
  le detail du declenchement multi-collecteurs.
