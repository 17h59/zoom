# Architecture

Un « bot » est un Chromium qui charge le client web Zoom, associé à un micro virtuel PulseAudio dédié. L'audio d'un fichier est décodé par ffmpeg et injecté dans ce micro, que le navigateur utilise comme périphérique d'entrée.

```
Navigateurs ──HTTP+WebSocket──►  GUI (/)  ──►  SessionManager ── Session (1 par onglet)
CLI `zoombots` ──HTTP──►  API admin + dashboard (/admin)           │  sons, actions, bots
                              │                                    │
                              └──────────────┬─────────────────────┘
                         BotManager ── Playwright (un driver partagé)
                              │          ├─ persistance (state.json, 0600)
                              │          └─ sémaphore de connexions
                           Bot (1 par instance, possède sa tâche asyncio)
                    ├─ Chromium (process dédié, env PULSE_SOURCE=zb_<id>_mic)
                    └─ VirtualMic
                         null-sink zb_<id>  ──monitor──►  remap-source zb_<id>_mic
                              ▲
                  ffmpeg (décodage) ─pipe─► paplay --device=zb_<id>
```

## Modules

| Fichier | Rôle |
|---|---|
| `zoombots/meeting.py` | `Meeting`, `parse_meeting` : ID ou lien Zoom, liste blanche de domaines ([détail](bot-lifecycle.md#domaines-zoom)) |
| `zoombots/bot.py` | `Bot` : connexion, surveillance, reconnexion, micro, lecture, furtivité et son d'origine ([détail](bot-lifecycle.md), [furtivité](anti-ban.md)) |
| `zoombots/audio.py` | `VirtualMic` : modules PulseAudio, pipeline de lecture ([détail](audio.md)) |
| `zoombots/manager.py` | `BotManager` : registre, driver Playwright partagé, respawn sur ban ([détail](anti-ban.md#remplacement-automatique-après-expulsion-respawn-on-ban)), persistance, plafond global |
| `zoombots/state.py` | Persistance JSON atomique des bots d'administration |
| `zoombots/stats.py` | RAM et CPU par bot, lus dans `/proc` |
| `zoombots/sessions.py` | Sessions du GUI : bots, sons, actions, présence, quotas ([détail](sessions.md)) |
| `zoombots/gui_api.py` | Routes `/api` et WebSocket du GUI ([détail](api.md#api-du-gui)) |
| `zoombots/api.py` | API d'administration, garde de sécurité, service des pages ([détail](api.md#api-dadministration), [sécurité](security.md)) |
| `zoombots/net.py` | Proxy de sortie, durcissement WebRTC, nettoyage des IP, IP réelle des clients ([détail](privacy-ip.md)) |
| `zoombots/deploy.py` | Configuration du serveur partagé, Caddyfile, DNS, vérification d'IP ([détail](server.md)) |
| `zoombots/cli.py` | La CLI ([détail](cli.md)) |
| `zoombots/log.py` | Logs JSON et journal d'événements par bot |
| `zoombots/static/` | GUI (`gui.html`, `gui.css`, `gui.js`) et dashboard d'administration (`index.html`, `app.js`), sans `innerHTML` |

## Deux familles de bots

- **Bots d'administration** : créés par la CLI ou l'API historique, persistés dans `state.json` et relancés au redémarrage du serveur.
- **Bots de session** : créés par le GUI, rattachés à un onglet (`owner` = identifiant de session), jamais persistés, retirés quand l'onglet se ferme. L'administration les voit et peut les piloter, mais ses actions de masse (`broadcast`, `rm --finished`) les ignorent.

## Choix techniques

- **Un process Chromium par bot.** `PULSE_SOURCE` est fixé au lancement du navigateur : un Chromium partagé partagerait un seul micro. La mutualisation se fait au niveau du conteneur (un PulseAudio, plusieurs bots), pas du navigateur ; le coût reste linéaire. Chromium est allégé : sans GPU, sans sortie audio, fenêtre 800×600.
- **Furtivité sous Xvfb** : pour éviter la détection du mode headless par Zoom, Chromium tourne par défaut en mode fenêtré sous un serveur X virtuel Xvfb avec suppression des drapeaux d'automatisation ([Furtivité et anti-ban](anti-ban.md#furtivité-maximale-stealth)).
- **`ffmpeg | paplay`** plutôt que `ffmpeg -f pulse` : le muxer pulse perd la fin du flux, `paplay` vide son buffer. Voir [Audio](audio.md).
- **Le bot possède sa tâche asyncio** (`Bot.start`, `Bot.leave`) : annuler un bot en pleine connexion ne laisse ni navigateur ni module PulseAudio.
- **Écriture de l'état groupée** : plusieurs changements dans le même tour de boucle donnent une seule écriture. `shutdown` fige la liste : les bots actifs sont relancés au prochain démarrage.
- **Routes `async`** : elles s'exécutent dans la boucle, sans accès concurrent au registre. `/stats` tourne dans un thread (balayage de `/proc`).
- **Démarrage** : les modules PulseAudio orphelins de l'instance sont déchargés (voir [Audio](audio.md#plusieurs-zoombots-sur-un-même-pulseaudio)), les entrées d'état invalides sont ignorées sans bloquer.

## Voir aussi

[Cycle de vie d'un bot](bot-lifecycle.md) · [Furtivité et anti-ban](anti-ban.md) · [Sessions](sessions.md) · [Sécurité](security.md) · [Configuration](configuration.md)
