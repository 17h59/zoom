# zoom

Lance des bots sur le client web de Zoom et pilote-les depuis une interface web ou en ligne de commande : rejoindre une réunion, jouer des sons (fichiers ou YouTube), répéter une action.

## Démarrage rapide (chez toi)

Linux uniquement.

```
git clone https://github.com/17h59/zoom.git && cd zoom
./install.sh        # installe les dépendances, Chromium et la commande `zoombots`
zoombots gui        # lance l'interface et ouvre ton navigateur
```

Si `zoombots` est introuvable : `export PATH="$HOME/.local/bin:$PATH"`.

## Utiliser l'interface

Dans le navigateur (http://127.0.0.1:8000), chaque onglet est une session isolée.

1. **Déployer** : colle un ou plusieurs liens de réunion Zoom (un par ligne), choisis le nombre de bots et leur répartition, clique sur *Déployer*.
2. **Sons** : glisse-dépose des fichiers audio ou vidéo sur la page, ou colle un lien YouTube.
3. **Action** : choisis un son et un nombre de répétitions, puis *Lancer* ; ou glisse un son sur un bot pour le jouer une fois.
4. **Piloter** : clique sur un bot pour voir son état et sa capture d'écran, couper son micro ou le retirer. *Tout stopper* et *Quitter tous les bots* agissent sur tous tes bots.

Fermer l'onglet retire ses bots. `zoombots shutdown` arrête tout.

## En ligne de commande

```
zoombots start                          # serveur d'administration en arrière-plan
zoombots spawn <id-ou-lien> -c 3        # envoie 3 bots
zoombots list                           # les voir
zoombots play son.mp3                   # joue un son (dossier sounds/) sur tous les bots
zoombots rm --all                       # les retirer
zoombots --help                         # toutes les commandes
```

## Cacher l'adresse IP de ta machine

Sans précaution, Zoom voit l'adresse IP de la machine qui lance les bots. Utilise un proxy :

```
zoombots gui --proxy http://hôte:port
zoombots check-ip                       # vérifie que l'IP est bien masquée
```

## Héberger pour d'autres utilisateurs

```
zoombots server start                                  # par adresse IP (HTTP)
./install.sh --server                                  # seulement pour un nom de domaine (installe Caddy)
zoombots server dns --domain bots.monsite.fr           # dit quel enregistrement DNS créer chez ton registrar
zoombots server start --domain bots.monsite.fr         # HTTPS automatique
```

La commande affiche l'adresse, le code d'accès à donner aux utilisateurs et le jeton d'administration. Avec Docker : `ZOOMBOTS_TOKEN=… ZOOMBOTS_ACCESS_CODE=… docker compose up`.

## Limites à connaître

Le parcours de connexion sur un vrai Zoom, le son reçu dans la réunion, le téléchargement YouTube réel et l'image Docker n'ont pas pu être testés de bout en bout : voir [Tests et validation](docs/testing.md).

## Documentation

Tout est détaillé dans [docs/README.md](docs/README.md) : [installation](docs/installation.md), [GUI](docs/gui.md), [CLI](docs/cli.md), [serveur partagé](docs/server.md), [nom de domaine](docs/domain.md), [masquer l'IP](docs/privacy-ip.md), [sécurité](docs/security.md), [dépannage](docs/troubleshooting.md).
