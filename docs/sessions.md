# Sessions du GUI

Une **session** est l'espace d'un onglet : ses bots, ses sons, ses actions. Deux onglets (ou deux utilisateurs) n'ont rien en commun. Usage : [Le GUI](gui.md).

## Création

L'onglet appelle `POST /api/session` (avec le [code d'accès](server.md#deux-secrets-deux-rôles) s'il est exigé) et reçoit un **identifiant secret de 192 bits**. Il le garde dans `sessionStorage` et l'envoie dans l'en-tête `X-Session` de chaque requête (jamais dans une URL).

## Présence et expiration

- Le navigateur ouvre un **WebSocket** (`/api/ws`). Le serveur y pousse l'état de la session à chaque changement ; le navigateur y envoie un `ping` toutes les 5 s.
- Quand le WebSocket reste fermé plus de `ZOOMBOTS_SESSION_GRACE` secondes (**15** par défaut), la session est **détruite** : actions annulées, bots retirés de leurs réunions (Leave), modules PulseAudio déchargés, fichiers supprimés.
- Un **rechargement de page** dans ce délai reprend la même session. Un **nouvel onglet** en crée une autre.
- Une session créée mais jamais connectée au WebSocket expire après 30 s.
- À l'arrêt du serveur, toutes les sessions sont détruites ; leurs dossiers (`<data>/sessions/`) sont aussi vidés à chaque démarrage.
- Si le serveur oublie la session (redémarrage, expiration), l'onglet le détecte et en démarre une nouvelle, en le signalant.

## Isolation

- Chaque bot de session porte son propriétaire : les routes ne voient que les bots de la session appelante ; un identifiant de bot d'une autre session donne 404.
- Un son d'une session est inutilisable par une autre.
- Les bots de session ne sont **pas persistés**, et l'API d'administration (`broadcast`, `rm --finished`, `list`) ne les touche pas.
- L'administration (dashboard `/admin`) voit en revanche tous les bots du serveur, avec le début de l'identifiant de leur session.

## Sons

- **Envoi** : `PUT /api/sounds?name=…`, corps lu en flux et plafonné en octets (pas de dépendance multipart). L'extension doit figurer dans la liste blanche, le nom est nettoyé, le fichier est stocké sous un identifiant généré dans le dossier de la session, puis vérifié par `ffprobe` (piste audio présente, durée plafonnée). Un fichier refusé est supprimé.
- **YouTube** : seuls `youtube.com`, `www.`, `m.`, `music.youtube.com` et `youtu.be` sont acceptés (aucun autre hôte n'est contacté par yt-dlp). Options : `--no-playlist`, `--ignore-config`, taille et durée plafonnées, délai `ZOOMBOTS_YT_TIMEOUT`, sortie confinée au dossier de la session. Le téléchargement se fait en arrière-plan ; une erreur reste affichée 20 secondes.
- Supprimer un son arrête les actions qui l'utilisent.

## Actions

Une action joue un son N fois sur chaque bot ciblé, **séquentiellement** : chaque lecture attend la fin de la précédente (`play_wait`), puis une pause optionnelle. Détails :

- Le bot *k* démarre après `k × décalage` secondes.
- Si le bot n'est pas encore `in_meeting`, l'action attend (jusqu'à 15 min) ; si le bot est terminé, elle s'arrête en erreur.
- Une coupure en cours de lecture ou un micro indisponible : nouvelle tentative après 1 s ; **trois échecs consécutifs** arrêtent l'action en erreur.
- Une nouvelle action sur un bot **annule et remplace** la précédente ; arrêter une action coupe aussi le son en cours.

## Voir aussi

[Le GUI](gui.md) · [Limites et quotas](limits.md) · [API du GUI](api.md#api-du-gui) · [Sécurité](security.md)
