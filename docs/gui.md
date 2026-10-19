# Le GUI

L'interface web est servie sur `/`. Chaque **onglet** est une session isolée : à l'arrivée la page est vide, et l'utilisateur ne voit que ses propres bots, sons et actions. Fonctionnement interne : [Sessions](sessions.md).

## Les quatre panneaux

### 1. Déployer des bots

- **Liens ou ID de réunion**, un par ligne. Pour un passcode séparé : `lien | passcode`. Les liens `zoom.us`, `zoomgov.com` et leurs sous-domaines sont acceptés ([détail](bot-lifecycle.md#domaines-zoom)).
- **Nom** : les bots s'appellent `<nom> 1`, `<nom> 2`… (juste `<nom>` s'il n'y en a qu'un).
- **Nombre** et **mode de répartition** :
  - *répartis sur les liens* : le nombre est le total, partagé équitablement (10 bots sur 3 liens donnent 4, 3 et 3 ; le reste va aux premiers liens) ;
  - *par lien* : le nombre est celui de chaque lien (3 liens × 2 = 6 bots).
- **Reconnexion auto** : un bot coupé ou expulsé se reconnecte ([détail](bot-lifecycle.md#reconnexion)).

### 2. Sons

- **Glisser-déposer** de fichiers audio ou vidéo n'importe où sur la page, ou clic sur la zone pour parcourir. Formats : mp3, wav, ogg, flac, m4a, opus, aac, mp4, webm, mkv (la piste audio est utilisée).
- **YouTube** : colle un lien `youtube.com`, `youtu.be` ou `music.youtube.com` ; l'audio est téléchargé par yt-dlp et converti en mp3.
- Les sons du dossier `sounds/` du serveur apparaissent aussi (« serveur »).
- Un son se **glisse sur un bot** pour le jouer une fois ; la croix supprime un son envoyé.

Limites : [Limites et quotas](limits.md).

### 3. Action

Joue un son **N fois** sur tous les bots actifs ou sur la sélection. Chaque lecture attend la fin de la précédente.

| Champ | Sens |
|---|---|
| Répétitions | nombre de lectures par bot (1 à 1000) |
| Pause entre | secondes entre deux lectures |
| Décalage entre bots | le bot *k* démarre `k × décalage` secondes plus tard |
| Volume | 0 à 2 |

Une action lancée avant que le bot soit entré dans la réunion **démarre dès son admission**. Une nouvelle action sur un bot **remplace** la précédente. Trois échecs de lecture consécutifs (micro introuvable, fichier illisible) arrêtent l'action en erreur.

### 4. Mes bots

Une carte par bot : statut, réunion, progression de l'action, boutons ⏹ (arrêter l'action) et ✕ (retirer le bot). La case à cocher sert à la sélection. **Un clic sur la carte** ouvre le panneau du bot : statut, historique, capture d'écran en direct (rafraîchie toutes les 3 s), micro on/off, jouer le son choisi, arrêter, retirer.

En haut de page : **Tout stopper** (actions et sons de tous tes bots) et **Quitter tous les bots**.

## Fermer l'onglet

Le navigateur garde un WebSocket ouvert. S'il reste coupé plus de **15 secondes**, la session est détruite : actions annulées, bots retirés de leurs réunions, fichiers supprimés. Un **rechargement** de page dans ce délai reprend la même session ; un **nouvel onglet** démarre une session vide.

## Voir aussi

[Sessions](sessions.md) · [Limites et quotas](limits.md) · [API du GUI](api.md#api-du-gui) · [Cycle de vie d'un bot](bot-lifecycle.md)
