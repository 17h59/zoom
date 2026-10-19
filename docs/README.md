# Documentation de zoombots

zoombots lance des bots sur le client web de Zoom : un Chromium (Playwright) par bot, un micro virtuel PulseAudio par bot, un soundboard. On les pilote par une interface web (GUI), une API HTTP ou une CLI.

Chaque page traite un seul sujet. Commence par la colonne « Tu veux… ».

## Tu veux…

| Tu veux… | Lis |
|---|---|
| Installer et lancer en 2 minutes | [Installation](installation.md) puis [Modes de lancement](modes.md) |
| Utiliser l'interface web | [Le GUI](gui.md) |
| Piloter en ligne de commande | [La CLI](cli.md) |
| Héberger pour d'autres utilisateurs | [Serveur partagé](server.md), puis [Nom de domaine](domain.md) |
| Cacher l'adresse IP de ta machine | [Masquer l'IP](privacy-ip.md) |
| Savoir ce qui est limité, et pourquoi | [Limites et quotas](limits.md) |
| Régler un comportement | [Configuration](configuration.md) |
| Comprendre un problème | [Dépannage](troubleshooting.md) |

## Référence

| Page | Contenu |
|---|---|
| [Architecture](architecture.md) | Vue d'ensemble, modules, choix techniques |
| [Cycle de vie d'un bot](bot-lifecycle.md) | Connexion, salle d'attente, surveillance, reconnexion, micro, domaines Zoom |
| [Audio](audio.md) | Micro virtuel PulseAudio, lecture des sons, instances |
| [Sessions du GUI](sessions.md) | Une session par onglet : présence, sons, actions, expiration |
| [API HTTP](api.md) | API d'administration et API du GUI |
| [Sécurité](security.md) | Modèle de menace et protections |
| [Docker](docker.md) | Image et `docker compose` |
| [Tests et validation](testing.md) | Ce qui est testé, ce qui ne l'est pas |
| [Limitations connues](known-limitations.md) | Ce qui ne marche pas, ou pas encore |

## Conventions

- Les commandes `zoombots …` supposent que `install.sh` a été exécuté.
- Les variables d'environnement commencent par `ZOOMBOTS_` ; la liste complète est dans [Configuration](configuration.md).
- Un point marqué **non validé** n'a pas pu être vérifié face à un vrai Zoom, un vrai certificat ou un vrai proxy : voir [Tests et validation](testing.md).
