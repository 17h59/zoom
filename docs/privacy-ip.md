# Masquer l'adresse IP

Zoom, et l'hôte de la réunion, voient l'adresse d'où part le navigateur du bot. Sans précaution, c'est celle de la machine qui fait tourner zoombots. Cette page décrit comment faire sortir les bots par une autre adresse.

## Le principe : un proxy de sortie

```
zoombots gui --proxy http://hôte:port
zoombots server start --proxy http://hôte:port
ZOOMBOTS_PROXY=http://hôte:port zoombots start
```

Les navigateurs des bots passent alors par le proxy ; Zoom voit l'adresse du proxy.

| Format | Remarque |
|---|---|
| `http://hôte:port`, `https://hôte:port` | Identifiants possibles : `http://utilisateur:motdepasse@hôte:port` (caractères spéciaux en `%xx`) |
| `socks5://hôte:port` (ou `socks5h://`, équivalent) | **Sans** identifiants : Chromium ne gère pas l'authentification SOCKS5 |
| `http://[::1]:8080` | Adresse IPv6 entre crochets |

Pour un SOCKS5 avec mot de passe, utilise un proxy HTTP qui l'accepte, ou un tunnel local sans mot de passe (`ssh -D 1080 serveur`, puis `socks5://127.0.0.1:1080`). Les noms de domaine sont résolus par le proxy (pas de fuite DNS).

## Plusieurs proxies : une IP par bot

`ZOOMBOTS_PROXY=http://a:1,http://b:2,http://c:3` : chaque **lancement de navigateur** prend le proxy suivant, en rotation. Les bots n'ont donc pas tous la même adresse, et un bot qui **se reconnecte change d'adresse** (ce qui peut surprendre un hôte Zoom qui le verrait revenir d'ailleurs).

## Jamais de sortie directe

Avec `gui --proxy` ou `server start --proxy`, `ZOOMBOTS_REQUIRE_PROXY=1` est posé : **sans proxy, un bot ne démarre pas** (il ne sort pas avec l'IP de la machine). Un proxy injoignable fait échouer le bot, jamais sortir en direct. (Avec `ZOOMBOTS_PROXY` seul, sans `REQUIRE_PROXY`, l'absence de proxy reste permise.)

## Fuites évitées

| Fuite | Mesure |
|---|---|
| Adresse découverte par WebRTC | Avec proxy : `--force-webrtc-ip-handling-policy=disable_non_proxied_udp` ; sans proxy : `default_public_interface_only` (ne révèle pas le réseau local) |
| Trafic UDP/QUIC hors proxy | `--disable-quic` ; le média passe en TCP via le proxy |
| DNS | Résolu par le proxy |
| Messages d'erreur montrés aux utilisateurs | Adresses IPv4 et IPv6 (y compris abrégées : `2001:db8::1`, `fe80::1%eth0`) remplacées par `[ip]` ; hôte, `hôte:port`, identifiant et mot de passe du proxy par `[proxy]` (sans tenir compte de la casse) ; passcodes par `***`. Un identifiant de proxy de moins de 3 caractères n'est pas masqué (il détruirait des mots), et un identifiant plus long n'est masqué que comme mot entier |
| Téléchargements YouTube | Passent par le proxy HTTP(S) configuré ; avec `ZOOMBOTS_REQUIRE_PROXY=1` et sans proxy HTTP(S) utilisable (un SOCKS ne suffit pas pour yt-dlp), le téléchargement est refusé |
| Secrets hérités | Chromium et les autres sous-processus ne reçoivent ni `ZOOMBOTS_PROXY`, ni le jeton, ni le code d'accès |
| Bannière du serveur | Pas d'en-tête `Server` (ni uvicorn ni Caddy) |
| Administration | `/admin`, `/bots`… répondent 404 hors de la machine ([Sécurité](security.md#administration-invisible-de-lextérieur)) |

## Vérifier

```
zoombots check-ip                     # proxy(s) configuré(s)
zoombots check-ip --proxy http://hôte:port
```

La commande affiche l'IP directe de la machine puis l'IP vue à travers chaque proxy : `✔ masquée` ou `✘ identique ou injoignable` (code de retour 1). `--proxy` est répétable. Elle utilise `curl` et un service public d'écho d'IP (ipify, ifconfig.me, icanhazip), en IPv4 seulement ; le test « direct » ignore `HTTPS_PROXY` de l'environnement, et les identifiants du proxy passent à `curl` par son entrée standard (pas par `ps`) et ne sont pas affichés.

## Ce que ça ne couvre pas

- Le **fournisseur du proxy** voit le trafic et l'adresse de la machine.
- Le **nom de domaine** d'un serveur partagé révèle son IP par le DNS : voir [Nom de domaine](domain.md#ce-que-le-dns-révèle).
- Les **autres sorties du serveur** (mises à jour, `check-ip`, ACME de Caddy) ne passent pas par le proxy.
- Un hôte Zoom peut **reconnaître un bot** à son nom, son comportement ou son empreinte de navigateur.
- Le comportement du client web Zoom derrière un proxy HTTP (média attendu en TCP/WebSocket) **n'est pas validé** : voir [Tests et validation](testing.md).

## Voir aussi

[Serveur partagé](server.md) · [Sécurité](security.md) · [Configuration](configuration.md#réseau-et-confidentialité)
