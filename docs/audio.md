# Audio

Chaque bot a un **micro virtuel** PulseAudio ; le navigateur du bot l'utilise comme micro. Jouer un son revient à l'écrire dans ce micro.

```
 fichier ──ffmpeg (décodage, volume)──pipe──► paplay --device=zb_<id> ──► null-sink zb_<id>
                                                                              │ monitor
                                              Chromium (PULSE_SOURCE=zb_<id>_mic) ◄── remap-source zb_<id>_mic
```

## Micro virtuel

- `module-null-sink` (`zb_<id>`) reçoit le son ; `module-remap-source` (`zb_<id>_mic`) expose son *monitor* comme micro.
- `<id>` est l'identifiant du bot (8 caractères hexadécimaux).
- Si le chargement échoue en route, les modules déjà chargés sont déchargés (pas de module à moitié créé).
- Fermer le bot décharge ses modules.

## Lecture d'un son

- Deux processus reliés par un pipe, **sans shell** : `ffmpeg` décode tous les formats (mp3, wav, ogg, flac, m4a, opus…) et règle le volume (0 à 2) ; `paplay` joue. Chacun dans son groupe de processus, arrêtés par SIGTERM.
- `paplay` plutôt que `ffmpeg -f pulse` : le muxer pulse de ffmpeg perd la fin du flux à la sortie, `paplay` vide son buffer.
- Jusqu'à **4 lectures simultanées par bot** ; au-delà, erreur.
- Une lecture en échec (fichier corrompu…) est consignée avec la fin de la sortie d'erreur de ffmpeg ; `play_wait` lève alors une erreur et une [action](gui.md#3-action) la compte comme un échec.
- `VirtualMic.playing` reste vrai tant que `paplay` vide son buffer, ce qui correspond à du son encore en cours.

## Sons disponibles

- **Dossier `sounds/`** du serveur (`ZOOMBOTS_SOUNDS`) : extensions mp3, wav, ogg, flac, m4a, opus. Noms validés : pas de séparateur de chemin, pas de caractère nul, extension obligatoire.
- **Sons de session** du [GUI](gui.md) : envoyés ou téléchargés, stockés dans le dossier de la session et supprimés avec elle.

## Plusieurs zoombots sur un même PulseAudio

Le serveur PulseAudio est partagé par tout ce qui tourne sous ton compte. Au démarrage, zoombots décharge les modules orphelins que laisserait un crash (`zb_<8 hex>` et `zb_<8 hex>_mic`), et **seulement ceux-là** : les modules d'autres applications ne sont pas touchés.

Pour faire tourner **plusieurs zoombots** sur la même machine, donne un nom à chacun : `ZOOMBOTS_INSTANCE=a` (1 à 16 caractères `[a-z0-9]`). Les modules s'appellent alors `zb_a_<id>`, le dossier de données par défaut devient `~/.local/state/zoombots-a`, et chaque instance ne nettoie, ne mesure et n'arrête que ses propres ressources.

```
ZOOMBOTS_INSTANCE=a ZOOMBOTS_PORT=8001 zoombots start
ZOOMBOTS_INSTANCE=b ZOOMBOTS_PORT=8002 zoombots start
```

Les commandes suivantes de la CLI doivent être lancées avec les mêmes variables. **Sans nom d'instance, deux zoombots se déchargent mutuellement leurs micros au démarrage.**

## Mesure des ressources

`zoombots stats` retrouve les processus d'un bot grâce à `PULSE_SOURCE=zb_<id>_mic`, héritée par tous les processus de son Chromium (navigateur, rendus, GPU), et lit leur RAM et CPU dans `/proc`.

## Voir aussi

[Cycle de vie d'un bot](bot-lifecycle.md) · [Architecture](architecture.md) · [Dépannage](troubleshooting.md#pas-de-son-dans-la-réunion)
