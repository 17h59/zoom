# Sécurité

zoombots pilote des navigateurs, lit des captures d'écran et accepte des fichiers : il est protégé contre le navigateur d'un visiteur malveillant et contre un utilisateur du GUI qui voudrait gêner les autres. Les protections sont par couche.

## Administration invisible de l'extérieur

Avec `ZOOMBOTS_PUBLIC_ONLY=1` (posé par [`server start`](server.md)), toute route qui n'est pas du GUI (`/`, `/static/`, `/api/`, `/health`) répond **404** si le client n'est pas la machine elle-même (adresse réelle, voir [Limites par IP](limits.md#par-adresse-ip)), même avec le bon jeton : l'existence de l'administration n'est pas révélée. En plus, le jeton `ZOOMBOTS_TOKEN` reste exigé pour ces routes.

## Écouter hors de la machine

`ZOOMBOTS_HOST` autre que `127.0.0.1`/`localhost`/`::1` **sans `ZOOMBOTS_TOKEN` fait échouer le démarrage**. Avec un domaine, l'application n'écoute que sur `127.0.0.1` (Caddy est la porte).

## Attaques depuis le navigateur

- **DNS rebinding** : l'en-tête `Host` doit être `localhost`, `127.0.0.1`, `[::1]` ou figurer dans `ZOOMBOTS_ALLOWED_HOSTS` (`*` accepte tout : serveur sans domaine). Le WebSocket vérifie aussi `Host` et que `Origin` correspond au `Host`.
- <a id="csrf"></a>**CSRF** : toute requête autre que GET/HEAD doit porter `X-Zoombots` (le GUI, le dashboard et la CLI l'envoient). Un en-tête personnalisé impose un *preflight* CORS que le serveur ne satisfait jamais ; `Sec-Fetch-Site: cross-site` est refusé.
- **XSS** : le GUI et le dashboard construisent le DOM avec `textContent` (jamais `innerHTML`). Toutes les réponses portent une CSP restrictive (`script-src 'self'`, `frame-ancestors 'none'`…), `X-Content-Type-Options: nosniff` et `Cache-Control: no-store`.

## Authentification

| Mécanisme | Protège | Détail |
|---|---|---|
| `ZOOMBOTS_ACCESS_CODE` | La création de session du GUI | Comparaison à temps constant ; sans code configuré, quiconque atteint le serveur peut créer une session |
| Identifiant de session | Tout `/api` | 192 bits aléatoires, dans `X-Session` |
| `ZOOMBOTS_TOKEN` | Administration | `Authorization: Bearer`, ou cookie `HttpOnly; SameSite=Strict` posé par `/admin?token=…` |

Les jetons circulent en clair en HTTP : sur un réseau non fiable, utilise le HTTPS ([Nom de domaine](domain.md)).

## Entrées utilisateur

- **Liens Zoom** : liste blanche de domaines ; pas de redirection vers un hôte arbitraire.
- **Envois de sons** : lus en flux, plafonnés en octets, extension en liste blanche, nom nettoyé (jamais utilisé comme chemin), contenu validé par `ffprobe`.
- **YouTube** : seulement les hôtes YouTube ; `--ignore-config`, `--no-playlist`, taille et durée plafonnées ; sortie confinée au dossier de la session.
- **Noms de sons** de la bibliothèque : pas de séparateur de chemin, pas de caractère nul, extension obligatoire ; les liens symboliques sortant du dossier sont refusés.
- **État sur disque** : les identifiants de bots lus dans `state.json` sont validés (8 caractères hexadécimaux) avant d'être utilisés dans PulseAudio ou dans un chemin.
- **Sous-processus** : jamais de shell ; arguments passés en liste.

## Secrets

`state.json` (passcodes) et `server.json` (code, jeton, proxy) sont écrits en **0600**, de façon atomique. Les passcodes sont masqués dans les erreurs, l'historique et les journaux ; les adresses IP et les informations de proxy aussi ([Masquer l'IP](privacy-ip.md#fuites-évitées)).

## Isolation entre utilisateurs

[Sessions](sessions.md#isolation) : chaque ressource est rattachée à sa session ; un identifiant étranger donne 404.

## Ce qui n'est pas couvert

- Pas de comptes ni de journal d'audit : le code d'accès est partagé.
- Les plafonds [par IP](limits.md#limites-de-ce-dispositif) se contournent en changeant d'adresse.
- Le contenu envoyé (sons) n'est pas modéré.
- Utiliser des bots dans une réunion à laquelle on n'est pas autorisé à participer relève des conditions d'utilisation de Zoom et de la loi, pas de ce logiciel.

## Voir aussi

[Serveur partagé](server.md) · [API HTTP](api.md) · [Limitations connues](known-limitations.md)
