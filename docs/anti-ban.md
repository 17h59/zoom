# Furtivité et anti-ban

Zoom surveille l'accès à son client web : empreinte du navigateur, marqueurs d'automatisation Playwright/Puppeteer, et possibilité pour l'hôte d'expulser un participant. zoombots applique une stratégie de furtivité maximale et remplace automatiquement tout bot expulsé.

## Furtivité maximale (Stealth)

Activée par défaut dans le GUI web et recommandée sur serveur :

1. **Chromium non-headless sous Xvfb** : le mode headless natif de Chromium présente des empreintes caractéristiques (User-Agent, absence de GPU/WebGL cohérent, timing de rendu) que Zoom détecte. Quand `xvfb-run` est disponible sur la machine, zoombots lance Chromium en mode fenêtré dans un serveur X virtuel (`ZOOMBOTS_HEADED=1`).
2. **Masquage de l'automatisation** :
   - `--disable-blink-features=AutomationControlled` pour masquer l'indicateur d'automatisation Blink (`navigator.webdriver`).
   - Suppression des drapeaux Playwright trahissant le pilotage automatique.
   - Désactivation de l'infobar d'automatisation Chrome.
3. **Durcissement WebRTC et réseau** : masquage de l'IP réelle et prévention des fuites WebRTC en présence d'un [proxy](privacy-ip.md).

## Remplacement automatique après expulsion (Respawn on ban)

Lorsqu'un hôte Zoom expulse un participant, Zoom verrouille le jeton de session et le contexte du navigateur (*« You cannot rejoin this meeting because you were previously removed by the host »*). Une simple reconnexion dans la même page échoue systématiquement.

### Fonctionnement

Dès qu'une expulsion est confirmée (`status="denied"` avec message d'exclusion de l'hôte, refus d'admission ou détection de bot) :

1. **Destruction immédiate du bot banni** :
   - Le bot banni est retiré des bots actifs (`manager.bots`) pour libérer immédiatement le slot de capacité.
   - `bot.leave()` ferme Chromium et détruit son micro virtuel PulseAudio.
2. **Spawn d'un bot de remplacement** :
   - Une courte temporisation (1 s) laisse à Zoom le temps de clore la session côté serveur.
   - Un bot flambant neuf démarre pour la même réunion, avec le même mot de passe, le même propriétaire (session GUI) et le même nom.
   - Ce nouveau bot bénéficie d'un contexte de navigation vierge (nouveau token de session) et d'un nouveau micro PulseAudio.

### Protection anti-boucle

Pour éviter un emballement si un hôte expulse les bots en continu :

- **Plafond de sécurité** : `ZOOMBOTS_MAX_RESPAWNS` (défaut : **10** remplacements consécutifs). Au-delà, le bot s'arrête définitivement en statut `denied`.
- **Réinitialisation sur session stable** : si le bot reste connecté et actif en réunion pendant au moins `ZOOMBOTS_STABLE_SESSION` (défaut : **60 s**), le compteur de respawns repart de zéro.
- **Désactivation** : variable `ZOOMBOTS_RESPAWN_ON_BAN=0`, ou argument CLI `--no-respawn` (`zoombots spawn ... --no-respawn`).

## Désactivation de la réduction de bruit Zoom

Zoom applique par défaut des filtres audio destructeurs (détection de frappe clavier, annulation de bruit agressive) qui coupent ou déforment les sons injectés par soundboard.

zoombots désactive ces filtres pour chaque instance :
- Neutralisation des contraintes 3A WebRTC (`noiseSuppression`, `echoCancellation`, `autoGainControl`).
- Activation automatique du profil haute-fidélité « Son d'origine » (*Original Sound*) dans le client Zoom.
- Voir [Audio](audio.md#désactivation-de-la-réduction-de-bruit-et-son-dorigine).

## Voir aussi

[Cycle de vie d'un bot](bot-lifecycle.md) · [Audio](audio.md) · [Configuration](configuration.md) · [Masquer l'IP](privacy-ip.md) · [Limites et quotas](limits.md)
