# Tests et validation

```
pip install -e '.[dev]'
pytest
```

La suite compte environ 185 tests (quelques dizaines de secondes). Les tests sur navigateur utilisent le Chromium de Playwright, ou un binaire trouvé dans `/opt/pw-browsers`, et sont ignorés s'il n'y en a pas.

## Ce qui est testé automatiquement

| Domaine | Fichiers | Contenu |
|---|---|---|
| Liens Zoom | `test_meeting.py` | ID, liens, domaines, hôtes étrangers refusés, ID trop long, encodage du passcode |
| État d'une page | `test_bot_state.py`, `test_page_integration.py` | Classification, **faux positifs de chat**, salle d'attente, surveillance sur **vrai Chromium** avec de fausses pages, bascule du micro |
| Reconnexion | `test_reconnect.py` | Reconnexion, bornage, sessions stables, annulation sans fuite, sémaphore libéré avant l'attente |
| Gestion des bots | `test_manager.py` | Persistance (cycle démarrer → arrêter → redémarrer), entrées invalides, `broadcast`, noms de sons |
| Audio | `test_audio.py` | Pipeline avec un `paplay` factice, échec journalisé, arrêt, plafond de lectures, rollback PulseAudio, orphelins, instances |
| Sessions du GUI | `test_sessions.py` | Répartition, isolation, quotas, actions (séquence, attente d'admission, arrêt, remplacement, échecs), uploads, YouTube (faux yt-dlp), expiration |
| API et WebSocket | `test_api.py`, `test_gui_api.py` | Host, CSRF, jeton, CSP, code d'accès, isolation, fermeture du WebSocket |
| **GUI réel** | `test_gui_browser.py` | Vrai serveur uvicorn et vrai Chromium, faux bots : déploiement, envoi d'un son, action, panneau d'un bot, glisser-déposer, rechargement, second onglet isolé, fermeture de l'onglet |
| Confidentialité | `test_privacy.py` | Proxy (formats, rotation, échec fermé), options Chromium, nettoyage des IP, IP des clients |
| Plafonds par IP | `test_ip_limits.py` | Sessions, bots, débit ; mode local |
| Déploiement | `test_deploy.py` | Configuration, domaines, Caddyfile, DNS, `check-ip`, commandes `gui` et `server`, administration cachée |
| CLI | `test_cli.py` | Préfixes, codes de retour, proxy ignoré, PID recyclé |

## Validé à la main

- `install.sh` (dont `--server` : téléchargement de Caddy et `setcap`).
- `zoombots server start --domain …` avec un vrai Caddy : démarrage, Caddyfile généré, arrêt de Caddy par `shutdown`.
- Plafonds par IP, code d'accès et `X-Forwarded-For` falsifié, à travers Caddy.
- `zoombots gui`, `status`, `shutdown`, `check-ip` (échec d'un proxy injoignable).
- Cycle complet `start → spawn → rm → shutdown`, y compris l'échec de lancement de Chromium (reconnexions, passage en `error`, aucun module PulseAudio résiduel).

## **Non validé**

| Point | Pourquoi |
|---|---|
| Le **parcours de connexion sur un vrai Zoom** : sélecteurs (`#input-for-name`, `#input-for-pwd`, boutons Join/Audio/Leave, `aria-label` du micro) et textes de détection d'état | Aucun accès à une réunion ; ce sont des hypothèses. À vérifier avec `zoombots screenshot` et `zoombots logs` |
| Le **son réellement reçu** par Zoom | La capture du micro virtuel était silencieuse dans l'environnement de développement, même pour un `paplay` direct : non imputable au code, mais non vérifié |
| Chromium avec `PULSE_SOURCE` sur une vraie réunion | idem |
| Le **client web Zoom derrière un proxy HTTP** (média attendu en TCP/WebSocket) et `check-ip` avec un vrai proxy | Pas de proxy disponible |
| Un **vrai certificat** Let's Encrypt | Pas de nom de domaine |
| Le **téléchargement YouTube réel** | Pas d'accès à YouTube ; seul le pipeline autour de yt-dlp est testé |
| La **charge** de plusieurs utilisateurs sur de vrais Chromium | Non mesurée |
| Le **Dockerfile** et le compose, `install.sh` hors Debian/Ubuntu, le mode « proxy » de Cloudflare | Non exécutés |

## Voir aussi

[Limitations connues](known-limitations.md) · [Dépannage](troubleshooting.md)
