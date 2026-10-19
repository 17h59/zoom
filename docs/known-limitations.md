# Limitations connues

## Zoom

- Le parcours de connexion repose sur des sélecteurs et des textes **non vérifiés** face à un vrai Zoom ; une évolution de la page de Zoom peut le casser ([Tests et validation](testing.md)).
- Les captcha, les réunions qui exigent un compte, un enregistrement ou une inscription ne sont pas gérés.
- La détection d'une expulsion repose sur des messages en anglais et en français.
- Un hôte Zoom peut reconnaître un bot (nom, comportement) et l'expulser.

## Audio

- Le son n'est pas vérifié de bout en bout dans un vrai Zoom.
- `VirtualMic.playing` reste vrai tant que `paplay` vide son buffer.

## Ressources

- Un Chromium complet par bot : de l'ordre de 300 à 500 Mo de RAM chacun, coût linéaire ([Limites](limits.md#combien-de-bots-tient-la-machine)).
- La mutualisation n'est possible qu'au niveau du conteneur (un PulseAudio), pas du navigateur ([Architecture](architecture.md#choix-techniques)).

## Sessions et état

- Après un redémarrage, les bots d'administration rejoignent la réunion comme de nouveaux participants (mute et lectures en cours non restaurés). Les bots des sessions du GUI **ne survivent pas** au redémarrage du serveur.
- Les bots terminés d'une session restent affichés jusqu'à ce que l'utilisateur les retire (« retirer les terminés ») ou ferme l'onglet.
- Un seul serveur à la fois par machine, sauf avec `ZOOMBOTS_INSTANCE` ; et la configuration du mode serveur (`server.json`) n'est pas séparée par instance ([Modes](modes.md#après-le-lancement)).
- Il n'y a pas de comptes utilisateurs : l'identité d'un utilisateur est son onglet. Une session meurt 15 s après la suspension de son onglet par le navigateur (onglet en arrière-plan sur téléphone).

## Réseau et confidentialité

- Les plafonds par IP se contournent en changeant d'adresse, et pénalisent les utilisateurs derrière un même NAT ([Limites](limits.md#limites-de-ce-dispositif)). Il n'y a pas de plafond de connexions WebSocket non authentifiées par IP.
- Chromium ne gère pas l'authentification SOCKS5, et yt-dlp n'est branché que sur un proxy HTTP(S) ([Masquer l'IP](privacy-ip.md)).
- Un bot qui se reconnecte avec plusieurs proxies change d'adresse IP.
- Le DNS d'un domaine révèle l'IP du serveur aux utilisateurs ([Nom de domaine](domain.md#ce-que-le-dns-révèle)). Derrière un service qui fait écran (Cloudflare…), les plafonds par IP deviennent collectifs.
- Sans nom de domaine, le serveur partagé est en HTTP : le code d'accès circule en clair.
- Les téléchargements du serveur autres que YouTube (mises à jour, ACME de Caddy, `check-ip`) ne passent pas par le proxy.
- `--proxy` et `--code` passent par la ligne de commande (visibles dans `ps`) ; préfère les variables d'environnement.

## Installation et déploiement

- `install.sh` télécharge Caddy **sans vérifier d'empreinte** et lance `playwright install-deps` en root depuis le venv du projet (Debian/Ubuntu) ([Installation](installation.md)).
- Le sandbox de Chromium est désactivé en root et dans l'image Docker fournie ([Sécurité](security.md#chromium-et-son-sandbox)).
- `uvicorn`, `playwright` et `yt-dlp` ne sont pas verrouillés (pas de fichier de verrouillage). YouTube change souvent : `pip install -U yt-dlp` de temps en temps.
- Chromium de Playwright doit correspondre à la version du paquet ; un binaire existant s'utilise via `ZOOMBOTS_CHROMIUM`.

## Plateforme

- Linux uniquement. Hors périmètre : dessin, partage d'écran, réception audio.

## Voir aussi

[Tests et validation](testing.md) · [Sécurité](security.md#ce-qui-nest-pas-couvert)
