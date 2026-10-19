# Limitations connues

## Zoom

- Le parcours de connexion repose sur des sélecteurs et des textes **non vérifiés** face à un vrai Zoom ; une évolution de la page de Zoom peut le casser ([Tests et validation](testing.md)).
- Les captcha, les réunions qui exigent un compte, un enregistrement ou une inscription ne sont pas gérés.
- La détection d'une expulsion repose sur des messages en anglais et en français.
- Un hôte Zoom peut reconnaître un bot (nom, comportement) et l'expulser.

## Audio

- Le son n'est pas vérifié de bout en bout dans un vrai Zoom.
- `VirtualMic.playing` reste vrai tant que `paplay` vide son buffer.
- Sur un serveur sans carte son, PulseAudio fonctionne en sink nul : normal.

## Ressources

- Un Chromium complet par bot : de l'ordre de 300 à 500 Mo de RAM chacun, coût linéaire ([Limites](limits.md#combien-de-bots-tient-la-machine)).
- La mutualisation n'est possible qu'au niveau du conteneur (un PulseAudio), pas du navigateur ([Architecture](architecture.md#choix-techniques)).

## Sessions et état

- Après un redémarrage, les bots d'administration rejoignent la réunion comme de nouveaux participants (mute et lectures en cours non restaurés). Les bots des sessions du GUI **ne survivent pas** au redémarrage du serveur.
- Un seul serveur à la fois par machine, sauf avec `ZOOMBOTS_INSTANCE` ([Audio](audio.md#plusieurs-zoombots-sur-un-même-pulseaudio)).
- Il n'y a pas de comptes utilisateurs : l'identité d'un utilisateur est son onglet.

## Réseau et confidentialité

- Les plafonds par IP se contournent en changeant d'adresse, et pénalisent les utilisateurs derrière un même NAT ([Limites](limits.md#limites-de-ce-dispositif)).
- Chromium ne gère pas l'authentification SOCKS5 ([Masquer l'IP](privacy-ip.md)).
- Le DNS d'un domaine révèle l'IP du serveur aux utilisateurs ([Nom de domaine](domain.md#ce-que-le-dns-révèle)).
- Sans nom de domaine, le serveur partagé est en HTTP : le code d'accès circule en clair.
- Dans Docker, les utilisateurs comptent pour une seule IP tant que la passerelle n'est pas dans `ZOOMBOTS_TRUSTED_PROXIES` ([Docker](docker.md#à-savoir)).

## Plateforme

- Linux uniquement. Hors périmètre : dessin, partage d'écran, réception audio.
- `uvicorn`, `playwright` et `yt-dlp` ne sont pas verrouillés (pas de fichier de verrouillage). YouTube change souvent : `pip install -U yt-dlp` de temps en temps.
- Chromium de Playwright doit correspondre à la version du paquet ; un binaire existant s'utilise via `ZOOMBOTS_CHROMIUM` (sous root, Chromium exige `--no-sandbox`, que zoombots n'ajoute pas).

## Voir aussi

[Tests et validation](testing.md) · [Sécurité](security.md#ce-qui-nest-pas-couvert)
