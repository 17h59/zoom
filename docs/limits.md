# Limites et quotas

Les plafonds protègent un petit serveur partagé. Tous sont réglables ([Configuration](configuration.md)) ; un dépassement renvoie une erreur lisible (HTTP 429, ou 503 pour un serveur plein).

## Par adresse IP

Actifs par défaut, **désactivés** avec `zoombots gui` (`ZOOMBOTS_MODE=local`) ; ils s'appliquent donc aussi à `zoombots start`, `serve` et Docker, où les utilisateurs locaux partagent l'adresse 127.0.0.1. Valeur 0 = illimité.

| Plafond | Défaut | Variable |
|---|---|---|
| Sessions simultanées | 3 | `ZOOMBOTS_IP_MAX_SESSIONS` |
| Bots actifs, toutes sessions de l'IP confondues | 10 | `ZOOMBOTS_IP_MAX_BOTS` |
| Créations de session par minute | 10 | `ZOOMBOTS_IP_SESSIONS_PER_MINUTE` |

**D'où vient l'adresse.** C'est celle de la connexion. `X-Forwarded-For` n'est lu que si la connexion vient d'un proxy de confiance (`ZOOMBOTS_TRUSTED_PROXIES`, défaut `127.0.0.1,::1`, donc Caddy sur la même machine). On remonte alors la liste **depuis la droite** jusqu'à la première adresse non fiable : un client ne peut pas se faire passer pour une autre adresse en envoyant son propre en-tête. Une connexion directe ignore l'en-tête.

**IPv6** : les plafonds portent sur le **/64** de l'adresse (un client IPv6 reçoit 2^64 adresses : changer d'adresse dans son /64 ne contourne rien). Un bot terminé (`ended`, `denied`, `error`, `left`) ne compte plus.

**Code d'accès** : 10 essais ratés par IP (/64) et par fenêtre de 5 minutes, puis HTTP 429 même avec le bon code. Ce compteur est distinct des plafonds ci-dessus, et n'existe que si un code d'accès est configuré.

## Par session

| Plafond | Défaut | Variable |
|---|---|---|
| Bots actifs | 10 (20 avec `zoombots gui`) | `ZOOMBOTS_SESSION_MAX_BOTS` |
| Sons | 20 | `ZOOMBOTS_SESSION_MAX_SOUNDS` |
| Taille d'un son | 25 Mo | `ZOOMBOTS_MAX_UPLOAD_MB` |
| Durée d'un son | 900 s | `ZOOMBOTS_MAX_SOUND_SECONDS` |
| Envois de sons simultanés | 2 | (fixe) |
| Téléchargements YouTube simultanés | 2 | (fixe) |
| WebSocket simultanés | 5 | (fixe) |
| Durée d'un téléchargement YouTube | 180 s | `ZOOMBOTS_YT_TIMEOUT` |

Fixes : 20 liens et 50 bots par déploiement, 1000 répétitions par action, corps JSON de 64 Ko, premier message WebSocket de 1 Ko.

**Déploiement tout ou rien** : la place restante sur le serveur (`ZOOMBOTS_MAX_BOTS`) est vérifiée **avant** de créer le moindre bot ; un déploiement refusé (429) ne laisse aucun bot créé « à moitié ».

## Globaux

| Plafond | Défaut | Variable |
|---|---|---|
| Bots actifs sur le serveur (tous utilisateurs et administration) | 20 | `ZOOMBOTS_MAX_BOTS` |
| Sessions simultanées sur le serveur | 20 (10 avec `zoombots gui`) | `ZOOMBOTS_MAX_SESSIONS` |
| Connexions simultanées de bots (hors attente d'admission) | 3 | `ZOOMBOTS_MAX_JOINS` |

Le plafond de connexions simultanées lisse le pic de CPU quand beaucoup de bots démarrent ; il est libéré avant la salle d'attente, qui ne bloque donc pas les autres bots.

## Combien de bots tient la machine

Chaque bot est un Chromium complet : compte de l'ordre de **300 à 500 Mo de RAM** et une part de CPU par bot (ordre de grandeur, à mesurer). `zoombots stats` affiche la RAM réellement consommée par bot et une **capacité estimée** : `(RAM disponible + RAM déjà prise par les bots) ÷ RAM moyenne par bot`, vide tant qu'aucun bot n'est actif. Sers-t'en pour régler `ZOOMBOTS_MAX_BOTS`.

## Limites de ce dispositif

- Une limite par IP est contournée par qui change d'adresse (VPN, plusieurs connexions) : elle borne l'accident et l'abus simple, pas un attaquant déterminé. Le plafond global `ZOOMBOTS_MAX_BOTS` reste le garde-fou.
- Derrière un NAT (une entreprise, une école), plusieurs utilisateurs partagent une adresse et donc ses plafonds.

## Voir aussi

[Sessions](sessions.md) · [Configuration](configuration.md) · [Sécurité](security.md)
