# notifiers/discord — notifications Discord Swifties Charts

Module indépendant des collectors : les collectors l'appellent explicitement
là où ils publient, avec un **type de post** (`kind`) qui fixe la priorité et
donc les rôles mentionnés. Indépendant de X : le post Discord part même si le
post X échoue, et peut exister sans équivalent X.

## Principe

- **Webhooks, pas de bot** : un webhook par salon, URL dans `.env`
  (`DISCORD_WEBHOOK_SPOTIFY_CHARTS`, `_SPOTIFY_STREAMS`, `_APPLE_MUSIC`,
  `_ITUNES`, `_TAYBOARD`, `_YOUTUBE`). Rien ne tourne en permanence.
- **Nom + logo par salon** dans `config.json` (`username`, `avatar_url`).
  `avatar_url` vide ⇒ avatar du webhook lui-même (cas YouTube, posé par PATCH).
- **4 niveaux** : `urgent` > `high` > `normal` > `low`.
- **Rôles par salon × niveau, cumulatifs** : `<Label> · Urgent`, `· Important`,
  `· Normal`, `· Tout`. Un post de niveau L mentionne le rôle de L et ceux des
  niveaux inférieurs ⇒ un membre prend UN rôle par salon (« Tout » = tout,
  « Urgent » = seulement l'urgent). Mentions sur le 1er message seulement.
- **`kinds`** : type de post → niveau, par salon. Changer une priorité = éditer
  `config.json`, pas le code. Kind inconnu ⇒ `normal` (loggué).
- **Style carte (2026-09-26)** : chaque post est un embed façon repost de tweet — auteur
  « Swifties Charts » + logo (lien x.com/swiftiescharts), texte, image DANS la carte (jusqu'à
  4 en galerie), pied = icône + label du salon + heure, barre de la couleur du salon
  (`channels.<salon>.color`). Réglages dans `config.json` → `embed` (`enabled: false` =
  anciens messages texte). Les mentions de rôles restent dans le texte du message, au-dessus
  de la carte (un embed ne ping jamais).
- **Anti-doublon** : `key` unique par post (ex. `global_daily_2026-09-25`) ;
  lock atomique `runtime/social/discord/sent/<salon>/<key>.lock` (gitignored),
  état `sending <ts>` pendant l'envoi puis `done <ts>`. Chaque message est journalisé dès
  qu'il part, avec sa position (fil, index) ; un envoi coupé (échec, ou process tué : lock
  `sending` de plus de 15 min) REPREND au rerun en sautant les messages déjà partis — ni trou,
  ni doublon.
- Chaque envoi journalisé avec ses ID de messages (voir `recent` / `delete`).
- Ne lève jamais. `DISCORD_ENABLED=0` coupe tout.

## Utilisation depuis un collector

```python
from core.discord_notify import discord_send   # pont (collectors/spotify/core)
discord_send("spotify-charts", [(tweet, image_path)], kind="rank_record",
             key=f"rank_record_{chart_date}_{region}_{slug}")
```

Hors `collectors/spotify` : mettre la racine du repo dans `sys.path`, puis
`from notifiers.discord import send` (même signature).

Règles d'appel : seulement quand le run publie vraiment (jamais en
`--no-post`/dry-run) ; appeler **avant** le post X (le slot X peut attendre
plusieurs minutes) ; ajouter tout nouveau `kind` dans `config.json`.

## CLI (depuis la racine)

```
python -m notifiers.discord show
python -m notifiers.discord send --channel spotify-charts --priority low --text "..." [--image a.png b.png] [--key X]
python -m notifiers.discord setup-roles [--channel spotify-charts ...]
python -m notifiers.discord set-role --channel spotify-charts --level high --id 123456789
python -m notifiers.discord recent [--channel spotify-charts] [-n 20]
python -m notifiers.discord delete --channel spotify-charts --key global_daily_2026-09-25
python -m notifiers.discord delete --channel spotify-charts --message-id 1553163113493176385
```

Chaque envoi est journalisé avec les ID Discord des messages
(`runtime/social/discord/sent_log.jsonl`, gitignored). `delete --key` supprime
tous les messages de ce post et libère son lock (un rerun pourra le renvoyer).
Un webhook ne peut supprimer que ses propres messages, et seulement ceux dont
l'ID est connu : les messages envoyés avant le 2026-09-25 23:55 (tests) ne
sont pas journalisés.

`setup-roles` crée les rôles manquants (idempotent, par nom) et écrit leurs ID
dans `config.json`. Il demande `DISCORD_BOT_TOKEN` + `DISCORD_GUILD_ID` dans
`.env` : une application Discord avec bot invité avec la permission « Gérer
les rôles ». Le bot n'a pas besoin de tourner, il sert juste de clé API.
Sans bot : créer les rôles à la main et les enregistrer avec `set-role`.

Vérifié le 2026-09-26 : un webhook notifie bien un rôle **non mentionnable** (via
`allowed_mentions.roles`) — pas besoin de rendre les rôles mentionnables.

Choix des rôles par les membres : page native « Salons & rôles » (questions des salons hors
onboarding), annoncée dans #roles — voir « Parcours d'arrivée » plus bas.

## Branchés

- `spotify-charts` (2026-09-25) : 15 posts, voir `kinds` dans `config.json`.
- Autres salons : webhooks + nom/logo prêts, collectors pas encore branchés.
  iTunes ne poste pas sur X : il faudra un post Discord dédié.

## Liens retires (2026-09-26)

`send()` passe chaque texte dans `strip_links()` : toute ligne contenant une URL
(`http://` / `https://`) est supprimee, les lignes vides en trop sont fusionnees. Un message
qui n'a plus ni texte ni image n'est pas envoye. Le texte publie sur X garde ses liens.

## Modifier / nettoyer des messages deja envoyes (2026-09-26)

Tout passe par `rewrite_message()` : relit le message depuis Discord (le journal ne garde que
120 caracteres) et le reecrit dans le style ACTUEL (carte), sans lignes de lien, ligne de roles
gardee sans re-ping, heure d'origine gardee dans la carte.

- `python -m notifiers.discord edit --channel C (--key K | --message-id ID) [--text T] [--image PNG ...]` :
  `--text` remplace le texte ; `--image` REMPLACE les images (omis = inchangees, `--image`
  seul = retire les images ; un chemin introuvable = echec, le message n'est pas touche).
  `--key` = 1er message envoye sous cette cle.
- `python -m notifiers.discord restyle [--channel C]` (alias `strip-links`) : remet tous les
  messages journalises au style actuel. Supprimes = `missing`, deja bons = `unchanged`.
  Passe du 2026-09-26 : 52 convertis en cartes, 0 erreur, 54/54 images verifiees.

**Piege images (2026-09-26)** : une fois un message en carte, Discord range ses images DANS
l'embed (liste `attachments` vide) et refuse de les re-attacher par id ; pointer l'embed vers
leur URL CDN detache le fichier, que Discord supprime (image 0x0 puis 404). Pour re-editer
une carte, `rewrite_message` telecharge donc ses images et les re-uploade.

**Piege lock (corrige)** : avant les etats `sending`/`done`, un process tue pendant un envoi
multi-messages laissait le lock pose sans rien journalise -> « deja envoye, skip » a chaque
rerun et le thread n'arrivait jamais (thread cards du 2026-09-25). Un vieux lock sans etat et
sans message journalise est maintenant repris apres 15 min.

## Fils par pays (threads) — 2026-09-26

`send(..., thread="us")` (et `core.discord_notify.discord_send(..., thread=...)`) poste dans le
fil Discord de la region si `channels.<salon>.threads.<code>` existe dans `config.json`, sinon
dans le salon principal (rien ne se perd). Codes : `global`, `us`, `gb` (`uk` = alias), `fr`,
`ca`… (codes pays Spotify), `worldwide` (thread des cards, cards multi-pays), `artists`
(charts artistes). Le webhook poste dans un fil EXISTANT (`?thread_id=`) mais ne peut pas en
creer dans un salon texte : les fils sont crees a la main puis enregistres :

    python -m notifiers.discord set-thread --channel spotify-charts --thread us --id <ID du fil>

Le `thread_id` est journalise avec chaque envoi, donc `edit` / `delete` / `strip-links`
marchent aussi sur les messages des fils. Teste en reel le 2026-09-26 sur le fil Global
(post, lecture, edit, suppression).

## Bot (2026-09-26) : fils + roles pays automatiques

Bot « Swifties Charts Bot » (app 1553417380997374143), invite sur le serveur Swifties Charts
avec voir salons / ecrire / historique / gerer roles / gerer + creer fils / ecrire dans les fils.
`.env` : `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID=1553151610358865970`. Il ne tourne pas : cle API.
Son role doit rester AU-DESSUS des roles qu'il gere.

- `config.json` → `channels.<salon>.thread_specs.<code>` = `{name, role}` (nom du fil + nom du role pays).
- `python -m notifiers.discord setup-threads --channel spotify-charts` : cree les fils manquants
  (actif ou archive du meme nom = reutilise), ecrit `threads`. Idempotent.
- `python -m notifiers.discord setup-country-roles --channel spotify-charts` : cree un role par fil,
  ecrit `thread_roles`. Idempotent (par nom).
- **Double envoi (proprietaire 2026-09-26, remplace « role du fil en plus »)** : le salon
  principal reste le fil « Overall » et recoit TOUS les posts comme avant, en mentionnant les
  roles d'importance + le role Overall (`overall_role` -> `thread_roles.overall`) ; si la
  region a un fil, le post y est AUSSI publie, en mentionnant seulement le role du pays (pas de
  double ping). Succes = envoi du salon principal ; un echec de la copie dans le fil est logue
  sans relance (relancer dupliquerait le salon principal). Chaque copie est journalisee avec
  son `thread_id` ; le bot desarchive le fil avant de poster (archive auto apres
  7 jours sans message). Sans bot : pas de desarchivage, le reste marche.
- Ajouter un pays : une entree dans `thread_specs`, puis relancer les 2 commandes.

Fils spotify-charts : global, us, gb (UK), fr, worldwide (thread des cards / cards multi-pays),
artists. Les membres choisissent leurs roles de fil dans « Salons & roles » ; le pays choisi a
l'arrivee donne aussi le role de son fil s'il existe (US, UK, France).

## Onboarding par l'API (2026-09-26)

Bot avec « Gerer le serveur » (+ « Gerer les roles »). `python -m notifiers.discord setup-onboarding
--channel spotify-charts [--title T]` ajoute/met a jour la question « Spotify Charts — which
charts? » (choix multiple : Overall + un choix par role de fil), en renvoyant le reste de
l'Onboarding tel quel. Les questions du salon restent TOUJOURS hors du parcours d'arrivee
(page « Salons et roles » seulement) : l'ancien flag `--in-onboarding` est supprime.

**Piege (incident 2026-09-26)** : GET renvoie l'emoji d'une option sous `emoji: {id, name,
animated}` mais PUT attend `emoji_id` / `emoji_name` / `emoji_animated` ; renvoyer la forme GET
EFFACE tous les emojis. La commande convertit desormais. `--restore-emoji TITRE EMOJI`
(repetable) remet l'emoji d'une option existante (utilise pour restaurer ⚠️ 🚨 📋 ☑️).

## Parcours d'arrivee (proprietaire 2026-09-26) : `setup-community`

Decision : **par defaut un membre ne recoit AUCUNE notification** (serveur en « mentions
seulement » + aucun role de notif donne a l'arrivee). Parcours :

1. **Onboarding = profil seulement** (anglais, inclusif) : age (obligatoire : 13–17 / 18+),
   pays (obligatoire, liste deroulante : 49 pays + « Other / rather not say » ; **sans Israel**,
   decision du proprietaire), pronoms (optionnel, multiple), genre (optionnel, multiple ; pas
   de question « sexe » separee). Chaque reponse donne un role de profil. Un pays qui a un fil
   Spotify Charts (US, UK, France) donne AUSSI le role de ce fil (choix du proprietaire : ces
   membres sont pingues dans le fil de leur pays des l'arrivee).
2. **Filtrage du reglement natif** : juste apres les questions, Discord impose « J'accepte » ;
   avant, impossible d'ecrire ou de reagir. Les regles (anglais) sont dans `community.json`.
3. **#rules** : message du bot (regles + « Next step: choose your notifications » + bouton lien
   vers la page native « Salons & roles »). **#roles** (lecture seule) : message du bot qui
   explique les roles de notif + meme bouton. Les questions de notifs des salons (niveau,
   quels charts) ne sont QUE dans « Salons & roles » (choix du proprietaire : pas de boutons
   custom, donc pas d'endpoint d'interactions a heberger).

    python -m notifiers.discord setup-community

Idempotent, tout vient de `notifiers/discord/community.json` : cree les roles de profil
manquants (et supprime ceux d'options retirees du fichier), reecrit l'onboarding en gardant
les ID des questions/options existantes, n'enregistre le reglement que s'il a change (chaque
enregistrement cree une nouvelle version), donne au bot le droit d'ecrire dans #rules/#roles,
met #roles en lecture seule, ajoute #rules/#roles aux salons par defaut, poste ou EDITE les 2
messages du bot (ID gardes dans `community.json` -> `message_ids`). Un bot peut poster des
boutons LIEN sans endpoint d'interactions.

**Limites mesurees sur l'API (2026-09-26)** : question « choix multiple » = **12 options max**
(`TOO_MANY_ONBOARDING_OPTIONS` des 13) ; question **liste deroulante** (`type: 1`) = **50 max**.
La commande passe en liste deroulante au-dela de 12. Le reglement natif
(`/guilds/{id}/member-verification`, non documente) a ete accepte avec le token du bot.
Les roles de profil sont visibles de tous sur le profil du membre (limite Discord).
