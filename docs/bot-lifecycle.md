# Cycle de vie d'un bot

`created → joining → waiting_room → in_meeting → reconnecting → … → ended | denied | error | left`

| Statut | Sens |
|---|---|
| `created` | Bot enregistré, pas encore lancé |
| `joining` | Chromium lancé, formulaire de connexion en cours |
| `waiting_room` | Salle d'attente : l'hôte n'a pas encore admis le bot |
| `in_meeting` | Dans la réunion, audio par ordinateur activé |
| `reconnecting` | Navigateur ou page fermés, ou connexion échouée : nouvelle tentative en attente |
| `ended` | La réunion est terminée |
| `denied` | Refus d'admission, expulsion confirmée ou réunion invalide |
| `error` | Échec définitif (reconnexions épuisées, reconnexion désactivée, proxy exigé et absent…) |
| `left` | Retiré par l'utilisateur |

Un bot **terminé** (`ended`, `denied`, `error`, `left`) ne compte plus dans les plafonds et n'est pas persisté.

## Connexion

1. Lancement de Chromium (avec son [sandbox](security.md#chromium-et-son-sandbox) quand c'est possible) avec le micro virtuel du bot, le [proxy](privacy-ip.md) éventuel et une locale `en-US` forcée (les libellés de Zoom sont ceux de l'interface anglaise ; les libellés français restent reconnus en secours).
2. Chargement de la page de connexion, bandeau cookies accepté, nom et passcode saisis, clic sur Join.
3. **Attente d'admission** (jusqu'à `ZOOMBOTS_ADMIT_TIMEOUT`, 600 s) : le bot distingue salle d'attente, refus, fin de réunion et entrée réelle. Dès que le bouton « audio par ordinateur » apparaît, il le clique.

Le plafond `ZOOMBOTS_MAX_JOINS` ne couvre que les étapes 1 et 2 : l'attente en salle d'attente ne bloque pas les autres bots.

## Détection de l'état

`classify_text` classe la page (`in_meeting`, `waiting`, `denied`, `ended`, `failed`, `unknown`) à partir du texte des **seuls titres, dialogues et alertes** (`h1`–`h3`, `[role=dialog]`, `[role=alertdialog]`, `[role=alert]`). **Dans la réunion**, le chat, les sous-titres et les noms de participants ne sont jamais lus : un participant nommé « Refusé » ou un message « I refuse » ne change rien. **Pendant l'admission** (avant d'entrer, quand il n'y a ni chat ni participants), tout le texte de la page est classé, parce que la salle d'attente est un message de page ordinaire. La présence du bouton Leave (barre d'outils) l'emporte sur tout texte.

## Surveillance

Une boucle (`ZOOMBOTS_WATCH_INTERVAL`, 3 s) vérifie que le navigateur et la page vivent. Une fin (`ended`, `denied`) n'est décidée que si la barre d'outils reste **absente 3 cycles de suite** et qu'un message système la confirme.

## Reconnexion

Quand le navigateur ou la page se ferme, ou que la connexion échoue (y compris le délai d'admission), et si la reconnexion automatique est active. Une **expulsion confirmée** (`denied`) ou une **fin de réunion** (`ended`) ne se reconnecte **pas** :

- attente croissante : 2, 4, 8… 30 secondes ;
- `ZOOMBOTS_MAX_RECONNECTS` tentatives consécutives (3), puis statut `error` ;
- le compteur ne repart de zéro qu'après une **session stable** (`ZOOMBOTS_STABLE_SESSION`, 60 s) : une boucle connexion/coupure immédiate s'arrête donc ;
- aucune reconnexion si la réunion est finie ou l'admission refusée ;
- chaque reconnexion relance Chromium et prend le **proxy suivant** s'il y en a plusieurs ([Masquer l'IP](privacy-ip.md#plusieurs-proxies--une-ip-par-bot)) ;
- le délai d'admission (`ZOOMBOTS_ADMIT_TIMEOUT`) s'applique à chaque tentative : l'attente totale peut atteindre `(1 + ZOOMBOTS_MAX_RECONNECTS) × 600 s` avant `error` (600 s seulement sans reconnexion).

## Micro et sons

`set_mic` vise le bouton par libellé exact (`mute my microphone`, pas « Select a microphone »), ne clique que si l'état doit changer, et attend 0,4 s après un dé-mute avant d'envoyer un son. Bouton absent ou délai dépassé : erreur lisible (HTTP 409), jamais un blocage. `play` dé-mute automatiquement ; `play_wait` attend la fin de la lecture ([Audio](audio.md)).

## Quitter

`leave()` clique sur Leave (et sa confirmation) pour ne pas laisser de participant fantôme, annule la tâche du bot, ferme Chromium et décharge les modules PulseAudio. Annuler un bot en pleine connexion ne laisse rien derrière lui. La fermeture de Chromium est bornée à 15 s (un navigateur figé ne bloque pas le nettoyage), et la dernière capture d'erreur du bot est supprimée.

## Domaines Zoom

Le lien fourni détermine l'hôte du client web (`https://<hôte>/wc/join/<id>`). Hôtes acceptés : `zoom.us`, `zoomgov.com`, `zoom.com`, `zoom.com.cn`, `zoomgov.cn` et **tous leurs sous-domaines** (régionaux `us02web.zoom.us`, d'entreprise `acme.zoom.us`).

- `zoom.us` et `www.zoom.us` deviennent `app.zoom.us` ; `zoomgov.com` devient `www.zoomgov.com`.
- Chemins reconnus : `/j/`, `/s/`, `/wc/join/`, `/wc/`.
- Un hôte hors liste est **refusé** : un lien arbitraire ne doit pas faire naviguer le navigateur ailleurs.
- Domaine d'entreprise personnalisé : `ZOOMBOTS_EXTRA_DOMAINS=meet.example.com,…`.
- L'ID fait 9 à 11 chiffres ; un ID plus long est refusé, jamais tronqué.
- Le passcode est encodé dans l'URL et masqué (`***`) dans les erreurs, l'historique et les journaux.

## Voir aussi

[Audio](audio.md) · [Sessions](sessions.md) · [Masquer l'IP](privacy-ip.md) · [Dépannage](troubleshooting.md#un-bot-reste-bloqué)
