# Dépannage

Commence par `zoombots status`, `zoombots logs -n 100` et, pour un bot précis, `zoombots events <bot>` et `zoombots screenshot <bot>` (ou le panneau du bot dans le [GUI](gui.md#4-mes-bots)).

## `zoombots: command not found`
`~/.local/bin` n'est pas dans ton `PATH` : `export PATH="$HOME/.local/bin:$PATH"`. Ou utilise `.venv/bin/zoombots`.

## « serveur injoignable »
Le serveur n'est pas lancé : `zoombots gui` ou `zoombots start`. Si tu as changé de port ou de mode, vérifie `zoombots status`. Après un crash, supprime `run.json` et `server.pid` dans `~/.local/state/zoombots`.

## « déjà lancé »
`zoombots shutdown`, puis relance. Un seul serveur à la fois par machine ([Modes](modes.md)).

## Chromium ne démarre pas
- *Executable doesn't exist* : la version de Playwright ne correspond pas au Chromium téléchargé. Relance `./install.sh` (ou `.venv/bin/python -m playwright install chromium`).
- Bibliothèques manquantes (hors Debian/Ubuntu) : installe celles que Playwright nomme.
- Le **sandbox** de Chromium, activé quand zoombots ne tourne pas en root, demande des espaces de noms utilisateur : sur un système qui les interdit (certains conteneurs, noyaux durcis), le bot échoue au lancement avec une erreur de sandbox. Mets `ZOOMBOTS_SANDBOX=0` (et lis [Sécurité](security.md#chromium-et-son-sandbox)). En root, il est déjà désactivé par défaut ; un utilisateur dédié sans droits est préférable.
- Conteneur : `shm_size` trop petit ([Docker](docker.md)).

## « PulseAudio est requis » / « impossible de démarrer PulseAudio »
Installe `pulseaudio-utils` et un serveur PulseAudio ou PipeWire-pulse (`./install.sh`). Teste `pactl info`. Sur un serveur sans session de bureau, `zoombots` lance `pulseaudio --start` lui-même.

## Un bot reste bloqué
- `joining` longtemps : regarde la capture ; un sélecteur de la page Zoom a peut-être changé ([Limitations](known-limitations.md#zoom)).
- `waiting_room` : l'hôte doit admettre le bot (attente max `ZOOMBOTS_ADMIT_TIMEOUT`).
- `denied` : expulsion confirmée, refus ou réunion invalide ; pas de reconnexion automatique.
- `error` avec « abandon après N reconnexion(s) » : lis l'erreur affichée et `zoombots logs`.

## Pas de son dans la réunion
Vérifie que le bot est `in_meeting`, que le micro n'est pas coupé (🎤 dans le panneau du bot), et que `ffmpeg` lit le fichier (`ffmpeg -i fichier -f null -`). Les erreurs de lecture sont dans `zoombots logs` (« lecture en échec »). Le son n'est pas validé de bout en bout : [Tests et validation](testing.md).

## « bouton micro introuvable ou inutilisable »
Zoom n'a pas affiché sa barre d'outils ou a changé le libellé du bouton. Capture d'écran du bot pour voir où il en est.

## Erreur 429
Un [plafond](limits.md) est atteint : lis le message (par IP, par session, global). `zoombots stats` montre la capacité de la machine.

## « code d'accès incorrect » (401) ou « trop d'essais » (429)
Le code est celui affiché par `zoombots server info` (`--reset-secrets` en génère un nouveau). Après 10 essais ratés, l'adresse est bloquée quelques minutes.

## « en-tête Host invalide » (400)
Le client envoie un `Host` mal formé (un proxy mal configuré ?) : il doit être un nom d'hôte ou une adresse, avec un port éventuel.

## « hôte non autorisé » (403)
Le nom utilisé dans l'URL n'est pas dans `ZOOMBOTS_ALLOWED_HOSTS` (`zoombots server start --domain …` le règle ; sans domaine, `*` est posé).

## Le certificat HTTPS n'est pas délivré
`zoombots server dns` : le domaine pointe-t-il vers ce serveur ? Les ports 80 et 443 sont-ils ouverts ? Lis `~/.local/state/zoombots/caddy.log`. Caddy s'arrête au démarrage si les ports 80/443 sont **déjà pris** (nginx, Apache, un autre Caddy) ou **interdits** à ton utilisateur : libère-les, ou relance `./install.sh --server` (droit `setcap`). Évite `sudo zoombots server start` : il changerait `HOME`, donc le dossier de données et `server.json` iraient dans `/root`. Un enregistrement `AAAA` qui pointe ailleurs bloque aussi le certificat ([Nom de domaine](domain.md#pas-à-pas)).

## La page se recharge « vide »
La session a expiré (onglet fermé plus de 15 s) : une nouvelle session démarre, et les bots de l'ancienne ont été retirés ([Sessions](sessions.md#présence-et-expiration)).

## YouTube : « échec du téléchargement »
yt-dlp est peut-être périmé : `.venv/bin/pip install -U yt-dlp`. Vérifie aussi la durée (max 15 min) et que le lien est un lien YouTube.

## Voir aussi

[Installation](installation.md) · [Configuration](configuration.md) · [Limitations connues](known-limitations.md)
