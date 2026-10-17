"""MZ Minecraft Server Launcher.

Interface a onglets pour auto-heberger un serveur Minecraft
(vanilla / paper / fabric / forge / neoforge) :
- Serveur     : loader/version, RAM (+garde-fou), port, install, demarrage, reseau
                (copier IP LAN/distant), tunnel playit.gg, auto-restart
- Configuration : seed, mode de jeu, difficulte, hardcore, type de monde, distance de rendu...
- Mods/Plugins : ajouter ses .jar OU chercher/installer depuis Modrinth
- Admin        : OP, kick, ban, whitelist, time, weather, backup du monde... pendant la partie

Console toujours visible en bas + compteur de joueurs et indicateur d'etat dans le header.
Profil sauvegarde (config_store) et rapport de crash auto (crash_reporter) inclus.

Lancer depuis les sources : python launcher.py   |   Compiler : build.bat
"""
import collections
import ctypes
import os
import re
import shutil
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from core import (config_store, crash_reporter, downloaders, java_manager,
                  mailer, modrinth, netinfo, paths, playit, sysinfo)
from core.java_manager import required_java
from core.server_process import (ServerProcess, accept_eula, read_properties,
                                 update_properties)

APP_VERSION = "0.4.1"
LOADERS = ["vanilla", "paper", "fabric", "forge", "neoforge"]
DEFAULT_PORT = 25565
DEFAULT_RAM = sysinfo.recommended_ram_mb()  # ~60% de la RAM, plafonne

# Detection des connexions/deconnexions dans la console serveur
JOIN_RE = re.compile(r"]: (\w+) joined the game")
LEFT_RE = re.compile(r"]: (\w+) left the game")

GAMEMODES = ["survival", "creative", "adventure", "spectator"]
DIFFICULTIES = ["peaceful", "easy", "normal", "hard"]
# Libelle affiche -> valeur reelle dans server.properties (format moderne 1.19+)
LEVEL_TYPES = {
    "Normal": "minecraft:normal",
    "Plat (superflat)": "minecraft:flat",
    "Grands biomes": "minecraft:large_biomes",
    "Amplifie": "minecraft:amplified",
}

# ==== Theme "dark SaaS" + accent emerald (look haut de gamme, pas gaming) ====
BG = "#0F1620"          # fond appli (slate tres sombre)
PANEL = "#18212E"       # cartes / panneaux
PANEL2 = "#1F2A3A"      # surface elevee (hover, champs)
BORDER = "#2A3849"      # bordures fines
FG = "#E6EDF3"          # texte principal
MUTED = "#8B9BB0"       # texte secondaire
FIELD = "#1C2735"       # fond des champs
ACCENT = "#10B981"      # emerald (boutons principaux, selection)
ACCENT_HOVER = "#34D399"
ACCENT_DARK = "#0E9E6E"
DANGER = "#EF4444"
DANGER_HOVER = "#F87171"
NEUTRAL = "#222C3A"     # boutons secondaires
NEUTRAL_HOVER = "#2C3848"
CONSOLE_BG = "#0B1118"
CONSOLE_FG = "#9FB4C8"


class App:
    def __init__(self, root):
        self.root = root
        root.title("MZ Minecraft Server Launcher")
        # Taille adaptee a l'ecran avec de bonnes marges sur tous les cotes (l'appli
        # ne touche jamais les bords). Le contenu des onglets est scrollable : rien
        # ne peut sortir de l'ecran, meme sur un petit ecran.
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = min(1040, sw - 160), min(780, sh - 200)
        x, y = (sw - w) // 2, max(20, (sh - h) // 2 - 20)
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.minsize(860, 560)
        root.configure(bg=BG)

        paths.ensure_dirs()

        self.server = None
        self.java_exe = None
        self.current_dir = None
        self.prepared = False
        self._lan_ip = "..."
        self._public_ip = "..."
        self._user_stopped = False     # True si arret demande (evite l'auto-restart)
        self._players = set()          # joueurs connectes (compteur)
        self.playit_agent = None
        self._playit_address = playit.saved_address()
        self._console_tail = collections.deque(maxlen=60)  # pour le rapport de crash

        self._setup_style()
        self._build_ui()
        self._load_config()            # restaure le profil sauvegarde
        self._refresh_versions()
        self._refresh_existing()
        self._update_guide()
        threading.Thread(target=self._load_network_info, daemon=True).start()
        self._show_home()   # demarre sur l'ecran d'accueil (simplifie / avance)

    # ---------- Theme ----------
    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        f = ("Segoe UI", 10)
        field = FIELD
        style.configure(".", background=BG, foreground=FG, font=f)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG, font=f)
        style.configure("Panel.TLabel", background=PANEL, foreground=FG, font=f)
        style.configure("Title.TLabel", background=BG, foreground=FG,
                        font=("Segoe UI Semibold", 16))
        style.configure("Accent.TLabel", background=BG, foreground=ACCENT,
                        font=("Segoe UI Semibold", 16))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Hint.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))

        # Boutons : neutres par defaut, accent emerald, danger rouge. Coins via padding.
        style.configure("TButton", padding=(12, 8), font=f, background=NEUTRAL, foreground=FG,
                        borderwidth=0, focuscolor=NEUTRAL, relief="flat")
        style.map("TButton",
                  background=[("active", NEUTRAL_HOVER), ("disabled", "#1A2230")],
                  foreground=[("disabled", "#566373")])
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10),
                        foreground="#06281C", background=ACCENT)
        style.map("Accent.TButton",
                  background=[("active", ACCENT_HOVER), ("disabled", "#1A2230")],
                  foreground=[("disabled", "#566373")])
        style.configure("Danger.TButton", foreground="white", background=DANGER)
        style.map("Danger.TButton",
                  background=[("active", DANGER_HOVER), ("disabled", "#1A2230")],
                  foreground=[("disabled", "#566373")])
        style.configure("Ghost.TButton", background=PANEL, foreground=MUTED)
        style.map("Ghost.TButton",
                  background=[("active", PANEL2)], foreground=[("active", FG)])

        style.configure("TCheckbutton", background=PANEL, foreground=FG, font=f)
        style.map("TCheckbutton", background=[("active", PANEL)],
                  indicatorcolor=[("selected", ACCENT), ("!selected", field)],
                  foreground=[("disabled", "#566373")])

        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 6, 0, 0))
        style.configure("TNotebook.Tab", background=BG, foreground=MUTED,
                        padding=(18, 9), font=("Segoe UI Semibold", 10), borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", PANEL)],
                  foreground=[("selected", ACCENT), ("active", FG)])

        style.configure("TLabelframe", background=PANEL, bordercolor=BORDER, borderwidth=1,
                        relief="solid")
        style.configure("TLabelframe.Label", background=PANEL, foreground=ACCENT,
                        font=("Segoe UI Semibold", 10))

        # Etapes reactives : a venir (gris) / en cours (emerald) / fait (vert dim)
        for name, bc, fc in (("Step", BORDER, MUTED),
                             ("StepOn", ACCENT, ACCENT),
                             ("StepDone", "#2F6B52", "#5FB591")):
            style.configure(f"{name}.TLabelframe", background=PANEL, bordercolor=bc,
                            borderwidth=1, relief="solid")
            style.configure(f"{name}.TLabelframe.Label", background=PANEL, foreground=fc,
                            font=("Segoe UI Semibold", 10))

        style.configure("TSeparator", background=BORDER)

        # Champs : fond sombre + texte clair, y compris en etat readonly/selected.
        style.configure("TCombobox", fieldbackground=field, background=field, foreground=FG,
                        arrowcolor=MUTED, selectbackground=field, selectforeground=FG,
                        borderwidth=0, padding=4)
        style.map("TCombobox",
                  fieldbackground=[("readonly", field), ("disabled", "#16202C")],
                  foreground=[("readonly", FG), ("disabled", "#566373")],
                  selectbackground=[("readonly", field)],
                  selectforeground=[("readonly", FG)],
                  background=[("readonly", field), ("active", field)],
                  arrowcolor=[("readonly", MUTED), ("active", ACCENT)])
        style.configure("TEntry", fieldbackground=field, foreground=FG,
                        insertcolor=ACCENT, borderwidth=0, padding=5)
        style.configure("TSpinbox", fieldbackground=field, foreground=FG,
                        background=field, arrowcolor=MUTED, borderwidth=0, padding=4)
        style.map("TSpinbox", fieldbackground=[("readonly", field)])
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL2,
                        borderwidth=0)

        # Liste deroulante des combobox (popup natif, pas controle par le theme ttk)
        self.root.option_add("*TCombobox*Listbox.background", PANEL2)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#06281C")

    # ---------- Construction UI ----------
    def _build_ui(self):
        header = ttk.Frame(self.root, padding=(16, 12))
        header.pack(fill="x")
        ttk.Label(header, text="MZ", style="Accent.TLabel").pack(side="left")
        ttk.Label(header, text=" Minecraft Server", style="Title.TLabel").pack(side="left")
        self.head_status = ttk.Label(header, text="", style="Muted.TLabel")
        self.head_status.pack(side="right")
        self.players_lbl = ttk.Label(header, text="0 joueur", style="Muted.TLabel")
        self.players_lbl.pack(side="right", padx=14)
        self.state_dot = ttk.Label(header, text="● arrete", foreground=DANGER, background=BG)
        self.state_dot.pack(side="right")
        ttk.Button(header, text="☰ Menu", style="Ghost.TButton",
                   command=self._show_home).pack(side="right", padx=(0, 16))

        self.nb = ttk.Notebook(self.root)
        self._tab_server()
        self._tab_config()
        self._tab_mods()
        self._tab_admin()

        # --- Bas de fenetre, epingle (toujours visible) : barre de statut tout en bas,
        #     puis la console a hauteur FIXE. Le notebook prend le reste et son contenu
        #     est scrollable -> rien ne peut sortir de l'ecran.
        self.status_var = tk.StringVar(value="Pret.")
        ttk.Label(self.root, textvariable=self.status_var, relief="flat",
                  background=PANEL2, foreground=MUTED,
                  anchor="w", padding=7).pack(fill="x", side="bottom")

        cons_frame = ttk.Frame(self.root, padding=(14, 6))
        cons_frame.pack(fill="x", side="bottom")
        ttk.Label(cons_frame, text="Console du serveur", style="Muted.TLabel").pack(anchor="w")
        self.console = scrolledtext.ScrolledText(
            cons_frame, height=7, bg=CONSOLE_BG, fg=CONSOLE_FG,
            insertbackground=CONSOLE_FG, font=("Consolas", 9), state="disabled",
            relief="flat", borderwidth=0)
        self.console.pack(fill="x", pady=(4, 2))

        self.nb.pack(fill="both", expand=True, padx=14, pady=(2, 6))

    def _tab_server(self):
        outer = ttk.Frame(self.nb, style="Panel.TFrame")
        self.nb.add(outer, text="  Serveur  ")
        tab = self._scroll_area(outer)

        # ----- Bandeau guide : dit en clair quoi faire MAINTENANT -----
        self.guide_lbl = tk.Label(
            tab, text="", bg="#11261E", fg="#CFF3E2",
            font=("Segoe UI Semibold", 11), justify="left", anchor="w",
            padx=16, pady=12, wraplength=920, highlightthickness=1,
            highlightbackground=ACCENT_DARK, highlightcolor=ACCENT_DARK)
        self.guide_lbl.pack(fill="x", pady=(0, 12))

        # ===== ETAPE 1 : choisir le serveur =====
        s1 = ttk.LabelFrame(tab, style="Step.TLabelframe", padding=12)
        s1.pack(fill="x")
        self._step1 = (s1, "Choisis ton serveur")

        row = ttk.Frame(s1, style="Panel.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="Type :", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.loader_var = tk.StringVar(value="paper")
        cb = ttk.Combobox(row, textvariable=self.loader_var, values=LOADERS,
                          state="readonly", width=12)
        cb.grid(row=0, column=1, padx=5)
        cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_versions())

        ttk.Label(row, text="Version :", style="Panel.TLabel").grid(row=0, column=2, padx=(15, 0))
        self.version_var = tk.StringVar()
        self.version_cb = ttk.Combobox(row, textvariable=self.version_var,
                                       state="readonly", width=18)
        self.version_cb.grid(row=0, column=3, padx=5)

        self.snapshot_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Snapshots (vanilla)", variable=self.snapshot_var,
                        command=self._refresh_versions).grid(row=0, column=4, padx=10)

        ttk.Label(row, text="RAM (Mo) :", style="Panel.TLabel").grid(row=1, column=0, sticky="w",
                                                                     pady=(10, 0))
        self.ram_var = tk.StringVar(value=str(DEFAULT_RAM))
        ttk.Entry(row, textvariable=self.ram_var, width=10).grid(row=1, column=1, pady=(10, 0))
        ttk.Label(row, text="Port :", style="Panel.TLabel").grid(row=1, column=2, padx=(15, 0),
                                                                 pady=(10, 0))
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        ttk.Entry(row, textvariable=self.port_var, width=10).grid(row=1, column=3, pady=(10, 0))

        total = sysinfo.total_ram_mb()
        ttk.Label(row, style="Hint.TLabel",
                  text=f"RAM PC : {total} Mo  -  max conseille : {sysinfo.max_safe_ram_mb()} Mo"
                  ).grid(row=1, column=4, padx=10, pady=(10, 0), sticky="w")

        opts = ttk.Frame(s1, style="Panel.TFrame")
        opts.pack(fill="x", pady=(10, 0))
        self.eula_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="J'accepte le CLUF Minecraft (obligatoire) - aka.ms/MinecraftEULA",
                        variable=self.eula_var).pack(side="left")
        self.autorestart_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Redemarrage auto si crash",
                        variable=self.autorestart_var).pack(side="left", padx=20)

        # Reprendre une partie deja installee (toujours dans l'etape 1)
        existing = ttk.Frame(s1, style="Panel.TFrame")
        existing.pack(fill="x", pady=(10, 0))
        ttk.Label(existing, text="Ou reprends une partie deja installee :",
                  style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.existing_var = tk.StringVar()
        self.existing_cb = ttk.Combobox(existing, textvariable=self.existing_var,
                                        state="readonly", width=28)
        self.existing_cb.grid(row=0, column=1, padx=(0, 8))
        ttk.Button(existing, text="Charger", style="Accent.TButton",
                   command=self._load_existing).grid(row=0, column=2, padx=2)
        ttk.Button(existing, text="Rafraichir",
                   command=self._refresh_existing).grid(row=0, column=3, padx=2)
        ttk.Button(existing, text="Supprimer", style="Danger.TButton",
                   command=self._delete_existing).grid(row=0, column=4, padx=2)

        # ===== ETAPE 2 : installer puis demarrer =====
        s2 = ttk.LabelFrame(tab, style="Step.TLabelframe", padding=12)
        s2.pack(fill="x", pady=(12, 0))
        self._step2 = (s2, "Installe puis demarre")
        actions = ttk.Frame(s2, style="Panel.TFrame")
        actions.pack(fill="x")
        self.prepare_btn = ttk.Button(actions, text="1) Installer / Preparer",
                                      command=self._on_prepare)
        self.prepare_btn.pack(side="left")
        self.start_btn = ttk.Button(actions, text="2) Demarrer", style="Accent.TButton",
                                    command=self._on_start, state="disabled")
        self.start_btn.pack(side="left", padx=6)
        self.stop_btn = ttk.Button(actions, text="Arreter", style="Danger.TButton",
                                   command=self._on_stop, state="disabled")
        self.stop_btn.pack(side="left")
        ttk.Button(actions, text="Liberer le port", style="Ghost.TButton",
                   command=self._free_port).pack(side="left", padx=6)
        # Outils secondaires, discrets (style fantome)
        ttk.Button(actions, text="Ouvrir le dossier", style="Ghost.TButton",
                   command=self._open_folder).pack(side="right")
        ttk.Button(actions, text="Rapports d'erreur", style="Ghost.TButton",
                   command=self._config_mail).pack(side="right", padx=6)

        # ===== ETAPE 3 : inviter des joueurs =====
        s3 = ttk.LabelFrame(tab, style="Step.TLabelframe", padding=12)
        s3.pack(fill="x", pady=(12, 0))
        self._step3 = (s3, "Inviter des joueurs")

        # Cas A : meme reseau local (le plus simple)
        a = ttk.Frame(s3, style="Panel.TFrame")
        a.pack(fill="x")
        ttk.Label(a, text="Joueurs sur le meme reseau (Wi-Fi / LAN)  -  adresse a communiquer :",
                  style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.lan_lbl = ttk.Label(a, text="...", font=("Consolas", 10))
        self.lan_lbl.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(a, text="Copier", width=8,
                   command=lambda: self._copy(self._addr(self._lan_ip))).grid(row=0, column=2)

        ttk.Separator(s3, orient="horizontal").pack(fill="x", pady=10)

        # Cas B : a distance (Internet) -> tunnel (recommande)
        ttk.Label(s3, text="Joueurs a distance (Internet)  -  recommande : le tunnel "
                           "(aucune configuration de box) :", style="Panel.TLabel").pack(anchor="w")
        bt = ttk.Frame(s3, style="Panel.TFrame")
        bt.pack(fill="x", pady=(6, 0))
        ttk.Button(bt, text="Activer l'acces distant (configuration unique)",
                   command=self._config_playit).pack(side="left")
        self.playit_btn = ttk.Button(bt, text="Demarrer le tunnel",
                                     command=self._toggle_playit)
        self.playit_btn.pack(side="left", padx=6)
        self.playit_addr = ttk.Label(bt, text=self._playit_address or "(non active)",
                                     font=("Consolas", 10))
        self.playit_addr.pack(side="left", padx=10)
        ttk.Button(bt, text="Copier l'adresse", width=14,
                   command=self._copy_playit).pack(side="left")

        # Alternative avancee : ouverture de port sur la box
        adv = ttk.Frame(s3, style="Panel.TFrame")
        adv.pack(fill="x", pady=(10, 0))
        ttk.Label(adv, text="Avance (sans tunnel, ouverture de port) :",
                  style="Hint.TLabel").pack(side="left")
        self.pub_lbl = ttk.Label(adv, text="...", font=("Consolas", 9))
        self.pub_lbl.pack(side="left", padx=8)
        ttk.Button(adv, text="Copier", width=8,
                   command=lambda: self._copy(self._addr(self._public_ip))).pack(side="left")
        ttk.Button(adv, text="Aide (ouverture de port)",
                   command=self._show_remote_info).pack(side="left", padx=6)

    # ---------- Guide pas-a-pas (dit quoi faire selon l'etat) ----------
    def _apply_steps(self):
        """Fait reagir visuellement les 3 etapes : a venir (gris) / en cours (emerald) / fait (vert)."""
        if not hasattr(self, "_step1"):
            return
        running = bool(self.server and self.server.is_running())
        if running:
            states = ["done", "done", "on"]
        elif self.prepared:
            states = ["done", "on", "idle"]
        else:
            states = ["on", "idle", "idle"]
        style_map = {"idle": "Step", "on": "StepOn", "done": "StepDone"}
        mark = {"idle": "", "on": "●  ", "done": "✓  "}
        for i, (frame, title) in enumerate((self._step1, self._step2, self._step3)):
            st = states[i]
            frame.configure(style=f"{style_map[st]}.TLabelframe",
                            text=f"  {mark[st]}Etape {i + 1}  -  {title}  ")

    def _update_guide(self):
        """Met a jour le bandeau guide selon l'etat reel (prepare / en marche / tunnel)."""
        if not hasattr(self, "guide_lbl"):
            return
        self._apply_steps()
        running = bool(self.server and self.server.is_running())
        tunnel = bool(self.playit_agent and self.playit_agent.is_running())
        lan = self._addr(self._lan_ip) if self._lan_ip not in ("...", "") else "(adresse LAN...)"

        if not running and not self.prepared:
            txt = ("Suivez les etapes dans l'ordre :\n"
                   "Etape 1  -  choisir le Type et la Version, regler la RAM, accepter le CLUF "
                   "(ou reprendre une partie installee).\n"
                   "Etape 2  -  cliquer sur \"1) Installer / Preparer\" (telechargement automatique).")
        elif not running and self.prepared:
            txt = ("Serveur pret.  ->  Etape 2 : cliquer sur le bouton vert \"2) Demarrer\" "
                   "pour lancer le serveur.")
        elif running and tunnel and self._playit_address:
            txt = ("Serveur en marche, tunnel actif.  ->  Etape 3 : adresses a communiquer aux joueurs :\n"
                   f"    - meme reseau (LAN)  :  {lan}\n"
                   f"    - a distance (Internet)  :  {self._playit_address}")
        elif running and self._playit_address:
            txt = ("Serveur en marche.  ->  Etape 3 : meme reseau = adresse "
                   f"{lan}.  Pour l'acces a distance, cliquer sur \"Demarrer le tunnel\".")
        else:  # running, tunnel jamais configure
            txt = ("Serveur en marche.  ->  Etape 3 : les joueurs sur le meme reseau se "
                   f"connectent a {lan}.\n"
                   "Pour un acces a distance (Internet), cliquer sur \"Activer l'acces distant\".")
        self.guide_lbl.configure(text=txt)

    # ====================================================================
    #  ECRAN D'ACCUEIL + ASSISTANT (mode simplifie) -- overlays par-dessus l'UI
    # ====================================================================
    def _overlay(self):
        """Cree un calque plein ecran (cache l'UI avancee en dessous)."""
        if getattr(self, "_ov", None) is not None:
            self._ov.destroy()
        self._ov = tk.Frame(self.root, bg=BG)
        self._ov.place(relx=0, rely=0, relwidth=1, relheight=1)
        return self._ov

    def _enter_advanced(self):
        """Ferme l'overlay -> revele l'interface complete (mode avance)."""
        if getattr(self, "_ov", None) is not None:
            self._ov.destroy()
            self._ov = None

    def _card(self, parent, icon, title, desc, cmd):
        """Carte cliquable avec effet de survol (bordure emerald)."""
        card = tk.Frame(parent, bg=PANEL, highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=BORDER, cursor="hand2")
        card.pack(fill="x", pady=5)
        inner = tk.Frame(card, bg=PANEL)
        inner.pack(fill="x", padx=16, pady=13)
        tk.Label(inner, text=icon, bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 17)).pack(side="left", padx=(0, 14))
        chev = tk.Label(inner, text="›", bg=PANEL, fg=MUTED, font=("Segoe UI", 18))
        chev.pack(side="right")
        txt = tk.Frame(inner, bg=PANEL)
        txt.pack(side="left", fill="x", expand=True)
        ttl = tk.Label(txt, text=title, bg=PANEL, fg=FG,
                       font=("Segoe UI Semibold", 12), anchor="w")
        ttl.pack(fill="x")
        if desc:
            tk.Label(txt, text=desc, bg=PANEL, fg=MUTED, font=("Segoe UI", 9),
                     anchor="w", justify="left", wraplength=560).pack(fill="x")

        def on_enter(_):
            card.configure(highlightbackground=ACCENT)
            ttl.configure(fg=ACCENT)
            chev.configure(fg=ACCENT)

        def on_leave(_):
            card.configure(highlightbackground=BORDER)
            ttl.configure(fg=FG)
            chev.configure(fg=MUTED)

        def bind_all(w):
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)
            w.bind("<Button-1>", lambda e: cmd())
            for ch in w.winfo_children():
                bind_all(ch)

        bind_all(card)
        return card

    def _show_home(self):
        ov = self._overlay()
        wrap = tk.Frame(ov, bg=BG)
        wrap.place(relx=0.5, rely=0.46, anchor="center", width=720)
        tk.Label(wrap, text="MZ", bg=BG, fg=ACCENT,
                 font=("Segoe UI Semibold", 24)).pack(side="top", anchor="w")
        tk.Label(wrap, text="Minecraft Server", bg=BG, fg=FG,
                 font=("Segoe UI Semibold", 24)).pack(anchor="w")
        tk.Label(wrap, text="Heberge ton serveur entre amis, en quelques clics.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 11)).pack(anchor="w", pady=(4, 26))
        self.wiz_body = wrap
        self._card(wrap, "✨", "Configuration simplifiee   (recommande)",
                   "Reponds a quelques questions, on cree et configure le serveur pour toi.",
                   self._start_wizard)
        self._card(wrap, "⚙", "Mode avance",
                   "Acces direct a l'interface complete (tous les reglages).",
                   self._enter_advanced)

    # ---------- Assistant pas-a-pas ----------
    def _start_wizard(self):
        self._wiz = {}
        self._wiz_history = []
        ov = self._overlay()
        wrap = tk.Frame(ov, bg=BG)
        wrap.place(relx=0.5, rely=0.5, anchor="center", width=720)
        top = tk.Frame(wrap, bg=BG)
        top.pack(fill="x")
        self._wiz_step_lbl = tk.Label(top, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self._wiz_step_lbl.pack(side="left")
        self._wiz_prog = ttk.Progressbar(wrap, style="Horizontal.TProgressbar",
                                         maximum=100, length=720)
        self._wiz_prog.pack(fill="x", pady=(6, 22))
        self.wiz_body = tk.Frame(wrap, bg=BG)
        self.wiz_body.pack(fill="x")
        foot = tk.Frame(wrap, bg=BG)
        foot.pack(fill="x", pady=(24, 0))
        ttk.Button(foot, text="← Precedent", style="Ghost.TButton",
                   command=self._wiz_back).pack(side="left")
        ttk.Button(foot, text="Quitter l'assistant", style="Ghost.TButton",
                   command=self._show_home).pack(side="right")
        self._wiz_go(self._wiz_q_mode)

    def _wiz_go(self, step):
        self._wiz_history.append(step)
        self._wiz_render(step)

    def _wiz_back(self):
        if len(self._wiz_history) > 1:
            self._wiz_history.pop()
            self._wiz_render(self._wiz_history[-1])
        else:
            self._show_home()

    def _wiz_render(self, step):
        n = len(self._wiz_history)
        self._wiz_step_lbl.configure(text=f"Etape {n}")
        self._wiz_prog["value"] = min(n * 16, 100)
        for w in self.wiz_body.winfo_children():
            w.destroy()
        step()

    def _wiz_title(self, title, subtitle=""):
        tk.Label(self.wiz_body, text=title, bg=BG, fg=FG, anchor="w", justify="left",
                 font=("Segoe UI Semibold", 18), wraplength=680).pack(fill="x", anchor="w")
        if subtitle:
            tk.Label(self.wiz_body, text=subtitle, bg=BG, fg=MUTED, anchor="w", justify="left",
                     font=("Segoe UI", 10), wraplength=680).pack(fill="x", anchor="w", pady=(4, 0))
        tk.Frame(self.wiz_body, bg=BG, height=14).pack()

    def _wiz_pick(self, key, value, nxt):
        self._wiz[key] = value
        self._wiz_go(nxt)

    def _wiz_q_mode(self):
        self._wiz_title("Bienvenue \U0001F44B", "Que veux-tu faire ?")
        self._card(self.wiz_body, "▶", "Creer un nouveau serveur",
                   "Quelques questions et c'est pret.", lambda: self._wiz_go(self._wiz_q_type))
        self._card(self.wiz_body, "↻", "Reprendre une partie",
                   "Relancer un serveur deja installe.", lambda: self._wiz_go(self._wiz_q_existing))

    def _wiz_q_existing(self):
        self._wiz_title("Reprendre une partie", "Choisis le serveur a relancer.")
        servers = paths.list_servers()
        if not servers:
            tk.Label(self.wiz_body, text="Aucun serveur installe pour l'instant.",
                     bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 8))
            self._card(self.wiz_body, "▶", "Creer un nouveau serveur", "",
                       lambda: self._wiz_go(self._wiz_q_type))
            return
        for name in servers:
            self._card(self.wiz_body, "⬢", name, "", lambda n=name: self._wiz_load_existing(n))

    def _wiz_load_existing(self, name):
        self.existing_var.set(name)
        self._enter_advanced()
        self.nb.select(0)
        self._load_existing()

    def _wiz_q_type(self):
        self._wiz_title("Quel type de serveur ?", "Choisis selon ce que tu veux faire.")
        self._card(self.wiz_body, "▢", "Vanilla", "Le Minecraft d'origine, sans ajout.",
                   lambda: self._wiz_pick("loader", "vanilla", self._wiz_q_version))
        self._card(self.wiz_body, "✦", "Paper   (recommande)",
                   "Optimise et fluide, supporte les plugins.",
                   lambda: self._wiz_pick("loader", "paper", self._wiz_q_version))
        self._card(self.wiz_body, "⚙", "Moddé (Fabric / Forge)",
                   "De vrais mods : modpacks, machines, etc.",
                   lambda: self._wiz_go(self._wiz_q_loader_mods))

    def _wiz_q_loader_mods(self):
        self._wiz_title("Quel chargeur de mods ?", "Fabric est plus leger et moderne.")
        self._card(self.wiz_body, "◈", "Fabric", "Leger, mods modernes, mises a jour rapides.",
                   lambda: self._wiz_pick("loader", "fabric", self._wiz_q_version))
        self._card(self.wiz_body, "◉", "Forge", "Historique, gros mods et gros modpacks.",
                   lambda: self._wiz_pick("loader", "forge", self._wiz_q_version))

    def _wiz_q_version(self):
        self._wiz_title("Quelle version de Minecraft ?", "On recommande la plus recente.")
        loading = tk.Label(self.wiz_body, text="Chargement des versions...",
                           bg=BG, fg=MUTED, font=("Segoe UI", 10))
        loading.pack(anchor="w", pady=8)
        loader = self._wiz["loader"]

        def work():
            try:
                versions = downloaders.list_versions(loader, False)
            except Exception:
                versions = []
            self.ui(lambda: self._wiz_version_ready(versions, loading))

        threading.Thread(target=work, daemon=True).start()

    def _wiz_version_ready(self, versions, loading):
        loading.destroy()
        if not versions:
            tk.Label(self.wiz_body, text="Impossible de charger les versions (reseau ?).",
                     bg=BG, fg=DANGER, font=("Segoe UI", 10)).pack(anchor="w")
            return
        latest = versions[0]
        self._card(self.wiz_body, "✦", f"Derniere version   ({latest})",
                   "Le choix recommande.",
                   lambda: self._wiz_pick("version", latest, self._wiz_q_world))
        box = tk.Frame(self.wiz_body, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        box.pack(fill="x", pady=5)
        inner = tk.Frame(box, bg=PANEL)
        inner.pack(fill="x", padx=16, pady=13)
        tk.Label(inner, text="Ou choisir :", bg=PANEL, fg=FG,
                 font=("Segoe UI", 10)).pack(side="left")
        var = tk.StringVar(value=latest)
        ttk.Combobox(inner, textvariable=var, values=versions, state="readonly",
                     width=18).pack(side="left", padx=10)
        ttk.Button(inner, text="Continuer", style="Accent.TButton",
                   command=lambda: self._wiz_pick("version", var.get(),
                                                  self._wiz_q_world)).pack(side="left")

    def _wiz_q_world(self):
        self._wiz_title("Quel monde ?", "Aleatoire, ou une seed precise ?")
        self._card(self.wiz_body, "\U0001F3B2", "Monde aleatoire", "Une carte au hasard, la surprise.",
                   lambda: self._wiz_pick("seed", "", self._wiz_q_gamemode))
        box = tk.Frame(self.wiz_body, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        box.pack(fill="x", pady=5)
        inner = tk.Frame(box, bg=PANEL)
        inner.pack(fill="x", padx=16, pady=13)
        tk.Label(inner, text="J'ai une seed :", bg=PANEL, fg=FG,
                 font=("Segoe UI", 10)).pack(side="left")
        var = tk.StringVar()
        ttk.Entry(inner, textvariable=var, width=20).pack(side="left", padx=10)
        ttk.Button(inner, text="Continuer", style="Accent.TButton",
                   command=lambda: self._wiz_pick("seed", var.get().strip(),
                                                  self._wiz_q_gamemode)).pack(side="left")

    def _wiz_q_gamemode(self):
        self._wiz_title("Mode de jeu ?", "")
        self._card(self.wiz_body, "⚔", "Survie", "Points de vie, faim, monstres.",
                   lambda: self._wiz_pick("gamemode", "survival", self._wiz_q_remote))
        self._card(self.wiz_body, "✨", "Creatif", "Ressources illimitees, vol, construction libre.",
                   lambda: self._wiz_pick("gamemode", "creative", self._wiz_q_remote))

    def _wiz_q_remote(self):
        self._wiz_title("Avec qui vas-tu jouer ?", "")
        self._card(self.wiz_body, "\U0001F3E0", "Sur mon reseau (meme Wi-Fi)",
                   "Le plus simple : tout le monde est chez toi / sur le meme reseau.",
                   lambda: self._wiz_pick("remote", False, self._wiz_summary))
        self._card(self.wiz_body, "\U0001F310", "Avec des amis a distance (Internet)",
                   "On preparera un acces distant securise (tunnel, sans toucher a ta box).",
                   lambda: self._wiz_pick("remote", True, self._wiz_summary))

    def _wiz_summary(self):
        w = self._wiz
        self._wiz_title("Recapitulatif", "Verifie, puis lance la creation.")
        card = tk.Frame(self.wiz_body, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="x", pady=(0, 6))
        rows = [
            ("Type", w.get("loader", "-")),
            ("Version", w.get("version", "-")),
            ("Monde", f"seed « {w['seed']} »" if w.get("seed") else "aleatoire"),
            ("Mode de jeu", w.get("gamemode", "survival")),
            ("Joueurs", "a distance (Internet)" if w.get("remote") else "meme reseau (LAN)"),
            ("RAM", f"{DEFAULT_RAM} Mo (auto)"),
        ]
        for k, v in rows:
            r = tk.Frame(card, bg=PANEL)
            r.pack(fill="x", padx=16, pady=4)
            tk.Label(r, text=k, bg=PANEL, fg=MUTED, font=("Segoe UI", 10), width=14,
                     anchor="w").pack(side="left")
            tk.Label(r, text=str(v), bg=PANEL, fg=FG, font=("Segoe UI Semibold", 10),
                     anchor="w").pack(side="left")
        foot = tk.Frame(self.wiz_body, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        foot.pack(fill="x", pady=(8, 0))
        self._wiz_eula = tk.BooleanVar(value=False)
        ttk.Checkbutton(foot, text="J'accepte le CLUF Minecraft (obligatoire) - aka.ms/MinecraftEULA",
                        variable=self._wiz_eula).pack(anchor="w", padx=14, pady=10)
        ttk.Button(self.wiz_body, text="Creer le serveur   →", style="Accent.TButton",
                   command=self._wiz_finish).pack(anchor="w", pady=(16, 0))

    def _wiz_finish(self):
        if not self._wiz_eula.get():
            self._warn("Contrat de licence",
                       "L'acceptation du CLUF Minecraft est obligatoire pour continuer.")
            return
        w = self._wiz
        self.loader_var.set(w["loader"])
        self.snapshot_var.set(False)
        self.version_var.set(w["version"])
        self.seed_var.set(w.get("seed", ""))
        self.gamemode_var.set(w.get("gamemode", "survival"))
        self.eula_var.set(True)
        self._pending_remote = bool(w.get("remote"))
        self._enter_advanced()
        self.nb.select(0)
        self._on_prepare()

    def _modal(self, title, message, kind="info", ok="OK", cancel=None):
        """Boite de dialogue modale stylee (dark + emerald). Renvoie True si 'ok'."""
        colors = {"info": ACCENT, "ok": ACCENT, "warn": "#F59E0B", "error": DANGER}
        icons = {"info": "i", "ok": "✓", "warn": "!", "error": "✕"}
        accent = colors.get(kind, ACCENT)
        top = tk.Toplevel(self.root)
        top.title(title)
        top.configure(bg=PANEL)
        top.transient(self.root)
        top.resizable(False, False)
        tk.Frame(top, bg=accent, height=4).pack(fill="x")
        body = tk.Frame(top, bg=PANEL)
        body.pack(fill="both", expand=True, padx=24, pady=20)
        head = tk.Frame(body, bg=PANEL)
        head.pack(fill="x")
        badge_fg = "#06281C" if kind in ("info", "ok") else "white"
        tk.Label(head, text=icons.get(kind, "i"), bg=accent, fg=badge_fg,
                 font=("Segoe UI Semibold", 12), width=3, height=1).pack(side="left")
        tk.Label(head, text=title, bg=PANEL, fg=FG,
                 font=("Segoe UI Semibold", 13)).pack(side="left", padx=12)
        tk.Label(body, text=message, bg=PANEL, fg="#C7D2DE", font=("Segoe UI", 10),
                 justify="left", wraplength=440).pack(anchor="w", pady=(14, 0))
        btns = tk.Frame(body, bg=PANEL)
        btns.pack(anchor="e", pady=(20, 0))
        result = {"v": False}

        def do_ok():
            result["v"] = True
            top.destroy()

        if cancel:
            ttk.Button(btns, text=cancel, style="Ghost.TButton",
                       command=top.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text=ok, style="Accent.TButton", command=do_ok).pack(side="right")
        top.bind("<Return>", lambda e: do_ok())
        top.bind("<Escape>", lambda e: top.destroy())
        top.update_idletasks()
        w, h = top.winfo_width(), top.winfo_height()
        rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
        rw, rh = self.root.winfo_width(), self.root.winfo_height()
        top.geometry(f"+{rx + (rw - w) // 2}+{ry + (rh - h) // 3}")
        top.grab_set()
        self.root.wait_window(top)
        return result["v"]

    def _info(self, title, msg):
        return self._modal(title, msg, "ok")

    def _warn(self, title, msg):
        return self._modal(title, msg, "warn")

    def _error(self, title, msg):
        return self._modal(title, msg, "error")

    def _confirm(self, title, msg, ok="Confirmer", cancel="Annuler"):
        return self._modal(title, msg, "info", ok=ok, cancel=cancel)

    def _scroll_area(self, parent):
        """Rend le contenu d'un onglet scrollable : rien ne sort jamais de l'ecran.

        Renvoie un cadre interieur (deja padde) ou poser le contenu de l'onglet.
        """
        canvas = tk.Canvas(parent, bg=PANEL, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas, style="Panel.TFrame", padding=16)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        def _wheel(e):
            canvas.yview_scroll(int(-e.delta / 120), "units")

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        return inner

    def _hint_banner(self, parent, text):
        """Petit bandeau en haut d'un onglet : explique a quoi il sert / quand l'utiliser."""
        lbl = tk.Label(parent, text=text, bg=PANEL2, fg=MUTED,
                       font=("Segoe UI", 9), justify="left", anchor="w",
                       padx=14, pady=9, wraplength=920, highlightthickness=1,
                       highlightbackground=BORDER, highlightcolor=BORDER)
        lbl.pack(fill="x", pady=(0, 12))

    def _tab_config(self):
        outer = ttk.Frame(self.nb, style="Panel.TFrame")
        self.nb.add(outer, text="  Configuration  ")
        tab = self._scroll_area(outer)
        self._hint_banner(
            tab, "Role : regler le monde (mode de jeu, difficulte, seed, MOTD, distance de vue...).\n"
                 "Quand : avant de demarrer le serveur. Cliquer sur \"Appliquer maintenant\" "
                 "pour enregistrer.")

        g = ttk.Frame(tab, style="Panel.TFrame")
        g.pack(fill="x")

        def lbl(t, r, c):
            ttk.Label(g, text=t, style="Panel.TLabel").grid(row=r, column=c, sticky="w",
                                                            padx=(0, 6), pady=4)

        lbl("Seed (vide = aleatoire) :", 0, 0)
        self.seed_var = tk.StringVar()
        ttk.Entry(g, textvariable=self.seed_var, width=24).grid(row=0, column=1, sticky="w", pady=4)

        lbl("MOTD :", 0, 2)
        self.motd_var = tk.StringVar(value="Mon serveur Minecraft")
        ttk.Entry(g, textvariable=self.motd_var, width=26).grid(row=0, column=3, sticky="w", pady=4)

        lbl("Mode de jeu :", 1, 0)
        self.gamemode_var = tk.StringVar(value="survival")
        ttk.Combobox(g, textvariable=self.gamemode_var, values=GAMEMODES,
                     state="readonly", width=14).grid(row=1, column=1, sticky="w", pady=4)

        lbl("Difficulte :", 1, 2)
        self.difficulty_var = tk.StringVar(value="normal")
        ttk.Combobox(g, textvariable=self.difficulty_var, values=DIFFICULTIES,
                     state="readonly", width=14).grid(row=1, column=3, sticky="w", pady=4)

        lbl("Type de monde :", 2, 0)
        self.leveltype_var = tk.StringVar(value="Normal")
        ttk.Combobox(g, textvariable=self.leveltype_var, values=list(LEVEL_TYPES),
                     state="readonly", width=18).grid(row=2, column=1, sticky="w", pady=4)

        lbl("Joueurs max :", 2, 2)
        self.maxplayers_var = tk.StringVar(value="10")
        ttk.Entry(g, textvariable=self.maxplayers_var, width=8).grid(row=2, column=3, sticky="w",
                                                                     pady=4)

        lbl("Distance de rendu :", 3, 0)
        self.viewdist_var = tk.StringVar(value="10")
        ttk.Spinbox(g, from_=3, to=32, textvariable=self.viewdist_var, width=6).grid(
            row=3, column=1, sticky="w", pady=4)

        lbl("Distance simulation :", 3, 2)
        self.simdist_var = tk.StringVar(value="10")
        ttk.Spinbox(g, from_=3, to=32, textvariable=self.simdist_var, width=6).grid(
            row=3, column=3, sticky="w", pady=4)

        # Cases a cocher
        checks = ttk.Frame(tab, style="Panel.TFrame")
        checks.pack(fill="x", pady=(12, 0))
        self.hardcore_var = tk.BooleanVar(value=False)
        self.pvp_var = tk.BooleanVar(value=True)
        self.monsters_var = tk.BooleanVar(value=True)
        self.nether_var = tk.BooleanVar(value=True)
        self.flight_var = tk.BooleanVar(value=False)
        self.cmdblock_var = tk.BooleanVar(value=True)
        self.online_var = tk.BooleanVar(value=True)
        self.whitelist_var = tk.BooleanVar(value=False)
        cks = [
            ("Hardcore", self.hardcore_var),
            ("PVP", self.pvp_var),
            ("Monstres", self.monsters_var),
            ("Nether", self.nether_var),
            ("Vol autorise", self.flight_var),
            ("Command blocks", self.cmdblock_var),
            ("Mode en ligne (premium)", self.online_var),
            ("Whitelist", self.whitelist_var),
        ]
        for i, (t, v) in enumerate(cks):
            ttk.Checkbutton(checks, text=t, variable=v).grid(row=i // 4, column=i % 4,
                                                             sticky="w", padx=6, pady=4)

        ttk.Label(tab, style="Hint.TLabel",
                  text="Astuce : 'Plat' = superflat. Skyblock/Bedwars necessitent un datapack ou "
                       "un plugin (Paper). La config est appliquee a la preparation/au demarrage."
                  ).pack(anchor="w", pady=(12, 0))
        ttk.Button(tab, text="Appliquer la config maintenant",
                   command=self._apply_config_now).pack(anchor="w", pady=(8, 0))

    def _tab_mods(self):
        outer = ttk.Frame(self.nb, style="Panel.TFrame")
        self.nb.add(outer, text="  Mods / Plugins  ")
        tab = self._scroll_area(outer)
        self._hint_banner(
            tab, "Role : ajouter des mods / plugins (recherche Modrinth integree).\n"
                 "Quand : apres avoir prepare un serveur Paper ou Fabric. Vanilla : aucun mod.")

        self.mods_info = ttk.Label(
            tab, style="Panel.TLabel",
            text="Preparez d'abord un serveur. Paper -> plugins, Fabric -> mods, Vanilla -> aucun.")
        self.mods_info.pack(anchor="w")

        self.mods_list = tk.Listbox(tab, height=12, bg=PANEL, fg=FG,
                                    selectbackground=ACCENT, relief="flat",
                                    font=("Consolas", 9))
        self.mods_list.pack(fill="both", expand=True, pady=8)

        bar = ttk.Frame(tab, style="Panel.TFrame")
        bar.pack(fill="x")
        ttk.Button(bar, text="Ajouter des .jar", command=self._mods_add).pack(side="left")
        ttk.Button(bar, text="Supprimer la selection", style="Danger.TButton",
                   command=self._mods_remove).pack(side="left", padx=6)
        ttk.Button(bar, text="Rafraichir", command=self._mods_refresh).pack(side="left")
        ttk.Button(bar, text="Ouvrir le dossier", command=self._mods_open).pack(side="left", padx=6)

        # Recherche Modrinth
        search = ttk.LabelFrame(tab, text="Chercher sur Modrinth (telechargement direct)", padding=8)
        search.pack(fill="x", pady=(10, 0))
        self.modr_query = tk.StringVar()
        e = ttk.Entry(search, textvariable=self.modr_query)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self._modr_search())
        ttk.Button(search, text="Rechercher", command=self._modr_search).pack(side="left", padx=6)
        ttk.Button(search, text="Installer la selection", style="Accent.TButton",
                   command=self._modr_install).pack(side="left")
        self.modr_list = tk.Listbox(tab, height=7, bg=PANEL, fg=FG,
                                    selectbackground=ACCENT, relief="flat", font=("Consolas", 9))
        self.modr_list.pack(fill="x", pady=(6, 0))
        self._modr_results = []  # slugs alignes avec modr_list

    def _tab_admin(self):
        outer = ttk.Frame(self.nb, style="Panel.TFrame")
        self.nb.add(outer, text="  Admin  ")
        tab = self._scroll_area(outer)
        self._hint_banner(
            tab, "Role : envoyer des commandes au serveur en jeu (op, kick, donner un objet, "
                 "regler l'heure ou la meteo, annonces...).\n"
                 "Quand : uniquement lorsque le serveur est demarre.")

        pl = ttk.Frame(tab, style="Panel.TFrame")
        pl.pack(fill="x", pady=(10, 0))
        ttk.Label(pl, text="Joueur :", style="Panel.TLabel").pack(side="left")
        self.admin_player = tk.StringVar()
        ttk.Entry(pl, textvariable=self.admin_player, width=20).pack(side="left", padx=6)

        pbtns = ttk.Frame(tab, style="Panel.TFrame")
        pbtns.pack(fill="x", pady=8)
        player_actions = [
            ("OP", "op {p}"), ("De-OP", "deop {p}"),
            ("Kick", "kick {p}"), ("Ban", "ban {p}"),
            ("Whitelist +", "whitelist add {p}"), ("Whitelist -", "whitelist remove {p}"),
            ("TP vers spawn", "spawnpoint {p}"),
        ]
        for i, (label, tmpl) in enumerate(player_actions):
            ttk.Button(pbtns, text=label, command=lambda t=tmpl: self._admin_player(t)).grid(
                row=i // 4, column=i % 4, sticky="w", padx=4, pady=4)

        gm = ttk.LabelFrame(tab, text="Mode de jeu du joueur", padding=8)
        gm.pack(fill="x", pady=6)
        for i, mode in enumerate(GAMEMODES):
            ttk.Button(gm, text=mode.capitalize(),
                       command=lambda m=mode: self._admin_player(f"gamemode {m} {{p}}")).grid(
                row=0, column=i, padx=4)

        world = ttk.LabelFrame(tab, text="Monde / partie", padding=8)
        world.pack(fill="x", pady=6)
        world_actions = [
            ("Jour", "time set day"), ("Nuit", "time set night"),
            ("Beau temps", "weather clear"), ("Pluie", "weather rain"),
            ("Sauvegarder", "save-all"), ("Liste joueurs", "list"),
            ("Difficulte facile", "difficulty easy"), ("Difficulte difficile", "difficulty hard"),
        ]
        for i, (label, cmd) in enumerate(world_actions):
            ttk.Button(world, text=label, command=lambda c=cmd: self._admin_cmd(c)).grid(
                row=i // 4, column=i % 4, sticky="w", padx=4, pady=4)

        backup = ttk.LabelFrame(tab, text="Sauvegardes du monde", padding=8)
        backup.pack(fill="x", pady=6)
        ttk.Button(backup, text="Sauvegarder le monde (.zip)",
                   command=self._backup_world).pack(side="left")
        ttk.Button(backup, text="Ouvrir le dossier des sauvegardes",
                   command=self._open_backups).pack(side="left", padx=6)

        say = ttk.Frame(tab, style="Panel.TFrame")
        say.pack(fill="x", pady=(6, 0))
        ttk.Label(say, text="Message :", style="Panel.TLabel").pack(side="left")
        self.say_var = tk.StringVar()
        e = ttk.Entry(say, textvariable=self.say_var)
        e.pack(side="left", fill="x", expand=True, padx=6)
        e.bind("<Return>", lambda ev: self._admin_say())
        ttk.Button(say, text="Annoncer (say)", command=self._admin_say).pack(side="left")

        # Ligne de commande brute (deplacee depuis sous la console). Pour les habitues.
        cmdf = ttk.LabelFrame(tab, text="Commande serveur (avance)", padding=8)
        cmdf.pack(fill="x", pady=(10, 0))
        self.cmd_var = tk.StringVar()
        ce = ttk.Entry(cmdf, textvariable=self.cmd_var)
        ce.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ce.bind("<Return>", lambda ev: self._send_command())
        ttk.Button(cmdf, text="Envoyer", style="Accent.TButton",
                   command=self._send_command).pack(side="left")

    # ---------- Helpers thread-safe ----------
    def ui(self, func):
        self.root.after(0, func)

    def log(self, text):
        self._console_tail.append(text)
        def _append():
            self.console.configure(state="normal")
            self.console.insert("end", text + "\n")
            self.console.see("end")
            self.console.configure(state="disabled")
        self.ui(_append)

    def _handle_output(self, line):
        """Sortie du serveur : log + detection des connexions/deconnexions."""
        self.log(line)
        m = JOIN_RE.search(line)
        if m:
            self._players.add(m.group(1))
            self._update_players()
        m = LEFT_RE.search(line)
        if m:
            self._players.discard(m.group(1))
            self._update_players()

    def _update_players(self):
        n = len(self._players)
        label = f"{n} joueur" + ("s" if n > 1 else "")
        self.ui(lambda: self.players_lbl.configure(text=label))

    def set_status(self, text):
        self.ui(lambda: self.status_var.set(text))

    def _copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status(f"Copie dans le presse-papier : {text}")

    def _addr(self, ip):
        return f"{ip}:{self.port_var.get()}"

    # ---------- Versions ----------
    def _refresh_versions(self):
        loader = self.loader_var.get()
        self.set_status(f"Chargement des versions {loader}...")

        def work():
            try:
                versions = downloaders.list_versions(loader, self.snapshot_var.get())
                self.ui(lambda: self._set_versions(versions))
                self.set_status(f"{len(versions)} versions {loader} disponibles.")
            except Exception as e:
                self.set_status(f"Erreur liste versions : {e}")

        threading.Thread(target=work, daemon=True).start()

    def _set_versions(self, versions):
        self.version_cb["values"] = versions
        if versions:
            self.version_var.set(versions[0])

    # ---------- Properties ----------
    def _collect_properties(self):
        return dict(
            server_port=int(self.port_var.get()),
            online_mode=self.online_var.get(),
            max_players=int(self.maxplayers_var.get()),
            motd=self.motd_var.get(),
            level_seed=self.seed_var.get(),
            gamemode=self.gamemode_var.get(),
            difficulty=self.difficulty_var.get(),
            hardcore=self.hardcore_var.get(),
            level_type=LEVEL_TYPES[self.leveltype_var.get()],
            pvp=self.pvp_var.get(),
            view_distance=int(self.viewdist_var.get()),
            simulation_distance=int(self.simdist_var.get()),
            spawn_monsters=self.monsters_var.get(),
            allow_nether=self.nether_var.get(),
            allow_flight=self.flight_var.get(),
            white_list=self.whitelist_var.get(),
            enable_command_block=self.cmdblock_var.get(),
        )

    def _apply_config_now(self):
        if not self.current_dir:
            self._info("Configuration",
                       "Veuillez d'abord preparer un serveur (onglet Serveur).")
            return
        try:
            update_properties(self.current_dir, **self._collect_properties())
            self.set_status("Config ecrite dans server.properties.")
            self.log("Config appliquee. (Redemarre le serveur pour qu'elle prenne effet.)")
        except ValueError as e:
            self._warn("Config", f"Valeur invalide : {e}")

    # ---------- Preparer ----------
    # ---------- Serveurs installes (recharger une partie) ----------
    def _refresh_existing(self):
        servers = paths.list_servers()
        self.existing_cb["values"] = servers
        if servers and not self.existing_var.get():
            self.existing_var.set(servers[0])

    def _apply_props_to_ui(self, props):
        """Recopie une config server.properties lue vers les champs de l'UI (onglet Config)."""
        def b(key, default="true"):
            return str(props.get(key, default)).lower() == "true"

        try:
            self.port_var.set(props.get("server-port", self.port_var.get()))
            self.online_var.set(b("online-mode"))
            self.maxplayers_var.set(props.get("max-players", self.maxplayers_var.get()))
            self.motd_var.set(props.get("motd", self.motd_var.get()))
            self.seed_var.set(props.get("level-seed", ""))
            self.gamemode_var.set(props.get("gamemode", self.gamemode_var.get()))
            self.difficulty_var.set(props.get("difficulty", self.difficulty_var.get()))
            self.hardcore_var.set(b("hardcore", "false"))
            lt = props.get("level-type", "minecraft:normal")
            label = next((k for k, v in LEVEL_TYPES.items() if v == lt), None)
            if label:
                self.leveltype_var.set(label)
            self.pvp_var.set(b("pvp"))
            self.viewdist_var.set(props.get("view-distance", self.viewdist_var.get()))
            self.simdist_var.set(props.get("simulation-distance", self.simdist_var.get()))
            self.monsters_var.set(b("spawn-monsters"))
            self.nether_var.set(b("allow-nether"))
            self.flight_var.set(b("allow-flight", "false"))
            self.whitelist_var.set(b("white-list", "false"))
            self.cmdblock_var.set(b("enable-command-block", "false"))
        except (KeyError, AttributeError):
            pass  # config partielle : on garde les valeurs par defaut pour le reste

    def _load_existing(self):
        name = self.existing_var.get()
        if not name:
            return
        sdir = os.path.join(paths.SERVERS_DIR, name)
        if not os.path.isdir(sdir):
            self._refresh_existing()
            return
        loader, _, version = name.partition("-")  # "paper-1.21.11" -> paper / 1.21.11
        if not version:
            self.log(f"Nom de serveur inattendu : {name}")
            return

        self.loader_var.set(loader)
        self.version_var.set(version)
        self._apply_props_to_ui(read_properties(sdir))
        self.prepare_btn.configure(state="disabled")
        self.start_btn.configure(state="disabled")
        self.prepared = False

        def work():
            try:
                feature = required_java(version)
                self.log(f"=== Chargement de {name} (Java {feature}) ===")
                java_exe = java_manager.ensure_java(
                    feature, log=self.log,
                    progress=lambda d, t: self.set_status(f"Java : {d*100//t}%"))
                self.java_exe = java_exe
                self.current_dir = sdir
                self.prepared = True
                self.log("=== Serveur charge, pret a demarrer ===")
                self.set_status(f"Serveur charge : {name}")
                self.ui(lambda: self.start_btn.configure(state="normal"))
                self.ui(self._mods_refresh)
                self.ui(self._update_guide)
            except Exception as e:
                self.log(f"ERREUR chargement : {e}")
                self.set_status(f"Erreur : {e}")
            finally:
                self.ui(lambda: self.prepare_btn.configure(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    def _delete_existing(self):
        name = self.existing_var.get()
        if not name:
            return
        if self.server and self.server.is_running():
            self._warn("Action impossible",
                       "Veuillez arreter le serveur avant de le supprimer.")
            return
        if not self._confirm(
                "Supprimer le serveur",
                f"Supprimer definitivement « {name} » (monde, configuration, mods) ?\n\n"
                "Cette action est irreversible. Pensez a sauvegarder le monde au prealable.",
                ok="Supprimer", cancel="Annuler"):
            return
        sdir = os.path.join(paths.SERVERS_DIR, name)
        try:
            shutil.rmtree(sdir)
            self.log(f"Serveur supprime : {name}")
            if self.current_dir == sdir:
                self.current_dir = None
                self.prepared = False
                self.start_btn.configure(state="disabled")
            self.existing_var.set("")
            self._refresh_existing()
        except OSError as e:
            self._error("Suppression impossible", f"La suppression a echoue :\n{e}")

    def _on_prepare(self):
        if not self.eula_var.get():
            self._warn(
                "Contrat de licence",
                "L'acceptation du CLUF Minecraft est obligatoire pour heberger un serveur.")
            return
        loader = self.loader_var.get()
        version = self.version_var.get()
        if not version:
            self._warn("Version manquante", "Veuillez selectionner une version.")
            return

        self.prepare_btn.configure(state="disabled")
        self.start_btn.configure(state="disabled")
        self.prepared = False

        def work():
            try:
                feature = required_java(version)
                self.log(f"=== Preparation {loader} {version} (Java {feature}) ===")
                java_exe = java_manager.ensure_java(
                    feature, log=self.log,
                    progress=lambda d, t: self.set_status(f"Java : {d*100//t}%"))

                sdir = paths.server_dir(loader, version)
                downloaders.prepare(
                    loader, version, sdir, java_exe, log=self.log,
                    progress=lambda d, t: self.set_status(f"Telechargement : {d*100//t}%"))

                accept_eula(sdir)
                update_properties(sdir, **self._collect_properties())

                self.java_exe = java_exe
                self.current_dir = sdir
                self.prepared = True
                self.log("=== Pret a demarrer ===")
                self.set_status("Preparation terminee.")
                self.ui(lambda: self.start_btn.configure(state="normal"))
                self.ui(self._mods_refresh)
                self.ui(self._refresh_existing)
                self.ui(self._update_guide)
                if getattr(self, "_pending_remote", False):
                    self._pending_remote = False
                    self.ui(lambda: self._info(
                        "Jouer a distance",
                        "Le serveur est pret.\n\nPour jouer avec des amis par Internet :\n"
                        "  1. demarrer le serveur (Etape 2) ;\n"
                        "  2. onglet Serveur, Etape 3, « Activer l'acces distant ».\n\n"
                        "Pour jouer sur le meme reseau, aucune action supplementaire n'est requise."))
            except Exception as e:
                self.log(f"ERREUR : {e}")
                self.set_status(f"Erreur : {e}")
            finally:
                self.ui(lambda: self.prepare_btn.configure(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    # ---------- Demarrer / Arreter ----------
    def _on_start(self):
        if not self.prepared:
            return
        try:
            ram = int(self.ram_var.get())
        except ValueError:
            self._warn("RAM invalide", "Veuillez saisir un nombre de Mo valide.")
            return
        safe = sysinfo.max_safe_ram_mb()
        if ram > safe and not self._confirm(
                "RAM elevee",
                f"{ram} Mo depasse le maximum conseille ({safe} Mo).\nDemarrer malgre tout ?",
                ok="Demarrer", cancel="Annuler"):
            return
        try:
            update_properties(self.current_dir, **self._collect_properties())
        except ValueError as e:
            self._warn("Valeur invalide", f"Reglage invalide : {e}")
            return

        # Garde-fou : port deja occupe (un serveur tourne peut-etre deja, ou un
        # demarrage precedent ne s'est pas ferme) -> evite un crash "FAILED TO BIND".
        port = self.port_var.get()
        if netinfo.port_in_use(port):
            if not self._confirm(
                    "Port deja utilise",
                    f"Le port {port} est deja utilise : un serveur tourne probablement deja "
                    "(ou un demarrage precedent ne s'est pas ferme correctement).\n\n"
                    "Liberer le port et demarrer malgre tout ?",
                    ok="Liberer et demarrer", cancel="Annuler"):
                return
            self.log(f"Liberation du port {port}...")
            netinfo.free_port(port)
            time.sleep(1)
            if netinfo.port_in_use(port):
                self._warn(
                    "Port toujours occupe",
                    f"Le port {port} est encore utilise.\n\n"
                    "Fermez le programme qui l'occupe, ou choisissez un autre port (Etape 1).")
                return

        args = downloaders.launch_args(self.loader_var.get(), self.java_exe,
                                       self.current_dir, ram)
        self._user_stopped = False
        self._players.clear()
        self._update_players()
        self.server = ServerProcess(
            args, self.current_dir,
            on_output=self._handle_output, on_exit=self._on_server_exit)
        self.server.start()
        self.start_btn.configure(state="disabled")
        self.prepare_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.state_dot.configure(text="● en marche", foreground="#7fd35a")
        self.set_status(f"Serveur lance sur le port {self.port_var.get()}.")
        self.log(f">>> Demarrage ({ram} Mo de RAM) <<<")
        self._update_guide()

    def _on_server_exit(self, code):
        self.log(f">>> Serveur arrete (code {code}) <<<")
        self.set_status("Serveur arrete.")
        self._players.clear()
        self._update_players()
        self.ui(lambda: self.state_dot.configure(text="● arrete", foreground="#c8553d"))
        self.ui(lambda: self.stop_btn.configure(state="disabled"))
        self.ui(lambda: self.start_btn.configure(state="normal"))
        self.ui(lambda: self.prepare_btn.configure(state="normal"))
        self.ui(self._update_guide)
        # Redemarrage auto si crash (code != 0) et arret non demande par l'utilisateur
        if self.autorestart_var.get() and code != 0 and not self._user_stopped:
            self.log(">>> Crash detecte : redemarrage automatique dans 5s... <<<")
            self.ui(lambda: self.root.after(5000, self._auto_restart))

    def _auto_restart(self):
        if not (self.server and self.server.is_running()):
            self._on_start()

    def _on_stop(self):
        if self.server and self.server.is_running():
            self._user_stopped = True
            self.log(">>> Arret en cours (sauvegarde du monde)... <<<")
            self.server.stop()

    def _free_port(self):
        """Libere le port du serveur : arret propre du notre, puis kill de ce qui reste."""
        port = self.port_var.get()
        if not netinfo.port_in_use(port):
            self._info("Port libre", f"Le port {port} est deja libre. Aucune action necessaire.")
            return
        if not self._confirm(
                "Liberer le port",
                f"Le port {port} est utilise. Cette action va ARRETER le programme qui "
                "l'occupe (le serveur en cours, ou un serveur reste ouvert).\n\n"
                "Attention : un serveur non sauvegarde peut perdre les dernieres minutes de jeu.\n\n"
                "Continuer ?",
                ok="Liberer le port", cancel="Annuler"):
            return

        def work():
            # 1) Si c'est NOTRE serveur, on tente un arret propre (sauvegarde du monde).
            if self.server and self.server.is_running():
                self._user_stopped = True
                self.log(">>> Arret du serveur pour liberer le port... <<<")
                self.server.stop()
                for _ in range(10):
                    if not (self.server and self.server.is_running()):
                        break
                    time.sleep(1)
            # 2) On force la liberation de ce qui occupe encore le port.
            if netinfo.port_in_use(port):
                self.log(f"Liberation forcee du port {port}...")
                netinfo.free_port(port)
                time.sleep(1)
            if netinfo.port_in_use(port):
                self.ui(lambda: self._warn(
                    "Port toujours occupe",
                    f"Le port {port} est encore utilise. Reessayez, ou redemarrez le PC."))
            else:
                self.log(f"Port {port} libere.")
                self.ui(lambda: self._info(
                    "Port libere", f"Le port {port} est maintenant libre. Vous pouvez demarrer."))
            self.ui(self._update_guide)

        threading.Thread(target=work, daemon=True).start()

    def _send_command(self):
        cmd = self.cmd_var.get().strip()
        if cmd and self.server and self.server.is_running():
            self.server.send(cmd)
            self.log(f"> {cmd}")
            self.cmd_var.set("")

    # ---------- Admin ----------
    def _server_running(self):
        if not (self.server and self.server.is_running()):
            self.set_status("Le serveur doit etre demarre pour cette action.")
            return False
        return True

    def _admin_cmd(self, command):
        if self._server_running():
            self.server.send(command)
            self.log(f"> {command}")

    def _admin_player(self, template):
        p = self.admin_player.get().strip()
        if not p:
            self.set_status("Entre un nom de joueur d'abord.")
            return
        self._admin_cmd(template.format(p=p))

    def _admin_say(self):
        msg = self.say_var.get().strip()
        if msg:
            self._admin_cmd(f"say {msg}")
            self.say_var.set("")

    # ---------- Sauvegardes ----------
    def _backups_dir(self):
        d = os.path.join(paths.DATA_DIR, "backups")
        os.makedirs(d, exist_ok=True)
        return d

    def _backup_world(self):
        if not self.current_dir:
            self._info("Sauvegarde", "Veuillez d'abord preparer un serveur.")
            return
        world = os.path.join(self.current_dir, "world")
        if not os.path.isdir(world):
            self._info("Sauvegarde", "Pas encore de monde a sauvegarder "
                                              "(demarre le serveur au moins une fois).")
            return
        if self.server and self.server.is_running():
            self.server.send("save-all")  # force l'ecriture sur disque avant la copie
        import datetime
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{os.path.basename(self.current_dir)}_{stamp}"
        base = os.path.join(self._backups_dir(), name)

        def work():
            try:
                self.log(f"Sauvegarde du monde en cours ({name}.zip)...")
                shutil.make_archive(base, "zip", world)
                self.log("Sauvegarde terminee.")
                self.set_status(f"Monde sauvegarde : {name}.zip")
            except OSError as e:
                self.log(f"Erreur sauvegarde : {e}")

        threading.Thread(target=work, daemon=True).start()

    def _open_backups(self):
        d = self._backups_dir()
        try:
            os.startfile(d)
        except OSError:
            webbrowser.open(d)

    # ---------- Mods ----------
    def _mods_dir(self):
        if not self.current_dir:
            return None
        return paths.content_folder(self.loader_var.get(), self.current_dir)

    def _mods_refresh(self):
        d = self._mods_dir()
        self.mods_list.delete(0, "end")
        if d is None:
            self.mods_info.configure(
                text="Vanilla : aucun mod / plugin possible. Choisissez Paper ou Fabric.")
            return
        os.makedirs(d, exist_ok=True)
        self.mods_info.configure(text=f"Dossier : {d}")
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(".jar"):
                self.mods_list.insert("end", f)

    def _mods_add(self):
        d = self._mods_dir()
        if d is None:
            self._info("Mods / Plugins", "Veuillez d'abord preparer un serveur Paper ou Fabric.")
            return
        files = filedialog.askopenfilenames(title="Choisir des .jar",
                                            filetypes=[("Java archive", "*.jar")])
        os.makedirs(d, exist_ok=True)
        for src in files:
            try:
                shutil.copy(src, os.path.join(d, os.path.basename(src)))
                self.log(f"Mod ajoute : {os.path.basename(src)}")
            except OSError as e:
                self.log(f"Erreur copie {src} : {e}")
        self._mods_refresh()

    def _mods_remove(self):
        d = self._mods_dir()
        if d is None:
            return
        for i in reversed(self.mods_list.curselection()):
            name = self.mods_list.get(i)
            try:
                os.remove(os.path.join(d, name))
                self.log(f"Mod supprime : {name}")
            except OSError as e:
                self.log(f"Erreur suppression {name} : {e}")
        self._mods_refresh()

    def _mods_open(self):
        d = self._mods_dir()
        if d is None:
            self._info("Mods / Plugins", "Veuillez d'abord preparer un serveur Paper ou Fabric.")
            return
        os.makedirs(d, exist_ok=True)
        try:
            os.startfile(d)
        except OSError:
            webbrowser.open(d)

    # ---------- Modrinth ----------
    def _modr_search(self):
        q = self.modr_query.get().strip()
        if not q:
            return
        loader = self.loader_var.get()
        version = self.version_var.get()
        self.modr_list.delete(0, "end")
        self.set_status("Recherche Modrinth...")

        def work():
            try:
                results = modrinth.search(q, loader, version)
                self._modr_results = results
                self.ui(lambda: self._modr_show(results))
                self.set_status(f"{len(results)} resultats Modrinth.")
            except Exception as e:
                self.set_status(f"Erreur Modrinth : {e}")

        threading.Thread(target=work, daemon=True).start()

    def _modr_show(self, results):
        self.modr_list.delete(0, "end")
        for r in results:
            self.modr_list.insert("end", f"{r['title']}  ({r['downloads']} dl)  - {r['slug']}")

    def _modr_install(self):
        d = self._mods_dir()
        if d is None:
            self._info("Modrinth", "Veuillez d'abord preparer un serveur Paper, Fabric ou Forge.")
            return
        sel = self.modr_list.curselection()
        if not sel:
            self.set_status("Selectionne un mod dans la liste.")
            return
        proj = self._modr_results[sel[0]]
        loader = self.loader_var.get()
        version = self.version_var.get()
        os.makedirs(d, exist_ok=True)

        def work():
            try:
                self.log(f"Installation Modrinth : {proj['title']}...")
                name = modrinth.install(proj["slug"], loader, version, d, log=self.log)
                self.log(f"Installe : {name}")
                self.ui(self._mods_refresh)
            except Exception as e:
                self.log(f"Erreur installation Modrinth : {e}")
                self.set_status(f"Erreur : {e}")

        threading.Thread(target=work, daemon=True).start()

    # ---------- playit.gg ----------
    def _toggle_playit(self):
        if self.playit_agent and self.playit_agent.is_running():
            self._user_stopped_playit = True
            self.playit_agent.stop()
            self.playit_btn.configure(text="Demarrer le tunnel")
            self._update_guide()
            return

        if not playit.is_configured():
            self._info(
                "Acces distant non configure",
                "L'acces distant n'est pas encore configure.\n\n"
                "Cliquez sur « Activer l'acces distant » pour le mettre en place.")
            return

        secret = playit.load_config().get("secret", "")
        self._user_stopped_playit = False

        def work():
            try:
                playit.ensure_agent(log=self.log)
                self.log("Demarrage du tunnel playit.gg...")
                self.playit_agent = playit.PlayitAgent(
                    on_output=self.log, on_exit=self._on_playit_exit)
                self.playit_agent.start(secret)
                self.ui(lambda: self.playit_btn.configure(text="Arreter le tunnel"))
                self.ui(self._update_guide)
                if self._playit_address:
                    self.log(f"Adresse a donner aux joueurs : {self._playit_address}")
            except Exception as e:
                self.log(f"Erreur playit : {e}")
                self.set_status(f"Erreur playit : {e}")

        threading.Thread(target=work, daemon=True).start()

    def _on_playit_exit(self, code):
        self.log(f">>> playit arrete (code {code}) <<<")
        self.ui(lambda: self.playit_btn.configure(text="Demarrer le tunnel"))
        self.ui(self._update_guide)
        # Code != 0 sans arret manuel = probleme (secret invalide le plus souvent)
        if code != 0 and not getattr(self, "_user_stopped_playit", False):
            self.ui(lambda: self._error(
                "Acces distant interrompu",
                "Le tunnel s'est arrete de maniere inattendue.\n\n"
                "Cause la plus probable : la cle d'acces est invalide ou expiree.\n"
                "Relancez « Activer l'acces distant » pour en regenerer une."))

    def _config_playit(self):
        if not self._confirm(
                "Activer l'acces distant",
                "La configuration est entierement automatique : elle permet de jouer a "
                "distance sans ouvrir de port sur la box.\n\n"
                "Une page playit.gg va s'ouvrir :\n"
                "  1. se connecter (ou creer un compte, puis verifier l'e-mail) ;\n"
                "  2. cliquer sur « Allow » pour autoriser l'application.\n\n"
                "L'application recupere ensuite la cle et cree le tunnel automatiquement.",
                ok="Continuer", cancel="Annuler"):
            return
        self._playit_cancel = False
        try:
            local_port = int(self.port_var.get())
        except (TypeError, ValueError):
            local_port = 25565

        def work():
            try:
                # 1) Autorisation playit (le seul clic manuel) -> secret key.
                secret = playit.claim_interactive(
                    self.log, lambda: getattr(self, "_playit_cancel", False))
                # 2) Telecharge l'agent et DEMARRE le daemon (il s'enregistre + servira
                #    le tunnel). Le tunnel ne peut etre cree qu'apres ce demarrage.
                playit.ensure_agent(log=self.log)
                if self.playit_agent and self.playit_agent.is_running():
                    self._user_stopped_playit = True
                    self.playit_agent.stop()
                    time.sleep(1)
                self._user_stopped_playit = False
                self.playit_agent = playit.PlayitAgent(
                    on_output=self.log, on_exit=self._on_playit_exit)
                self.playit_agent.start(secret)
                self.ui(lambda: self.playit_btn.configure(text="Arreter le tunnel"))
                # 3) Cree (ou reutilise) le tunnel Minecraft et recupere l'adresse.
                self.log("Agent demarre, configuration du tunnel Minecraft...")
                address = playit.ensure_tunnel(secret, local_port, self.log)
                playit.save_config(secret, address)
                self._playit_address = address
                self.ui(lambda: self.playit_addr.configure(text=address))
                self.ui(self._update_guide)
                self.ui(lambda: self._info(
                    "Acces distant pret",
                    "Configuration terminee, le tunnel est actif.\n\n"
                    f"Adresse a communiquer aux joueurs (dans Minecraft) :\n\n    {address}\n\n"
                    "Demarrez le serveur s'il ne l'est pas : le tunnel suit automatiquement."))
            except Exception as e:
                msg = str(e)  # capture avant que Python ne supprime 'e' a la sortie du except
                self.log(f"Echec config playit : {msg}")
                self.ui(lambda: self._error(
                    "Configuration de l'acces distant",
                    f"La configuration automatique a echoue :\n\n{msg}"))

        threading.Thread(target=work, daemon=True).start()

    def _copy_playit(self):
        if self._playit_address:
            self._copy(self._playit_address)
        else:
            self.set_status("Aucune adresse playit (clique sur 'Configurer').")

    # ---------- Profil (config persistante) ----------
    def _gather_state(self):
        return dict(
            loader=self.loader_var.get(), version=self.version_var.get(),
            ram=self.ram_var.get(), port=self.port_var.get(),
            snapshots=self.snapshot_var.get(), autorestart=self.autorestart_var.get(),
            seed=self.seed_var.get(), motd=self.motd_var.get(),
            gamemode=self.gamemode_var.get(), difficulty=self.difficulty_var.get(),
            leveltype=self.leveltype_var.get(), maxplayers=self.maxplayers_var.get(),
            viewdist=self.viewdist_var.get(), simdist=self.simdist_var.get(),
            hardcore=self.hardcore_var.get(), pvp=self.pvp_var.get(),
            monsters=self.monsters_var.get(), nether=self.nether_var.get(),
            flight=self.flight_var.get(), cmdblock=self.cmdblock_var.get(),
            online=self.online_var.get(), whitelist=self.whitelist_var.get(),
        )

    def _save_config(self):
        config_store.save(self._gather_state())

    def _load_config(self):
        c = config_store.load()
        if not c:
            return
        setters = {
            "loader": self.loader_var, "version": self.version_var, "ram": self.ram_var,
            "port": self.port_var, "seed": self.seed_var, "motd": self.motd_var,
            "gamemode": self.gamemode_var, "difficulty": self.difficulty_var,
            "leveltype": self.leveltype_var, "maxplayers": self.maxplayers_var,
            "viewdist": self.viewdist_var, "simdist": self.simdist_var,
        }
        for key, var in setters.items():
            if key in c:
                var.set(c[key])
        bools = {
            "snapshots": self.snapshot_var, "autorestart": self.autorestart_var,
            "hardcore": self.hardcore_var, "pvp": self.pvp_var, "monsters": self.monsters_var,
            "nether": self.nether_var, "flight": self.flight_var, "cmdblock": self.cmdblock_var,
            "online": self.online_var, "whitelist": self.whitelist_var,
        }
        for key, var in bools.items():
            if key in c:
                var.set(bool(c[key]))

    # ---------- Contexte pour le rapport de crash ----------
    def crash_context(self):
        return {
            "loader": self.loader_var.get(),
            "version": self.version_var.get(),
            "ram": self.ram_var.get(),
            "port": self.port_var.get(),
            "prepared": self.prepared,
            "current_dir": self.current_dir,
            "server_running": bool(self.server and self.server.is_running()),
            "players_online": len(self._players),
            "_console_tail": list(self._console_tail),
        }

    # ---------- Reseau / divers ----------
    def _load_network_info(self):
        self._lan_ip = netinfo.lan_ip()
        self.ui(lambda: self.lan_lbl.configure(text=self._addr(self._lan_ip)))
        self.ui(lambda: self.head_status.configure(text=f"LAN {self._lan_ip}"))
        self.ui(self._update_guide)
        self._public_ip = netinfo.public_ip()
        self.ui(lambda: self.pub_lbl.configure(text=self._addr(self._public_ip)))

    def _show_remote_info(self):
        port = self.port_var.get()
        msg = (
            "=== Jouer sur le meme reseau (LAN / Wi-Fi) ===\n"
            f"Adresse a communiquer aux joueurs :  {self._lan_ip}:{port}\n\n"
            "=== Jouer a distance (Internet) ===\n"
            f"IP publique :  {self._public_ip}:{port}\n\n"
            f"Pour un acces exterieur, ouvrir le port {port} (TCP) sur la box :\n"
            "  1. Interface de la box (souvent http://192.168.1.1)\n"
            "  2. Section 'NAT/PAT' ou 'Redirection de ports'\n"
            f"  3. Rediriger le port {port} (TCP) vers {self._lan_ip}\n\n"
            "Alternative sans configuration de box : le tunnel (bouton \"Activer l'acces distant\").\n"
            "Penser aussi a autoriser java.exe dans le pare-feu Windows."
        )
        self._info("Acces au serveur", msg)

    def _open_folder(self):
        target = self.current_dir or paths.DATA_DIR
        try:
            os.startfile(target)
        except OSError:
            webbrowser.open(target)

    # ---------- Config envoi auto des rapports de crash ----------
    def _ask_secret(self, title, prompt):
        """Petite boite de dialogue avec saisie masquee (mot de passe)."""
        top = tk.Toplevel(self.root)
        top.title(title)
        top.configure(bg=PANEL)
        top.transient(self.root)
        top.grab_set()
        ttk.Label(top, text=prompt, style="Panel.TLabel").pack(padx=14, pady=(14, 6))
        var = tk.StringVar()
        ent = ttk.Entry(top, textvariable=var, show="*", width=34)
        ent.pack(padx=14)
        ent.focus_set()
        result = {"val": None}

        def ok():
            result["val"] = var.get().strip()
            top.destroy()

        btns = ttk.Frame(top, style="Panel.TFrame")
        btns.pack(pady=12)
        ttk.Button(btns, text="OK", command=ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Annuler", command=top.destroy).pack(side="left", padx=4)
        ent.bind("<Return>", lambda e: ok())
        self.root.wait_window(top)
        return result["val"]

    def _config_mail(self):
        configured = " (deja configure)" if mailer.is_configured() else ""
        if not self._confirm(
                "Envoi automatique des rapports" + configured,
                "Pour envoyer les rapports d'erreur automatiquement, un compte Gmail et un "
                "« mot de passe d'application » (16 caracteres) sont necessaires.\n\n"
                "Il se cree sur https://myaccount.google.com/apppasswords (2FA requise).",
                ok="Configurer", cancel="Annuler"):
            return
        address = simpledialog.askstring(
            "Adresse Gmail", "Adresse Gmail qui ENVOIE les rapports :",
            initialvalue=crash_reporter.DEV_EMAIL, parent=self.root)
        if not address:
            return
        pwd = self._ask_secret("Mot de passe d'application",
                               "Mot de passe d'application Gmail (16 car.) :")
        if not pwd:
            return
        mailer.save_creds(address.strip(), pwd)
        if self._confirm("Identifiants enregistres",
                         "Envoyer un e-mail de test pour verifier la configuration ?",
                         ok="Envoyer le test", cancel="Plus tard"):
            self._test_mail()

    def _test_mail(self):
        def work():
            try:
                mailer.send(crash_reporter.DEV_EMAIL, "MZ Launcher - test",
                            "Ceci est un mail de test : l'envoi auto des rapports fonctionne.")
                self.ui(lambda: self._info("Test reussi", "L'e-mail de test a bien ete envoye."))
            except Exception as e:
                self.ui(lambda: self._error(
                    "Echec de l'envoi",
                    f"L'envoi a echoue :\n{e}\n\n"
                    "Verifiez l'adresse et le mot de passe d'application."))
        threading.Thread(target=work, daemon=True).start()


def _enable_hidpi():
    """Rend l'appli nette sur les ecrans HiDPI (sinon Windows l'etire -> flou)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # System DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main():
    _enable_hidpi()
    root = tk.Tk()
    # Adapte la densite de Tk au DPI reel (textes nets, tailles correctes).
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        pass
    app = App(root)

    # Rapport de crash : envoi auto (SMTP) si configure, sinon ouverture du client mail.
    def on_report(path, mode):
        try:
            if mode == "auto":
                detail = "Le rapport a ete ENVOYE automatiquement au dev."
            else:
                detail = ("Ton client mail s'est ouvert : clique sur Envoyer.\n"
                          "(Active l'envoi auto via le bouton 'Rapports auto' dans l'onglet Serveur.)")
            messagebox.showerror(
                "Plantage",
                f"Une erreur est survenue.\n\n{detail}\n\nFichier du rapport :\n{path}")
        except tk.TclError:
            pass

    handler = crash_reporter.install(app.crash_context, APP_VERSION, on_report)
    root.report_callback_exception = lambda et, e, tb: handler(et, e, tb)

    def on_close():
        if app.server and app.server.is_running():
            if not app._confirm("Quitter l'application",
                                 "Le serveur est en cours d'execution. L'arreter et quitter ?",
                                 ok="Arreter et quitter", cancel="Annuler"):
                return
            app.server.kill()
        if app.playit_agent and app.playit_agent.is_running():
            app._user_stopped_playit = True  # evite la popup d'erreur a la fermeture
            app.playit_agent.stop()
        app._save_config()  # memorise le profil pour la prochaine fois
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
