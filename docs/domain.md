# Nom de domaine

Avec un nom de domaine, `zoombots server start --domain …` obtient un certificat HTTPS tout seul. Aucune connaissance de Caddy ni de certificats n'est requise ; seul le DNS demande une action chez ton registrar.

## Pas à pas

**1. Crée un enregistrement DNS de type A** chez le registrar du domaine (le site où tu l'as acheté, quel qu'il soit) :

| Champ | Valeur |
|---|---|
| Type | `A` |
| Nom | `@` pour le domaine racine (`monsite.fr`), ou le sous-domaine voulu (`bots` pour `bots.monsite.fr`) |
| Valeur | l'adresse IP **publique** du serveur |
| TTL | le plus court proposé |

`zoombots server dns --domain bots.monsite.fr` affiche exactement cet enregistrement avec l'IP détectée, puis dit si le domaine pointe déjà vers ce serveur (code de retour 0) ou non (1).

**2. Ouvre les ports 80 et 443** : pare-feu du serveur, et box ou hébergeur si besoin (redirection de ports). Let's Encrypt en a besoin pour délivrer le certificat.

**3. Lance** `zoombots server start --domain bots.monsite.fr`.

La propagation DNS prend de quelques minutes à quelques heures. Tant que le domaine ne pointe pas vers le serveur, Caddy réessaie tout seul : regarde `caddy.log` dans le dossier de données (`~/.local/state/zoombots`).

## Ce que Caddy fait

Le Caddyfile est généré dans `<data>/Caddyfile` à chaque lancement :

- HTTPS automatique (Let's Encrypt), avec `--email` pour les avis d'expiration ;
- API d'administration de Caddy coupée (`admin off`) ;
- taille des envois bornée à `ZOOMBOTS_MAX_UPLOAD_MB` + 5 Mo ;
- en-tête `Server` supprimé, `Strict-Transport-Security` et `Referrer-Policy: no-referrer` ajoutés ;
- relais vers `127.0.0.1:<port>` ; il transmet l'adresse réelle du client dans `X-Forwarded-For`, que l'application croit parce que Caddy tourne sur la même machine ([Limites par IP](limits.md#par-adresse-ip)).

## Ce que le DNS révèle

Le nom de domaine pointe vers l'IP du serveur : n'importe qui peut la retrouver. Cela n'affecte pas ce que **Zoom** voit (c'est l'affaire du [proxy de sortie](privacy-ip.md)). Pour cacher aussi l'IP du serveur aux utilisateurs du GUI, il faut un service qui fait écran devant le domaine (par exemple le mode « proxy » de Cloudflare). **Non testé ici.**

## Sans nom de domaine

C'est possible : `zoombots server start` sert en HTTP sur `http://<IP>:8000/`. Le code d'accès circule alors en clair ; voir [Serveur partagé](server.md#avec-ou-sans-domaine).

## Voir aussi

[Serveur partagé](server.md) · [Masquer l'IP](privacy-ip.md) · [Dépannage](troubleshooting.md#le-certificat-https-nest-pas-délivré)
