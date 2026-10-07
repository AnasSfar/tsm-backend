# Contexte Spotify Core

## Role

`collectors/spotify/core` contient les helpers partages par les pipelines
Spotify streams et charts.

## Fichiers

- `data_paths.py`: chemins modernes/legacy, snapshots, exports web, DB, helpers
  `spotify_chart_dir`, `legacy_spotify_chart_dir`, `run_all_charts_root`,
  `first_existing`.
- `twitter.py`: posting X/Twitter, sessions, images, locks/skip_if autour du
  composer.
- `git_ops.py`: commit/push.
- `notify.py`: ntfy.
- `discord_notify.py`: pont vers `notifiers/discord` (module Discord
  independant, voir son README) : `discord_send(channel, posts, kind=, key=)`.
  `twitter.py` ne touche PAS a Discord.
- `logger.py`: logging.
- `history.py`: helpers history.
- `retention.py`: nettoyage artefacts generes.
- `swift_top_gate.py`: gate Swift Top apres charts.
- `download.py`: telechargements.
- `fmt.py`: formatters.
- `chart_comment.py`: commentaire du tweet quotidien Global/US/UK (`build_chart_comment`).
  **Refonte 2026-10-01** (owner : « remains steady at » / « jumped to the X spot » trop pauvres) :
  raconte avec l'historique complet `db/charts_history_<chart>.csv` (relie par titre) — jours
  consecutifs / total a #1, retour a #1, 1er #1, nouveau peak, RE = « jumps back into the chart
  at #N. It last charted X days ago, at #R on Weekday (Mon D, YYYY) », gros bond (>= 15) +
  « its highest position since <date> » (>= 14 j), paliers de jours (100e, 365e, 1 000e...),
  serie top 10 >= 5 j, plus gros gain streams (>= 5 %), sinon plus longue serie (champ `streak`).
  Jour de sortie : >= 3 debuts → « Taylor Swift debuts N new songs ..., led by ... » ; >= 3 RE →
  « N Taylor Swift songs re-enter ... ». Candidats scores, 2 phrases max (2 chansons differentes).
  Garde exactitude : une serie/absence/« since » qui traverse une date absente du CSV n'est pas
  ecrite (`_history_complete`, `_consecutive_days` → None).
- `album_emoji.py`: emoji albums.

## Regles

- Ne pas hardcoder de nouveaux chemins quand `data_paths.py` fournit deja une
  abstraction moderne/legacy.
- Toute modification de `twitter.py` doit preserver:
  - pas de post image sans image confirmee;
  - re-check `skip_if` apres acquisition du slot;
  - locks anti-double-post;
  - compat sessions region;
  - slot de post a priorite (`_twitter_account_slot(priority=...)` / env `TWITTER_POST_PRIORITY`
    / fichiers `waiter_<acct>_<pid>.json`) : sous contention le slot va au plus prioritaire
    puis au plus ancien, avec anti-famine (`TWITTER_WAITER_AGING_SECONDS`). Fail-open si la
    file d'attente casse (ne jamais bloquer un post a cause d'elle). Bareme -> skill `data-rules`.
  - espacement entre posts attendu HORS verrou (depuis 2026-09-28) : `_twitter_account_slot` tire
    l'espacement du post une fois (`_SLOT_SPACING_S`, reutilise par `_wait_account_spacing`), le
    publie dans le waiter (`"spacing"`), et ne prend le verrou qu'a <= `TWITTER_SLOT_SPACING_LEAD_SECONDS`
    (25 s, couvre l'ouverture du navigateur) de la fin de cet espacement ; si un autre process a poste
    entre-temps, il rend le verrou. `_waiter_is_next` : un waiter mieux classe ne bloque que s'il est
    pret OU de priorite effective strictement meilleure. Ne jamais remettre un `sleep` d'espacement
    long en tenant le verrou (incident 2026-09-28 : post AM/iTunes a 150 s qui bloquait le Global
    Spotify Charts). Sim : `previews_and_sims/twitter-account-slot-spacing/simulate.py`.
- Toute modification de git/notify doit rester non bloquante quand le pipeline
  doit continuer, sauf quand le code l'exige explicitement.
- Les helpers partages ne doivent pas masquer une donnee manquante par defaut
  silencieux.

## Verification

Selon le helper:

- `data_paths.py`: verifier un script charts et un script streams.
- `twitter.py`: tester en mode no-post/dry-run si possible; ne pas poster sans
  intention claire.
- `retention.py`: dry-run ou inspecter chemins avant suppression.
- `git_ops.py`: ne pas commit/push sans demande.

## Pieges

- Changer un fallback legacy peut affecter des scripts historiques.
- Les chemins `website/site/data` peuvent encore etre lus en fallback, mais les
  exports modernes vivent sous `runtime/exports/web`.
- `twitter.py` est fragile au DOM X; diagnostiquer avec les HTML/debugs plutot
  que deviner les selecteurs.
- Incident 2026-09-17 (global-post/us-post, plusieurs tentatives echouees) :
  quand le file chooser natif ne se declenche pas ("X file chooser
  introuvable, fallback input[type=file]"), le fallback `set_input_files`
  contourne l'evenement de fermeture du chooser et laisse une couche
  decorative absolument positionnee (inset:0) peinte au-dessus de
  `tweetButton`. Un `.click()` classique boucle en timeout contre elle ; meme
  `force=True` echoue silencieusement car Playwright dispatche quand meme
  l'evenement souris aux coordonnees du bouton, que le navigateur route vers
  ce meme div en hit-test (constate le 2026-09-17 : le clic force ne renvoyait
  pas d'erreur mais le texte du composer n'etait jamais efface, donc rien
  n'etait poste). Fix dans `_click_tweet_button()` : clic normal, puis
  raccourci clavier Ctrl/Cmd+Entree (evite completement le hit-test), puis
  `element.evaluate("el => el.click()")` (invoque les handlers JS du bouton
  sans passer par le hit-test souris), et seulement en dernier recours un clic
  force. Apres chaque etape, on verifie brievement le composer/toast avant
  d'escalader, pour eviter de poster deux fois si une etape a en fait marche.
  Un screenshot/HTML debug est aussi ecrit des que le fallback file-input est
  utilise (`_write_upload_debug_artifacts`), pour diagnostiquer pourquoi le
  chooser natif ne se declenche pas.

## Posts programmes sur X : registre anti-collision (2026-10-07)

`core/twitter.py` tient `TWITTER_COORD_DIR/scheduled_<compte>.json` : chaque post
programme (`post_with_image` avec `TWITTER_SCHEDULE_SLOTS`/`_AT`, `schedule_post`)
y est inscrit avec sa source (`TWITTER_SCHEDULE_SOURCE`, ex.
`streams-finalize:2026-10-05`). Lecture : `scheduled_entries(account_key=None)`
(None = tous les comptes, purge > 1 h passe). Garde-fous :
- programmation : `free_schedule_slot` decale a la minute libre suivante si un post
  deja programme est a < `TWITTER_SCHEDULE_COLLISION_SECONDS` (180) ;
- post en direct : `_wait_account_spacing` -> `_wait_scheduled_collision` attend
  qu'aucun post programme du compte ne parte a +/- `TWITTER_SCHEDULE_LIVE_GUARD_SECONDS`
  (60 s, +30 s de derive X). Concerne TOUS les chemins directs (charts compris) ;
  fail-open si le registre est illisible.
- `_env_schedule_at` : n-ieme post programme du process = n-ieme creneau de
  `TWITTER_SCHEDULE_SLOTS` ; repli `dernier + TWITTER_SCHEDULE_GAP_SECONDS` arrondi a
  la minute SUPERIEURE.
- `_wait_post_scheduled` : mots d'erreur lus seulement dans toast/alert/aria-live
  (plus dans tout le `body`, faux « non confirme » le 2026-10-07).
Planificateur cote streams : `streams/tools/scripts/post_pacing.py` + heatmap
`core/x_active_times.py` (voir `spotify-streams/CONTEXTE.md`).
