# Changelog

Format : chaque version liste les changements. Date au format AAAA-MM-JJ.

## [0.4.1] — 2026-07-01
### Ajouté
- **Garde-fou « port déjà utilisé »** : au démarrage, si le port du serveur est déjà occupé
  (un serveur tourne déjà, ou un démarrage précédent ne s'est pas fermé), l'application le
  détecte et propose de **libérer le port** au lieu de laisser le serveur planter
  (`FAILED TO BIND TO PORT`).
- Bouton **« Libérer le port »** (Étape 2) : arrête proprement le serveur en cours puis force
  la libération du port, avec avertissement. `netinfo.port_in_use()` / `free_port()`.

## [0.4.0] — 2026-07-01
Refonte de l'expérience + accès distant 100 % automatique.

### Ajouté
- **Écran d'accueil** au lancement : choix entre **Configuration simplifiée** (assistant
  question/réponse qui guide pas à pas : type de serveur, version, monde, mode de jeu,
  accès) et **Mode avancé** (interface complète). Bouton **☰ Menu** pour revenir à l'accueil.
- **Étapes guidées et réactives** (onglet Serveur) : l'étape en cours s'allume, les étapes
  terminées passent en ✓, un bandeau indique quoi faire à chaque instant.

### Changé
- **Accès distant (playit.gg) entièrement automatisé** : plus aucune manipulation sur le site.
  Un seul clic « Allow » dans le navigateur, puis l'application récupère la clé et **crée le
  tunnel Minecraft toute seule** via l'API playit ; l'adresse à partager s'affiche directement.
- **Refonte visuelle** : thème sombre « SaaS » + accent émeraude, boîtes de dialogue stylées
  (fini les popups système), textes professionnels, effets de survol.
- **Fenêtre nette (HiDPI)** et adaptée à l'écran ; **onglets défilables** (plus rien hors champ).
- La saisie de commande brute passe dans l'onglet **Admin**.

### Corrigé
- **Java 25** requis pour les versions Minecraft du nouveau schéma (`26.x`, class file 69) :
  le mapping téléchargeait Java 21 → serveur qui ne démarrait pas (`UnsupportedClassVersionError`).

## [0.3.2] — 2026-06-30
### Ajouté
- **Serveurs installés** (onglet Serveur) : liste déroulante de tes serveurs existants +
  bouton **Charger** pour **relancer une partie** sans repasser par « Préparer » (pas de
  re-téléchargement, et la config sauvegardée du serveur est relue dans l'UI). Boutons
  **Rafraîchir** et **Supprimer** (avec confirmation).
- `paths.list_servers()` et `server_process.read_properties()`.

## [0.3.1] — 2026-06-29
### Ajouté
- **Envoi automatique** des rapports de crash par SMTP (`core/mailer.py`) : si configuré,
  le rapport part tout seul (avec pièce jointe), sans clic. Sinon, fallback sur l'ouverture
  du client mail (manuel).
- Bouton **« Rapports auto »** (onglet Serveur) pour saisir l'adresse Gmail + le mot de
  passe d'application (stockés en local), avec mail de test.

## [0.3.0] — 2026-06-29
Grosse vague de fonctionnalités.

### Ajouté
- **Support Forge & NeoForge** (téléchargement de l'installeur + exécution `--installServer`
  + lancement via fichier d'arguments `win_args.txt`). `core/downloaders.prepare()` et
  `launch_args()` gèrent désormais tous les loaders ; `ServerProcess` prend une commande
  générique.
- **Recherche & installation de mods/plugins depuis Modrinth** (onglet Mods) — filtré par
  loader et version. `core/modrinth.py`.
- **Tunnel playit.gg intégré** (jouer à distance sans ouvrir de port) : téléchargement de
  l'agent, lancement, ouverture auto du lien de configuration. `core/playit.py`.
- **Profils sauvegardés** (`core/config_store.py`) : loader/version/RAM/port + toute la
  config restaurés au lancement, sauvés à la fermeture.
- **Compteur de joueurs en ligne** dans le header (parse des lignes joined/left).
- **Auto-restart** optionnel si le serveur crash (code ≠ 0, hors arrêt manuel).
- **Détection de la RAM totale** + garde-fou (max conseillé, confirmation si dépassé).
  `core/sysinfo.py`.
- **Rapport de crash automatique** (`core/crash_reporter.py`) : à toute exception non gérée,
  génère un rapport structuré (système, état, dernières lignes console, traceback),
  l'enregistre dans `MZ_Server_Data/crash_reports/` et ouvre le client mail vers le dev.

### Corrigé
- Dérivation MC depuis NeoForge compatible nouveau versioning Minecraft (`26.x`).

## [0.2.1] — 2026-06-29
### Corrigé
- **Overlay blanc** sur les combobox (états readonly/selected) qui masquait le texte :
  couleurs des champs forcées en sombre + texte clair, dropdown stylé aussi.
- Boutons : couleurs explicites (clam mettait un gris clair illisible).

### Ajouté
- **Indicateur d'état** du serveur dans le header (● arrêté / ● en marche).
- **Sauvegarde du monde en .zip** (onglet Admin) + ouverture du dossier des sauvegardes.

## [0.2.0] — 2026-06-29
Refonte interface + gestion serveur complète.

### Ajouté
- Interface **à onglets** (Serveur / Configuration / Mods-Plugins / Admin) + thème sombre.
- **Boutons Copier** sur les IP LAN et distante.
- Onglet **Configuration** (façon Aternos) : seed, mode de jeu, difficulté, hardcore,
  type de monde (normal/plat/grands biomes/amplifié), distance de rendu & simulation,
  PVP, monstres, nether, vol, command blocks, online-mode, whitelist.
- Onglet **Mods / Plugins** : ajouter/supprimer/lister les .jar du serveur sélectionné.
- Onglet **Admin** : OP/de-OP, kick, ban, whitelist, gamemode joueur, time, weather,
  save-all, list, difficulté, annonces (say) — pour gérer pendant la partie.
- `run.bat` lance désormais la GUI **sans terminal** (pythonw) ; `run_debug.bat` pour debug.

### Changé
- `run.bat` : plus de fenêtre console qui traîne derrière le launcher.

## [0.1.0] — 2026-06-29
Première version fonctionnelle.

### Ajouté
- GUI Tkinter (`launcher.py`) : choix loader + version, RAM, port, joueurs max, MOTD, CLUF.
- Téléchargement auto de Java (Temurin/Adoptium) selon la version Minecraft.
- Téléchargement auto du serveur : **vanilla**, **paper**, **fabric**.
- Démarrage/arrêt du serveur + console + envoi de commandes.
- Config auto : `eula.txt` et `server.properties`.
- Infos réseau : IP locale (LAN) + IP publique + aide port-forwarding.
- Export en `.exe` unique via PyInstaller (`build.bat`).
- Docs : `README.md` + ce changelog.

### Limites connues
- Forge non géré (vanilla/paper/fabric uniquement).
- Jeu à distance : port forwarding manuel requis (pas encore de tunnel intégré).

## À venir (backlog)
Barre de progression réelle des téléchargements, règle de pare-feu Windows automatique,
gestion multi-profils nommés.
