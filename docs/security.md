# Sécurité

zoombots pilote des navigateurs, lit des captures d'écran et accepte des fichiers : il est protégé contre le navigateur d'un visiteur malveillant et contre un utilisateur du GUI qui voudrait gêner les autres. Les protections sont par couche.

## Administration invisible de l'extérieur

Avec `ZOOMBOTS_PUBLIC_ONLY=1` (posé par [`server start`](server.md)), toute route qui n'est pas du GUI (`/`, `/static/`, `/api/`, `/health`) répond **404** si le client n'est pas la machine elle-même (adresse réelle, voir [Limites par IP](limits.md#par-adresse-ip)), même avec le bon jeton : l'existence de l'administration n'est pas révélée. Cela inclut `/docs`, `/redoc` et `/openapi.json`. En plus, le jeton `ZOOMBOTS_TOKEN` reste exigé pour ces routes.

## Écouter hors de la machine

`ZOOMBOTS_HOST` autre que `127.0.0.1`/`localhost`/`::1` **sans `ZOOMBOTS_TOKEN` fait échouer le démarrage**. Avec un domaine, l'application n'écoute que sur `127.0.0.1` (Caddy est la porte). `zoombots gui` écoute sur `127.0.0.1` **et** génère un jeton d'administration aléatoire à chaque lancement (rangé dans `run.json`, en 0600) : un autre utilisateur de la machine qui joindrait `127.0.0.1:8000` ne pilote pas l'API d'administration.

## Attaques depuis le navigateur

- **DNS rebinding** : l'en-tête `Host` doit être bien formé (sinon HTTP 400), puis égal à `localhost`, `127.0.0.1`, `[::1]` ou figurer dans `ZOOMBOTS_ALLOWED_HOSTS` (`*` accepte tout : serveur sans domaine). Le WebSocket vérifie aussi `Host` et que `Origin` correspond au `Host`.
- **Chemin jugé sur la route réelle** : les décisions de sécurité (route publique ou non, administration, jeton) s'appuient sur le chemin effectivement routé (`scope["path"]`), jamais sur une URL reconstruite à partir de `Host` : avec d'anciennes versions de Starlette, un `Host: x/api/` faisait passer `/bots` pour une route publique. `pyproject.toml` exige Starlette ≥ 1.3, et le `Host` mal formé est refusé en plus.
- <a id="csrf"></a>**CSRF** : toute requête autre que GET/HEAD doit porter `X-Zoombots` (le GUI, le dashboard et la CLI l'envoient). Un en-tête personnalisé impose un *preflight* CORS que le serveur ne satisfait jamais ; `Sec-Fetch-Site: cross-site` est refusé.
- **XSS** : le GUI et le dashboard construisent le DOM avec `textContent` (jamais `innerHTML`). **Toutes** les réponses, refus et redirections compris, portent une CSP restrictive (`script-src 'self'`, `connect-src 'self'`, `frame-ancestors 'none'`…), `X-Content-Type-Options: nosniff` et `Cache-Control: no-store`.

## Authentification

| Mécanisme | Protège | Détail |
|---|---|---|
| `ZOOMBOTS_ACCESS_CODE` | La création de session du GUI | Comparaison à temps constant (valeurs non ASCII comprises) ; **10 essais ratés par IP et par 5 minutes**, puis HTTP 429 même avec le bon code ; 8 caractères minimum avec `--code`. Sans code configuré, quiconque atteint le serveur peut créer une session |
| Identifiant de session | Tout `/api` | 192 bits aléatoires, dans `X-Session` |
| `ZOOMBOTS_TOKEN` | Administration | `Authorization: Bearer`, ou cookie `HttpOnly; SameSite=Strict` posé par `/admin?token=…` |

Les jetons circulent en clair en HTTP : sur un réseau non fiable, utilise le HTTPS ([Nom de domaine](domain.md)).

## Déni de service

- **Corps de requête bornés avant lecture** : 64 Ko pour le JSON, la taille maximale d'un son (+ 1 Mo) pour `PUT /api/sounds`, HTTP 413 au-delà, avec ou sans `Content-Length`. Valable sans Caddy devant.
- **WebSocket** : premier message limité à 1 Ko (et 4 Ko au total côté serveur), 5 connexions par session. Il n'y a pas de plafond de connexions WebSocket *non authentifiées* par IP : c'est le rôle du pare-feu ou du proxy.
- Plafonds de sessions, de bots, de sons et d'envois : [Limites et quotas](limits.md).

## Entrées utilisateur

- **Liens Zoom** : liste blanche de domaines ; pas de redirection vers un hôte arbitraire.
- **Envois de sons** : lus en flux, plafonnés en octets, extension en liste blanche, nom nettoyé (jamais utilisé comme chemin), contenu validé par `ffprobe` (protocoles réseau interdits).
- **YouTube** : seulement des liens de vidéo des hôtes YouTube ; yt-dlp restreint à l'extracteur YouTube, un seul élément, `--ignore-config`, taille et durée plafonnées, sortie confinée au dossier de la session ([détail](sessions.md#sons)).
- **Noms de sons** de la bibliothèque : pas de séparateur de chemin, pas de caractère nul, extension obligatoire ; les liens symboliques sortant du dossier sont refusés.
- **Caddyfile** : domaine (`fullmatch`), adresse e-mail et port sont validés avant d'être écrits : aucune directive ne peut être injectée.
- **État sur disque** : les identifiants de bots lus dans `state.json` sont validés (8 caractères hexadécimaux) avant d'être utilisés dans PulseAudio ou dans un chemin.
- **Sous-processus** : jamais de shell, arguments passés en liste, groupe de processus tué en entier au délai.

## Secrets

- `state.json` (passcodes), `server.json` (code, jeton, proxy), `run.json` (jeton local), `server.pid`, `server.log`, `Caddyfile`, `caddy.log` sont en **0600**, le dossier de données en **0700** ; les écritures sont atomiques.
- Le journal d'accès d'uvicorn est **désactivé** : `/admin?token=…` n'apparaît dans aucun fichier.
- Les sous-processus (Chromium, ffmpeg, ffprobe, yt-dlp, pactl) ne reçoivent **pas** `ZOOMBOTS_TOKEN`, `ZOOMBOTS_ACCESS_CODE` ni `ZOOMBOTS_PROXY` ; yt-dlp reçoit seulement le proxy HTTP(S), par son environnement et non par sa ligne de commande.
- Les passcodes sont masqués dans les erreurs, l'historique et les journaux ; les adresses IP et les informations de proxy aussi ([Masquer l'IP](privacy-ip.md#fuites-évitées)).
- `zoombots check-ip` passe le proxy à `curl` par sa configuration lue sur l'entrée standard : les identifiants n'apparaissent pas dans `ps`.

## Chromium et son sandbox

Playwright désactive par défaut le sandbox de Chromium. zoombots l'**active** (`ZOOMBOTS_SANDBOX=1`) quand il ne tourne pas en root, car il isole la page Zoom du reste du serveur si le moteur de rendu est compromis. En root (Chromium le refuse) et dans le conteneur Docker fourni (`ZOOMBOTS_SANDBOX=0`), il est désactivé : l'isolation est alors celle du conteneur, ou celle de l'utilisateur système. Lance zoombots avec un utilisateur dédié sans droits.

## Isolation entre utilisateurs

[Sessions](sessions.md#isolation) : chaque ressource est rattachée à sa session ; un identifiant étranger donne 404.

## Ce qui n'est pas couvert

- Pas de comptes ni de journal d'audit : le code d'accès est partagé.
- Les plafonds [par IP](limits.md#limites-de-ce-dispositif) se contournent en changeant d'adresse (en IPv6, ils portent sur le /64).
- Le contenu envoyé (sons) n'est pas modéré.
- Utiliser des bots dans une réunion à laquelle on n'est pas autorisé à participer relève des conditions d'utilisation de Zoom et de la loi, pas de ce logiciel.

## Voir aussi

[Serveur partagé](server.md) · [API HTTP](api.md) · [Limitations connues](known-limitations.md)
