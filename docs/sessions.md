# Sessions du GUI

Une **session** est l'espace d'un onglet : ses bots, ses sons, ses actions. Deux onglets (ou deux utilisateurs) n'ont rien en commun. Usage : [Le GUI](gui.md).

## Création

L'onglet appelle `POST /api/session` (avec le [code d'accès](server.md#deux-secrets-deux-rôles) s'il est exigé) et reçoit un **identifiant secret de 192 bits**. Il le garde dans `sessionStorage` et l'envoie dans l'en-tête `X-Session` de chaque requête (jamais dans une URL).

## Présence et expiration

- Le navigateur ouvre un **WebSocket** (`/api/ws`). Le serveur y pousse l'état de la session à chaque changement ; le navigateur y envoie un `ping` toutes les 5 s. Le premier message (`{"sid": …}`) est limité à 1 Ko et un identifiant invalide ferme la connexion (code 4401) ; une session accepte au plus 5 WebSocket simultanés (4429 au-delà).
- Quand le WebSocket reste fermé plus de `ZOOMBOTS_SESSION_GRACE` secondes (**15** par défaut), la session est **détruite** : actions annulées, bots retirés de leurs réunions (Leave), modules PulseAudio déchargés, fichiers supprimés.
- Un **rechargement de page** dans ce délai reprend la même session. Un **nouvel onglet** en crée une autre.
- Une session créée mais jamais connectée au WebSocket expire après 30 s.
- À l'arrêt du serveur, toutes les sessions sont détruites ; leurs dossiers (`<data>/sessions/`) sont aussi vidés à chaque démarrage (ne mets donc rien à toi dans un dossier `sessions/` de `ZOOMBOTS_DATA`).
- Chaque destruction d'une session expirée est une tâche à part, bornée à 60 s : une session dont le nettoyage se fige (Chromium figé, `pactl` bloqué) ou échoue ne retarde pas celle des autres. Une destruction demandée deux fois (onglet fermé pendant l'expiration) attend la fin du nettoyage réel.
- Une session peut mourir quand le navigateur suspend un onglet en arrière-plan (téléphone) plus de 15 s : augmente `ZOOMBOTS_SESSION_GRACE` si besoin.
- Si le serveur oublie la session (redémarrage, expiration), l'onglet le détecte et en démarre une nouvelle, en le signalant.

## Isolation

- Chaque bot de session porte son propriétaire : les routes ne voient que les bots de la session appelante ; un identifiant de bot d'une autre session donne 404.
- Un son d'une session est inutilisable par une autre.
- Les bots de session ne sont **pas persistés**, et l'API d'administration (`broadcast`, `rm --finished`, `list`) ne les touche pas.
- L'administration (dashboard `/admin`, CLI) voit en revanche tous les bots du serveur, avec le début de l'identifiant de leur session, et peut les piloter ; seules ses actions de masse (`broadcast`, `rm --finished`) les ignorent.

## Sons

- **Envoi** : `PUT /api/sounds?name=…`, corps lu en flux et plafonné en octets (pas de dépendance multipart). L'extension doit figurer dans la liste blanche, le nom est nettoyé, le fichier est stocké sous un identifiant généré dans le dossier de la session, puis vérifié par `ffprobe` (piste audio présente, durée plafonnée, protocoles réseau interdits). Un fichier refusé est supprimé. Une place est **réservée dès le début de l'envoi** : des envois en parallèle ne dépassent pas le plafond de sons, et 2 envois simultanés au maximum par session ; `ffprobe` tourne au plus 2 fois à la fois par session.
- **YouTube** : seuls les liens **de vidéo** des hôtes `youtube.com`, `www.`, `m.`, `music.youtube.com` et `youtu.be` sont acceptés (`/watch`, `/shorts/…`, `/live/…`, `/embed/…`, `youtu.be/<id>`) : ni playlist, ni chaîne, ni page d'information (qui redirigerait vers un autre hôte). yt-dlp est de plus restreint à l'extracteur YouTube (`--use-extractors youtube`), à un seul élément (`--playlist-items 1`, `--no-playlist`), sans configuration (`--ignore-config`), avec taille et durée plafonnées, délai `ZOOMBOTS_YT_TIMEOUT` ; la sortie est confinée au dossier de la session. Au délai ou à l'annulation, **tout le groupe de processus** (yt-dlp et son ffmpeg) est tué et les fichiers partiels sont supprimés. Le téléchargement passe par le proxy HTTP(S) configuré ([Masquer l'IP](privacy-ip.md)). Il se fait en arrière-plan ; une erreur (nettoyée des IP) reste affichée 20 secondes.
- Supprimer un son arrête les actions qui l'utilisent **lui** (par identifiant : un autre son du même nom n'est pas touché).

## Actions

Une action joue un son N fois sur chaque bot ciblé, **séquentiellement** : chaque lecture attend la fin de la précédente (`play_wait`), puis une pause optionnelle. Détails :

- Le bot *k* démarre après `k × décalage` secondes.
- Si le bot n'est pas encore `in_meeting`, l'action attend (jusqu'à 15 min) ; si le bot est terminé, elle s'arrête en erreur.
- **Reconnexion du bot** pendant une lecture : la lecture interrompue n'est pas comptée ; l'action attend que le bot revienne dans la réunion (ou soit terminé) et rejoue la même lecture, sans consommer le budget d'échecs.
- **Micro indisponible** alors que le bot est dans la réunion : nouvelle tentative après 1 s ; **trois échecs consécutifs** arrêtent l'action en erreur.
- Une nouvelle action sur un bot **annule et remplace** la précédente ; arrêter une action coupe aussi le son en cours.

## Voir aussi

[Le GUI](gui.md) · [Limites et quotas](limits.md) · [API du GUI](api.md#api-du-gui) · [Sécurité](security.md)
