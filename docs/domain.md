# Nom de domaine

Avec un nom de domaine, `zoombots server start --domain …` obtient un certificat HTTPS tout seul. Ce qu'il faut faire à la main : un enregistrement DNS chez ton registrar, et ouvrir deux ports.

## Avant de commencer

| Il te faut | Comment |
|---|---|
| Un nom de domaine | Acheté chez n'importe quel registrar |
| Un serveur **joignable depuis Internet** avec une **adresse IP publique** | Un hébergeur (VPS), ou chez toi avec une box qui peut rediriger des ports. Derrière un opérateur en **CGNAT** (pas d'IP publique propre), ça ne marche pas : demande une IP publique ou prends un hébergeur |
| Caddy installé | `./install.sh --server` (sans lui, `server start --domain` répond « Caddy est introuvable ») |
| zoombots installé | [Installation](installation.md) |

## Pas à pas

**1. Crée un enregistrement DNS de type A** chez le registrar :

| Champ | Valeur |
|---|---|
| Type | `A` |
| Nom | `@` pour le domaine racine (`monsite.fr`), ou le sous-domaine voulu (`bots` pour `bots.monsite.fr`) |
| Valeur | l'adresse IP **publique** du serveur |
| TTL | le plus court proposé |

`zoombots server dns --domain bots.monsite.fr` affiche cet enregistrement avec l'IP détectée, puis dit si le domaine pointe déjà vers ce serveur (code de retour 0) ou non (1). Pour les suffixes à deux niveaux courants (`.co.uk`, `.com.au`…) il sait que le domaine racine est `monsite.co.uk` ; **si le nom affiché te semble faux, la règle du tableau prévaut** (`@` = ce que tu as acheté, sans préfixe).

**Nettoie les anciens enregistrements** du même nom : une page de parking, un `CNAME`, ou surtout un enregistrement **`AAAA`** (IPv6) qui pointe ailleurs empêchent la délivrance du certificat. Supprime-les, ou fais pointer l'`AAAA` vers ce serveur. (`server dns` ne vérifie que l'IPv4.)

**2. Ouvre les ports 80 et 443.** Let's Encrypt s'en sert pour valider le domaine, puis les visiteurs arrivent par le 443.

- Pare-feu du serveur, par exemple : `sudo ufw allow 80/tcp && sudo ufw allow 443/tcp`.
- Hébergeur (groupes de sécurité, pare-feu du panneau) : autoriser les mêmes ports.
- Chez toi : redirige les ports 80 et 443 de ta box vers la machine.
- Rien d'autre ne doit déjà écouter sur 80/443 (nginx, Apache, un autre Caddy) : sinon Caddy s'arrête (« occupés ou non autorisés »).

**3. Lance** `zoombots server start --domain bots.monsite.fr`.

**4. Vérifie le résultat** : ouvre `https://bots.monsite.fr/` (cadenas fermé, la page « Code d'accès » ou le GUI), et cherche une ligne « certificate obtained successfully » dans `caddy.log` (dossier de données, `~/.local/state/zoombots`). La propagation DNS prend de quelques minutes à quelques heures ; tant que le domaine ne pointe pas vers le serveur, Caddy réessaie tout seul.

## Ce que Caddy fait

Le Caddyfile est généré dans `<data>/Caddyfile` à chaque lancement :

- HTTPS automatique : certificat **Let's Encrypt** (ou **ZeroSSL** en secours, Caddy choisit), renouvelé seul ; `--email` pour les avis d'expiration ;
- API d'administration de Caddy coupée (`admin off`) ;
- taille des envois bornée à `ZOOMBOTS_MAX_UPLOAD_MB` + 5 Mo ;
- en-têtes `Server` et `Via` supprimés, `Strict-Transport-Security` (1 an) et `Referrer-Policy: no-referrer` ajoutés ;
- relais vers `127.0.0.1:<port>` ; il transmet l'adresse réelle du client dans `X-Forwarded-For`, que l'application croit parce que Caddy tourne sur la même machine ([Limites par IP](limits.md#par-adresse-ip)).

Les certificats sont rangés par Caddy dans `~/.local/share/caddy` (sauvegarde-les si tu veux éviter une nouvelle délivrance après une réinstallation).

## À savoir

- **HSTS** : les navigateurs retiennent un an que le domaine est en HTTPS. Repasser ce domaine en HTTP (`--no-domain`) sera bloqué chez les visiteurs qui l'ont déjà ouvert.
- **IP qui change** (box ou serveur sans IP fixe) : mets à jour l'enregistrement A à chaque changement, ou utilise le service de DNS dynamique de ton registrar.
- **Domaine** : en caractères ASCII (pour `café.fr`, utilise la forme `xn--caf-dma.fr`) ; une adresse IP ou `hôte:port` est refusée.
- **Derrière Cloudflare** (mode « proxy ») : l'IP que Caddy voit est celle de Cloudflare, pas celle de l'utilisateur ; tous les utilisateurs partagent alors les plafonds [par IP](limits.md#par-adresse-ip) (désactive-les, ou place les adresses de Cloudflare dans `ZOOMBOTS_TRUSTED_PROXIES` et lis son en-tête, ce qui n'est pas pris en charge ici). **Non testé.**

## Ce que le DNS révèle

Le nom de domaine pointe vers l'IP du serveur : n'importe qui peut la retrouver. Cela n'affecte pas ce que **Zoom** voit (c'est l'affaire du [proxy de sortie](privacy-ip.md)). Cacher l'IP du serveur aux utilisateurs du GUI demande un service qui fait écran devant le domaine (voir ci-dessus).

## Sans nom de domaine

C'est possible : `zoombots server start` sert en HTTP sur `http://<IP>:8000/` (**ouvre ce port** dans le pare-feu, ou celui de `--port`). Le code d'accès circule alors en clair ; voir [Serveur partagé](server.md#avec-ou-sans-domaine).

## Voir aussi

[Serveur partagé](server.md) · [Masquer l'IP](privacy-ip.md) · [Dépannage](troubleshooting.md#le-certificat-https-nest-pas-délivré)
