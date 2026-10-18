"""EZ Minecraft Server Launcher.

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
# Displayed label -> actual value in server.properties (modern 1.19+ format)
LEVEL_TYPES = {
    "Normal": "minecraft:normal",
    "Flat (superflat)": "minecraft:flat",
    "Large biomes": "minecraft:large_biomes",
    "Amplified": "minecraft:amplified",
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
        root.title("EZ Minecraft Server Launcher")
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
        ttk.Label(header, text="EZ", style="Accent.TLabel").pack(side="left")
        ttk.Label(header, text=" Minecraft Server", style="Title.TLabel").pack(side="left")
        self.head_status = ttk.Label(header, text="", style="Muted.TLabel")
        self.head_status.pack(side="right")
        self.players_lbl = ttk.Label(header, text="0 player", style="Muted.TLabel")
        self.players_lbl.pack(side="right", padx=14)
        self.state_dot = ttk.Label(header, text="● stopped", foreground=DANGER, background=BG)
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
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self.root, textvariable=self.status_var, relief="flat",
                  background=PANEL2, foreground=MUTED,
                  anchor="w", padding=7).pack(fill="x", side="bottom")

        cons_frame = ttk.Frame(self.root, padding=(14, 6))
        cons_frame.pack(fill="x", side="bottom")
        ttk.Label(cons_frame, text="Server Console", style="Muted.TLabel").pack(anchor="w")
        self.console = scrolledtext.ScrolledText(
            cons_frame, height=7, bg=CONSOLE_BG, fg=CONSOLE_FG,
            insertbackground=CONSOLE_FG, font=("Consolas", 9), state="disabled",
            relief="flat", borderwidth=0)
        self.console.pack(fill="x", pady=(4, 2))

        self.nb.pack(fill="both", expand=True, padx=14, pady=(2, 6))

    def _tab_server(self):
        outer = ttk.Frame(self.nb, style="Panel.TFrame")
        self.nb.add(outer, text="  Server  ")
        tab = self._scroll_area(outer)

        # ----- Guide banner: clearly says what to do NOW -----
        self.guide_lbl = tk.Label(
            tab, text="", bg="#11261E", fg="#CFF3E2",
            font=("Segoe UI Semibold", 11), justify="left", anchor="w",
            padx=16, pady=12, wraplength=920, highlightthickness=1,
            highlightbackground=ACCENT_DARK, highlightcolor=ACCENT_DARK)
        self.guide_lbl.pack(fill="x", pady=(0, 12))

        # ===== STEP 1: choose the server =====
        s1 = ttk.LabelFrame(tab, style="Step.TLabelframe", padding=12)
        s1.pack(fill="x")
        self._step1 = (s1, "Choose your server")

        row = ttk.Frame(s1, style="Panel.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="Type:", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.loader_var = tk.StringVar(value="paper")
        cb = ttk.Combobox(row, textvariable=self.loader_var, values=LOADERS,
                          state="readonly", width=12)
        cb.grid(row=0, column=1, padx=5)
        cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_versions())

        ttk.Label(row, text="Version:", style="Panel.TLabel").grid(row=0, column=2, padx=(15, 0))
        self.version_var = tk.StringVar()
        self.version_cb = ttk.Combobox(row, textvariable=self.version_var,
                                       state="readonly", width=18)
        self.version_cb.grid(row=0, column=3, padx=5)

        self.snapshot_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Snapshots (vanilla)", variable=self.snapshot_var,
                        command=self._refresh_versions).grid(row=0, column=4, padx=10)

        ttk.Label(row, text="RAM (MB):", style="Panel.TLabel").grid(row=1, column=0, sticky="w",
                                                                     pady=(10, 0))
        self.ram_var = tk.StringVar(value=str(DEFAULT_RAM))
        ttk.Entry(row, textvariable=self.ram_var, width=10).grid(row=1, column=1, pady=(10, 0))
        ttk.Label(row, text="Port:", style="Panel.TLabel").grid(row=1, column=2, padx=(15, 0),
                                                                 pady=(10, 0))
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        ttk.Entry(row, textvariable=self.port_var, width=10).grid(row=1, column=3, pady=(10, 0))

        total = sysinfo.total_ram_mb()
        ttk.Label(row, style="Hint.TLabel",
                  text=f"PC RAM: {total} MB  -  recommended max: {sysinfo.max_safe_ram_mb()} MB"
                  ).grid(row=1, column=4, padx=10, pady=(10, 0), sticky="w")

        opts = ttk.Frame(s1, style="Panel.TFrame")
        opts.pack(fill="x", pady=(10, 0))
        self.eula_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="I accept the Minecraft EULA (required) - aka.ms/MinecraftEULA",
                        variable=self.eula_var).pack(side="left")
        self.autorestart_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Auto-restart on crash",
                        variable=self.autorestart_var).pack(side="left", padx=20)

        # Resume an already installed world (still in step 1)
        existing = ttk.Frame(s1, style="Panel.TFrame")
        existing.pack(fill="x", pady=(10, 0))
        ttk.Label(existing, text="Or continue an already installed server:",
                  style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.existing_var = tk.StringVar()
        self.existing_cb = ttk.Combobox(existing, textvariable=self.existing_var,
                                        state="readonly", width=28)
        self.existing_cb.grid(row=0, column=1, padx=(0, 8))
        ttk.Button(existing, text="Load", style="Accent.TButton",
                   command=self._load_existing).grid(row=0, column=2, padx=2)
        ttk.Button(existing, text="Refresh",
                   command=self._refresh_existing).grid(row=0, column=3, padx=2)
        ttk.Button(existing, text="Delete", style="Danger.TButton",
                   command=self._delete_existing).grid(row=0, column=4, padx=2)

        # ===== STEP 2: install and start =====
        s2 = ttk.LabelFrame(tab, style="Step.TLabelframe", padding=12)
        s2.pack(fill="x", pady=(12, 0))
        self._step2 = (s2, "Install and start")
        actions = ttk.Frame(s2, style="Panel.TFrame")
        actions.pack(fill="x")
        self.prepare_btn = ttk.Button(actions, text="1) Install / Prepare",
                                      command=self._on_prepare)
        self.prepare_btn.pack(side="left")
        self.start_btn = ttk.Button(actions, text="2) Start", style="Accent.TButton",
                                    command=self._on_start, state="disabled")
        self.start_btn.pack(side="left", padx=6)
        self.stop_btn = ttk.Button(actions, text="Stop", style="Danger.TButton",
                                   command=self._on_stop, state="disabled")
        self.stop_btn.pack(side="left")
        ttk.Button(actions, text="Free port", style="Ghost.TButton",
                   command=self._free_port).pack(side="left", padx=6)
        # Secondary utilities, discreet
        ttk.Button(actions, text="Open folder", style="Ghost.TButton",
                   command=self._open_folder).pack(side="right")
        ttk.Button(actions, text="Error reports", style="Ghost.TButton",
                   command=self._config_mail).pack(side="right", padx=6)

        # ===== STEP 3: invite players =====
        s3 = ttk.LabelFrame(tab, style="Step.TLabelframe", padding=12)
        s3.pack(fill="x", pady=(12, 0))
        self._step3 = (s3, "Invite players")

        # Case A: same local network (simplest)
        a = ttk.Frame(s3, style="Panel.TFrame")
        a.pack(fill="x")
        ttk.Label(a, text="Players on the same network (Wi‑Fi / LAN)  -  address to share:",
                  style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.lan_lbl = ttk.Label(a, text="...", font=("Consolas", 10))
        self.lan_lbl.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(a, text="Copy", width=8,
                   command=lambda: self._copy(self._addr(self._lan_ip))).grid(row=0, column=2)

        ttk.Separator(s3, orient="horizontal").pack(fill="x", pady=10)

        # Case B: remote (Internet) -> tunnel (recommended)
        ttk.Label(s3, text="Players over the Internet  -  recommended: tunnel "
                           "(no router setup required):", style="Panel.TLabel").pack(anchor="w")
        bt = ttk.Frame(s3, style="Panel.TFrame")
        bt.pack(fill="x", pady=(6, 0))
        ttk.Button(bt, text="Enable remote access (one-time setup)",
                   command=self._config_playit).pack(side="left")
        self.playit_btn = ttk.Button(bt, text="Start tunnel",
                                     command=self._toggle_playit)
        self.playit_btn.pack(side="left", padx=6)
        self.playit_addr = ttk.Label(bt, text=self._playit_address or "(not active)",
                                     font=("Consolas", 10))
        self.playit_addr.pack(side="left", padx=10)
        ttk.Button(bt, text="Copy address", width=14,
                   command=self._copy_playit).pack(side="left")

        # Advanced alternative: port forwarding on the router
        adv = ttk.Frame(s3, style="Panel.TFrame")
        adv.pack(fill="x", pady=(10, 0))
        ttk.Label(adv, text="Advanced (without tunnel, port forwarding):",
                  style="Hint.TLabel").pack(side="left")
        self.pub_lbl = ttk.Label(adv, text="...", font=("Consolas", 9))
        self.pub_lbl.pack(side="left", padx=8)
        ttk.Button(adv, text="Copy", width=8,
                   command=lambda: self._copy(self._addr(self._public_ip))).pack(side="left")
        ttk.Button(adv, text="Help (port forwarding)",
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
            txt = ("Follow the steps in order:\n"
                   "Step 1  -  choose the Type and Version, set RAM, accept the EULA "
                   "(or continue an already installed server).\n"
                   "Step 2  -  click \"1) Install / Prepare\" (automatic download).")
        elif not running and self.prepared:
            txt = ("Server ready.  ->  Step 2: click the green \"2) Start\" button "
                   "to launch the server.")
        elif running and tunnel and self._playit_address:
            txt = ("Server running, tunnel active.  ->  Step 3: share these addresses with players:\n"
                   f"    - same network (LAN)  :  {lan}\n"
                   f"    - remote (Internet)  :  {self._playit_address}")
        elif running and self._playit_address:
            txt = ("Server running.  ->  Step 3: same network = address "
                   f"{lan}.  For remote access, click \"Start tunnel\".")
        else:  # running, tunnel never configured
            txt = ("Server running.  ->  Step 3: players on the same network connect to "
                   f"{lan}.\n"
                   "For remote access (Internet), click \"Enable remote access\".")
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
        tk.Label(wrap, text="EZ", bg=BG, fg=ACCENT,
                 font=("Segoe UI Semibold", 24)).pack(side="top", anchor="w")
        tk.Label(wrap, text="Minecraft Server", bg=BG, fg=FG,
                 font=("Segoe UI Semibold", 24)).pack(anchor="w")
        tk.Label(wrap, text="Host your server with friends in just a few clicks.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 11)).pack(anchor="w", pady=(4, 26))
        self.wiz_body = wrap
        self._card(wrap, "✨", "Simple setup   (recommended)",
                   "Answer a few questions and we will create and configure the server for you.",
                   self._start_wizard)
        self._card(wrap, "⚙", "Advanced mode",
                   "Direct access to the full interface (all settings).",
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
        ttk.Button(foot, text="← Back", style="Ghost.TButton",
                   command=self._wiz_back).pack(side="left")
        ttk.Button(foot, text="Exit wizard", style="Ghost.TButton",
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
        self._wiz_title("Welcome \U0001F44B", "What do you want to do?")
        self._card(self.wiz_body, "▶", "Create a new server",
                   "A few questions and you're ready.", lambda: self._wiz_go(self._wiz_q_type))
        self._card(self.wiz_body, "↻", "Resume a game",
                   "Restart an already installed server.", lambda: self._wiz_go(self._wiz_q_existing))

    def _wiz_q_existing(self):
        self._wiz_title("Resume a game", "Choose the server to relaunch.")
        servers = paths.list_servers()
        if not servers:
            tk.Label(self.wiz_body, text="No servers installed yet.",
                     bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 8))
            self._card(self.wiz_body, "▶", "Create a new server", "",
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
        self._wiz_title("What type of server?", "Choose based on what you want to do.")
        self._card(self.wiz_body, "▢", "Vanilla", "The original Minecraft, without add-ons.",
                   lambda: self._wiz_pick("loader", "vanilla", self._wiz_q_version))
        self._card(self.wiz_body, "✦", "Paper   (recommended)",
                   "Optimized and smooth, supports plugins.",
                   lambda: self._wiz_pick("loader", "paper", self._wiz_q_version))
        self._card(self.wiz_body, "⚙", "Modded (Fabric / Forge)",
                   "Real mods: modpacks, machines, etc.",
                   lambda: self._wiz_go(self._wiz_q_loader_mods))

    def _wiz_q_loader_mods(self):
        self._wiz_title("Which mod loader?", "Fabric is lighter and more modern.")
        self._card(self.wiz_body, "◈", "Fabric", "Lightweight, modern mods, quick updates.",
                   lambda: self._wiz_pick("loader", "fabric", self._wiz_q_version))
        self._card(self.wiz_body, "◉", "Forge", "Classic, large mods and big modpacks.",
                   lambda: self._wiz_pick("loader", "forge", self._wiz_q_version))

    def _wiz_q_version(self):
        self._wiz_title("Which Minecraft version?", "The latest version is recommended.")
        loading = tk.Label(self.wiz_body, text="Loading versions...",
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
            tk.Label(self.wiz_body, text="Unable to load versions (network issue?).",
                     bg=BG, fg=DANGER, font=("Segoe UI", 10)).pack(anchor="w")
            return
        latest = versions[0]
        self._card(self.wiz_body, "✦", f"Latest version   ({latest})",
                   "Recommended choice.",
                   lambda: self._wiz_pick("version", latest, self._wiz_q_world))
        box = tk.Frame(self.wiz_body, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        box.pack(fill="x", pady=5)
        inner = tk.Frame(box, bg=PANEL)
        inner.pack(fill="x", padx=16, pady=13)
        tk.Label(inner, text="Or choose:", bg=PANEL, fg=FG,
                 font=("Segoe UI", 10)).pack(side="left")
        var = tk.StringVar(value=latest)
        ttk.Combobox(inner, textvariable=var, values=versions, state="readonly",
                     width=18).pack(side="left", padx=10)
        ttk.Button(inner, text="Continue", style="Accent.TButton",
                   command=lambda: self._wiz_pick("version", var.get(),
                                                  self._wiz_q_world)).pack(side="left")

    def _wiz_q_world(self):
        self._wiz_title("What world?", "Random or a specific seed?")
        self._card(self.wiz_body, "\U0001F3B2", "Random world", "A map at random, the surprise.",
                   lambda: self._wiz_pick("seed", "", self._wiz_q_gamemode))
        box = tk.Frame(self.wiz_body, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        box.pack(fill="x", pady=5)
        inner = tk.Frame(box, bg=PANEL)
        inner.pack(fill="x", padx=16, pady=13)
        tk.Label(inner, text="I have a seed:", bg=PANEL, fg=FG,
                 font=("Segoe UI", 10)).pack(side="left")
        var = tk.StringVar()
        ttk.Entry(inner, textvariable=var, width=20).pack(side="left", padx=10)
        ttk.Button(inner, text="Continue", style="Accent.TButton",
                   command=lambda: self._wiz_pick("seed", var.get().strip(),
                                                  self._wiz_q_gamemode)).pack(side="left")

    def _wiz_q_gamemode(self):
        self._wiz_title("Game mode?", "")
        self._card(self.wiz_body, "⚔", "Survival", "Health, hunger, monsters.",
                   lambda: self._wiz_pick("gamemode", "survival", self._wiz_q_remote))
        self._card(self.wiz_body, "✨", "Creative", "Unlimited resources, flying, free building.",
                   lambda: self._wiz_pick("gamemode", "creative", self._wiz_q_remote))

    def _wiz_q_remote(self):
        self._wiz_title("Who are you playing with?", "")
        self._card(self.wiz_body, "\U0001F3E0", "On my network (same Wi‑Fi)",
                   "The easiest option: everyone is at home or on the same network.",
                   lambda: self._wiz_pick("remote", False, self._wiz_summary))
        self._card(self.wiz_body, "\U0001F310", "With friends over the Internet",
                   "We will set up a secure remote access tunnel without changing your router.",
                   lambda: self._wiz_pick("remote", True, self._wiz_summary))

    def _wiz_summary(self):
        w = self._wiz
        self._wiz_title("Summary", "Check it, then start the setup.")
        card = tk.Frame(self.wiz_body, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="x", pady=(0, 6))
        rows = [
            ("Type", w.get("loader", "-")),
            ("Version", w.get("version", "-")),
            ("World", f"seed « {w['seed']} »" if w.get("seed") else "random"),
            ("Game mode", w.get("gamemode", "survival")),
            ("Players", "over the Internet" if w.get("remote") else "same network (LAN)"),
            ("RAM", f"{DEFAULT_RAM} MB (auto)"),
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
        ttk.Checkbutton(foot, text="I accept the Minecraft EULA (required) - aka.ms/MinecraftEULA",
                        variable=self._wiz_eula).pack(anchor="w", padx=14, pady=10)
        ttk.Button(self.wiz_body, text="Create server   →", style="Accent.TButton",
                   command=self._wiz_finish).pack(anchor="w", pady=(16, 0))

    def _wiz_finish(self):
        if not self._wiz_eula.get():
            self._warn("License agreement",
                       "You must accept the Minecraft EULA to continue.")
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

    def _confirm(self, title, msg, ok="Confirm", cancel="Cancel"):
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
            tab, "Purpose: configure the world (game mode, difficulty, seed, MOTD, view distance...).\n"
                 "When: before starting the server. Click \"Apply now\" to save changes.")

        g = ttk.Frame(tab, style="Panel.TFrame")
        g.pack(fill="x")

        def lbl(t, r, c):
            ttk.Label(g, text=t, style="Panel.TLabel").grid(row=r, column=c, sticky="w",
                                                            padx=(0, 6), pady=4)

        lbl("Seed (empty = random):", 0, 0)
        self.seed_var = tk.StringVar()
        ttk.Entry(g, textvariable=self.seed_var, width=24).grid(row=0, column=1, sticky="w", pady=4)

        lbl("MOTD:", 0, 2)
        self.motd_var = tk.StringVar(value="My Minecraft Server")
        ttk.Entry(g, textvariable=self.motd_var, width=26).grid(row=0, column=3, sticky="w", pady=4)

        lbl("Game mode:", 1, 0)
        self.gamemode_var = tk.StringVar(value="survival")
        ttk.Combobox(g, textvariable=self.gamemode_var, values=GAMEMODES,
                     state="readonly", width=14).grid(row=1, column=1, sticky="w", pady=4)

        lbl("Difficulty:", 1, 2)
        self.difficulty_var = tk.StringVar(value="normal")
        ttk.Combobox(g, textvariable=self.difficulty_var, values=DIFFICULTIES,
                     state="readonly", width=14).grid(row=1, column=3, sticky="w", pady=4)

        lbl("World type:", 2, 0)
        self.leveltype_var = tk.StringVar(value="Normal")
        ttk.Combobox(g, textvariable=self.leveltype_var, values=list(LEVEL_TYPES),
                     state="readonly", width=18).grid(row=2, column=1, sticky="w", pady=4)

        lbl("Max players:", 2, 2)
        self.maxplayers_var = tk.StringVar(value="10")
        ttk.Entry(g, textvariable=self.maxplayers_var, width=8).grid(row=2, column=3, sticky="w",
                                                                     pady=4)

        lbl("View distance:", 3, 0)
        self.viewdist_var = tk.StringVar(value="10")
        ttk.Spinbox(g, from_=3, to=32, textvariable=self.viewdist_var, width=6).grid(
            row=3, column=1, sticky="w", pady=4)

        lbl("Simulation distance:", 3, 2)
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
            ("Monsters", self.monsters_var),
            ("Nether", self.nether_var),
            ("Flight allowed", self.flight_var),
            ("Command blocks", self.cmdblock_var),
            ("Online mode (premium)", self.online_var),
            ("Whitelist", self.whitelist_var),
        ]
        for i, (t, v) in enumerate(cks):
            ttk.Checkbutton(checks, text=t, variable=v).grid(row=i // 4, column=i % 4,
                                                             sticky="w", padx=6, pady=4)

        ttk.Label(tab, style="Hint.TLabel",
                  text="Tip: 'Flat' = superflat. Skyblock/Bedwars require a datapack or a plugin (Paper). "
                       "The config is applied at preparation/start."
                  ).pack(anchor="w", pady=(12, 0))
        ttk.Button(tab, text="Apply config now",
                   command=self._apply_config_now).pack(anchor="w", pady=(8, 0))

    def _tab_mods(self):
        outer = ttk.Frame(self.nb, style="Panel.TFrame")
        self.nb.add(outer, text="  Mods / Plugins  ")
        tab = self._scroll_area(outer)
        self._hint_banner(
            tab, "Purpose: add mods / plugins (built-in Modrinth search).\n"
                 "When: after preparing a Paper or Fabric server. Vanilla: no mods.")

        self.mods_info = ttk.Label(
            tab, style="Panel.TLabel",
            text="Prepare a server first. Paper -> plugins, Fabric -> mods, Vanilla -> none.")
        self.mods_info.pack(anchor="w")

        self.mods_list = tk.Listbox(tab, height=12, bg=PANEL, fg=FG,
                                    selectbackground=ACCENT, relief="flat",
                                    font=("Consolas", 9))
        self.mods_list.pack(fill="both", expand=True, pady=8)

        bar = ttk.Frame(tab, style="Panel.TFrame")
        bar.pack(fill="x")
        ttk.Button(bar, text="Add .jar files", command=self._mods_add).pack(side="left")
        ttk.Button(bar, text="Remove selected", style="Danger.TButton",
                   command=self._mods_remove).pack(side="left", padx=6)
        ttk.Button(bar, text="Refresh", command=self._mods_refresh).pack(side="left")
        ttk.Button(bar, text="Open folder", command=self._mods_open).pack(side="left", padx=6)

        # Modrinth search
        search = ttk.LabelFrame(tab, text="Search Modrinth (direct download)", padding=8)
        search.pack(fill="x", pady=(10, 0))
        self.modr_query = tk.StringVar()
        e = ttk.Entry(search, textvariable=self.modr_query)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self._modr_search())
        ttk.Button(search, text="Search", command=self._modr_search).pack(side="left", padx=6)
        ttk.Button(search, text="Install selected", style="Accent.TButton",
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
            tab, "Purpose: send commands to the in-game server (op, kick, give an item, "
                 "set time or weather, announcements...).\n"
                 "When: only while the server is running.")

        pl = ttk.Frame(tab, style="Panel.TFrame")
        pl.pack(fill="x", pady=(10, 0))
        ttk.Label(pl, text="Player:", style="Panel.TLabel").pack(side="left")
        self.admin_player = tk.StringVar()
        ttk.Entry(pl, textvariable=self.admin_player, width=20).pack(side="left", padx=6)

        pbtns = ttk.Frame(tab, style="Panel.TFrame")
        pbtns.pack(fill="x", pady=8)
        player_actions = [
            ("OP", "op {p}"), ("De-OP", "deop {p}"),
            ("Kick", "kick {p}"), ("Ban", "ban {p}"),
            ("Whitelist +", "whitelist add {p}"), ("Whitelist -", "whitelist remove {p}"),
            ("TP to spawn", "spawnpoint {p}"),
        ]
        for i, (label, tmpl) in enumerate(player_actions):
            ttk.Button(pbtns, text=label, command=lambda t=tmpl: self._admin_player(t)).grid(
                row=i // 4, column=i % 4, sticky="w", padx=4, pady=4)

        gm = ttk.LabelFrame(tab, text="Player game mode", padding=8)
        gm.pack(fill="x", pady=6)
        for i, mode in enumerate(GAMEMODES):
            ttk.Button(gm, text=mode.capitalize(),
                       command=lambda m=mode: self._admin_player(f"gamemode {m} {{p}}")).grid(
                row=0, column=i, padx=4)

        world = ttk.LabelFrame(tab, text="World / session", padding=8)
        world.pack(fill="x", pady=6)
        world_actions = [
            ("Day", "time set day"), ("Night", "time set night"),
            ("Clear weather", "weather clear"), ("Rain", "weather rain"),
            ("Save", "save-all"), ("Player list", "list"),
            ("Easy difficulty", "difficulty easy"), ("Hard difficulty", "difficulty hard"),
        ]
        for i, (label, cmd) in enumerate(world_actions):
            ttk.Button(world, text=label, command=lambda c=cmd: self._admin_cmd(c)).grid(
                row=i // 4, column=i % 4, sticky="w", padx=4, pady=4)

        backup = ttk.LabelFrame(tab, text="World backups", padding=8)
        backup.pack(fill="x", pady=6)
        ttk.Button(backup, text="Backup world (.zip)",
                   command=self._backup_world).pack(side="left")
        ttk.Button(backup, text="Open backup folder",
                   command=self._open_backups).pack(side="left", padx=6)

        say = ttk.Frame(tab, style="Panel.TFrame")
        say.pack(fill="x", pady=(6, 0))
        ttk.Label(say, text="Message:", style="Panel.TLabel").pack(side="left")
        self.say_var = tk.StringVar()
        e = ttk.Entry(say, textvariable=self.say_var)
        e.pack(side="left", fill="x", expand=True, padx=6)
        e.bind("<Return>", lambda ev: self._admin_say())
        ttk.Button(say, text="Announce (say)", command=self._admin_say).pack(side="left")

        # Raw command line (moved from below the console). For experienced users.
        cmdf = ttk.LabelFrame(tab, text="Server command (advanced)", padding=8)
        cmdf.pack(fill="x", pady=(10, 0))
        self.cmd_var = tk.StringVar()
        ce = ttk.Entry(cmdf, textvariable=self.cmd_var)
        ce.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ce.bind("<Return>", lambda ev: self._send_command())
        ttk.Button(cmdf, text="Send", style="Accent.TButton",
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
        label = f"{n} player" + ("s" if n > 1 else "")
        self.ui(lambda: self.players_lbl.configure(text=label))

    def set_status(self, text):
        self.ui(lambda: self.status_var.set(text))

    def _copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status(f"Copied to clipboard: {text}")

    def _addr(self, ip):
        return f"{ip}:{self.port_var.get()}"

    # ---------- Versions ----------
    def _refresh_versions(self):
        loader = self.loader_var.get()
        self.set_status(f"Loading {loader} versions...")

        def work():
            try:
                versions = downloaders.list_versions(loader, self.snapshot_var.get())
                self.ui(lambda: self._set_versions(versions))
                self.set_status(f"{len(versions)} {loader} versions available.")
            except Exception as e:
                self.set_status(f"Version list error: {e}")

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
                       "Please prepare a server first (Server tab).")
            return
        try:
            update_properties(self.current_dir, **self._collect_properties())
            self.set_status("Config written to server.properties.")
            self.log("Config applied. (Restart the server for it to take effect.)")
        except ValueError as e:
            self._warn("Config", f"Invalid value: {e}")

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
            self.log(f"Unexpected server name: {name}")
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
                self.log(f"=== Loading {name} (Java {feature}) ===")
                java_exe = java_manager.ensure_java(
                    feature, log=self.log,
                    progress=lambda d, t: self.set_status(f"Java: {d*100//t}%"))
                self.java_exe = java_exe
                self.current_dir = sdir
                self.prepared = True
                self.log("=== Server loaded, ready to start ===")
                self.set_status(f"Server loaded: {name}")
                self.ui(lambda: self.start_btn.configure(state="normal"))
                self.ui(self._mods_refresh)
                self.ui(self._update_guide)
            except Exception as e:
                self.log(f"LOAD ERROR: {e}")
                self.set_status(f"Error: {e}")
            finally:
                self.ui(lambda: self.prepare_btn.configure(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    def _delete_existing(self):
        name = self.existing_var.get()
        if not name:
            return
        if self.server and self.server.is_running():
            self._warn("Action unavailable",
                       "Please stop the server before deleting it.")
            return
        if not self._confirm(
                "Delete server",
                f"Permanently delete \"{name}\" (world, config, mods)?\n\n"
                "This action is irreversible. Consider backing up the world first.",
                ok="Delete", cancel="Cancel"):
            return
        sdir = os.path.join(paths.SERVERS_DIR, name)
        try:
            shutil.rmtree(sdir)
            self.log(f"Server deleted: {name}")
            if self.current_dir == sdir:
                self.current_dir = None
                self.prepared = False
                self.start_btn.configure(state="disabled")
            self.existing_var.set("")
            self._refresh_existing()
        except OSError as e:
            self._error("Deletion failed", f"Deletion failed:\n{e}")

    def _on_prepare(self):
        if not self.eula_var.get():
            self._warn(
                "License agreement",
                "You must accept the Minecraft EULA to host a server.")
            return
        loader = self.loader_var.get()
        version = self.version_var.get()
        if not version:
            self._warn("Missing version", "Please select a version.")
            return

        self.prepare_btn.configure(state="disabled")
        self.start_btn.configure(state="disabled")
        self.prepared = False

        def work():
            try:
                feature = required_java(version)
                self.log(f"=== Preparing {loader} {version} (Java {feature}) ===")
                java_exe = java_manager.ensure_java(
                    feature, log=self.log,
                    progress=lambda d, t: self.set_status(f"Java: {d*100//t}%"))

                sdir = paths.server_dir(loader, version)
                downloaders.prepare(
                    loader, version, sdir, java_exe, log=self.log,
                    progress=lambda d, t: self.set_status(f"Download: {d*100//t}%"))

                accept_eula(sdir)
                update_properties(sdir, **self._collect_properties())

                self.java_exe = java_exe
                self.current_dir = sdir
                self.prepared = True
                self.log("=== Ready to start ===")
                self.set_status("Preparation complete.")
                self.ui(lambda: self.start_btn.configure(state="normal"))
                self.ui(self._mods_refresh)
                self.ui(self._refresh_existing)
                self.ui(self._update_guide)
                if getattr(self, "_pending_remote", False):
                    self._pending_remote = False
                    self.ui(lambda: self._info(
                        "Play remotely",
                        "The server is ready.\n\nTo play with friends over the Internet:\n"
                        "  1. start the server (Step 2);\n"
                        "  2. Server tab, Step 3, \"Enable remote access\".\n\n"
                        "For same-network play, no extra action is required."))
            except Exception as e:
                self.log(f"ERROR: {e}")
                self.set_status(f"Error: {e}")
            finally:
                self.ui(lambda: self.prepare_btn.configure(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    # ---------- Start / Stop ----------
    def _on_start(self):
        if not self.prepared:
            return
        try:
            ram = int(self.ram_var.get())
        except ValueError:
            self._warn("Invalid RAM", "Please enter a valid amount of MB.")
            return
        safe = sysinfo.max_safe_ram_mb()
        if ram > safe and not self._confirm(
                "High RAM usage",
                f"{ram} MB exceeds the recommended maximum ({safe} MB).\nStart anyway?",
                ok="Start", cancel="Cancel"):
            return
        try:
            update_properties(self.current_dir, **self._collect_properties())
        except ValueError as e:
            self._warn("Invalid value", f"Invalid setting: {e}")
            return

        port = self.port_var.get()
        if netinfo.port_in_use(port):
            if not self._confirm(
                    "Port already in use",
                    f"Port {port} is already in use: a server is probably running already "
                    "(or a previous launch did not stop cleanly).\n\n"
                    "Free the port and start anyway?",
                    ok="Free and start", cancel="Cancel"):
                return
            self.log(f"Freeing port {port}...")
            netinfo.free_port(port)
            time.sleep(1)
            if netinfo.port_in_use(port):
                self._warn(
                    "Port still in use",
                    f"Port {port} is still in use.\n\n"
                    "Close the program using it, or choose another port (Step 1).")
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
        self.state_dot.configure(text="● running", foreground="#7fd35a")
        self.set_status(f"Server started on port {self.port_var.get()}.")
        self.log(f">>> Starting ({ram} MB RAM) <<<")
        self._update_guide()

    def _on_server_exit(self, code):
        self.log(f">>> Server stopped (code {code}) <<<")
        self.set_status("Server stopped.")
        self._players.clear()
        self._update_players()
        self.ui(lambda: self.state_dot.configure(text="● stopped", foreground="#c8553d"))
        self.ui(lambda: self.stop_btn.configure(state="disabled"))
        self.ui(lambda: self.start_btn.configure(state="normal"))
        self.ui(lambda: self.prepare_btn.configure(state="normal"))
        self.ui(self._update_guide)
        if self.autorestart_var.get() and code != 0 and not self._user_stopped:
            self.log(">>> Crash detected: automatic restart in 5s... <<<")
            self.ui(lambda: self.root.after(5000, self._auto_restart))

    def _auto_restart(self):
        if not (self.server and self.server.is_running()):
            self._on_start()

    def _on_stop(self):
        if self.server and self.server.is_running():
            self._user_stopped = True
            self.log(">>> Stopping (saving the world)... <<<")
            self.server.stop()

    def _free_port(self):
        """Free the server port: clean shutdown, then kill the remaining process if needed."""
        port = self.port_var.get()
        if not netinfo.port_in_use(port):
            self._info("Port free", f"Port {port} is already free. No action needed.")
            return
        if not self._confirm(
                "Free port",
                f"Port {port} is in use. This will STOP the program currently using it "
                "(your running server or another process still open).\n\n"
                "Warning: an unsaved server may lose the last few minutes of gameplay.\n\n"
                "Continue?",
                ok="Free port", cancel="Cancel"):
            return

        def work():
            if self.server and self.server.is_running():
                self._user_stopped = True
                self.log(">>> Stopping the server to free the port... <<<")
                self.server.stop()
                for _ in range(10):
                    if not (self.server and self.server.is_running()):
                        break
                    time.sleep(1)
            if netinfo.port_in_use(port):
                self.log(f"Force releasing port {port}...")
                netinfo.free_port(port)
                time.sleep(1)
            if netinfo.port_in_use(port):
                self.ui(lambda: self._warn(
                    "Port still in use",
                    f"Port {port} is still in use. Try again or restart your PC."))
            else:
                self.log(f"Port {port} released.")
                self.ui(lambda: self._info(
                    "Port released", f"Port {port} is now free. You can start the server."))
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
            self.set_status("The server must be running for this action.")
            return False
        return True

    def _admin_cmd(self, command):
        if self._server_running():
            self.server.send(command)
            self.log(f"> {command}")

    def _admin_player(self, template):
        p = self.admin_player.get().strip()
        if not p:
            self.set_status("Enter a player name first.")
            return
        self._admin_cmd(template.format(p=p))

    def _admin_say(self):
        msg = self.say_var.get().strip()
        if msg:
            self._admin_cmd(f"say {msg}")
            self.say_var.set("")

    # ---------- Backups ----------
    def _backups_dir(self):
        d = os.path.join(paths.DATA_DIR, "backups")
        os.makedirs(d, exist_ok=True)
        return d

    def _backup_world(self):
        if not self.current_dir:
            self._info("Backup", "Please prepare a server first.")
            return
        world = os.path.join(self.current_dir, "world")
        if not os.path.isdir(world):
            self._info("Backup", "There is no world to back up yet "
                                 "(start the server at least once).")
            return
        if self.server and self.server.is_running():
            self.server.send("save-all")
        import datetime
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{os.path.basename(self.current_dir)}_{stamp}"
        base = os.path.join(self._backups_dir(), name)

        def work():
            try:
                self.log(f"Backing up world ({name}.zip)...")
                shutil.make_archive(base, "zip", world)
                self.log("Backup complete.")
                self.set_status(f"World backed up: {name}.zip")
            except OSError as e:
                self.log(f"Backup error: {e}")

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
                text="Vanilla: no mods/plugins possible. Choose Paper or Fabric.")
            return
        os.makedirs(d, exist_ok=True)
        self.mods_info.configure(text=f"Folder: {d}")
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(".jar"):
                self.mods_list.insert("end", f)

    def _mods_add(self):
        d = self._mods_dir()
        if d is None:
            self._info("Mods / Plugins", "Please prepare a Paper or Fabric server first.")
            return
        files = filedialog.askopenfilenames(title="Choose .jar files",
                                            filetypes=[("Java archive", "*.jar")])
        os.makedirs(d, exist_ok=True)
        for src in files:
            try:
                shutil.copy(src, os.path.join(d, os.path.basename(src)))
                self.log(f"Mod added: {os.path.basename(src)}")
            except OSError as e:
                self.log(f"Copy error {src}: {e}")
        self._mods_refresh()

    def _mods_remove(self):
        d = self._mods_dir()
        if d is None:
            return
        for i in reversed(self.mods_list.curselection()):
            name = self.mods_list.get(i)
            try:
                os.remove(os.path.join(d, name))
                self.log(f"Mod removed: {name}")
            except OSError as e:
                self.log(f"Removal error {name}: {e}")
        self._mods_refresh()

    def _mods_open(self):
        d = self._mods_dir()
        if d is None:
            self._info("Mods / Plugins", "Please prepare a Paper or Fabric server first.")
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
        self.set_status("Searching Modrinth...")

        def work():
            try:
                results = modrinth.search(q, loader, version)
                self._modr_results = results
                self.ui(lambda: self._modr_show(results))
                self.set_status(f"{len(results)} Modrinth results.")
            except Exception as e:
                self.set_status(f"Modrinth error: {e}")

        threading.Thread(target=work, daemon=True).start()

    def _modr_show(self, results):
        self.modr_list.delete(0, "end")
        for r in results:
            self.modr_list.insert("end", f"{r['title']}  ({r['downloads']} dl)  - {r['slug']}")

    def _modr_install(self):
        d = self._mods_dir()
        if d is None:
            self._info("Modrinth", "Please prepare a Paper, Fabric, or Forge server first.")
            return
        sel = self.modr_list.curselection()
        if not sel:
            self.set_status("Select a mod from the list.")
            return
        proj = self._modr_results[sel[0]]
        loader = self.loader_var.get()
        version = self.version_var.get()
        os.makedirs(d, exist_ok=True)

        def work():
            try:
                self.log(f"Installing Modrinth: {proj['title']}...")
                name = modrinth.install(proj["slug"], loader, version, d, log=self.log)
                self.log(f"Installed: {name}")
                self.ui(self._mods_refresh)
            except Exception as e:
                self.log(f"Modrinth install error: {e}")
                self.set_status(f"Error: {e}")

        threading.Thread(target=work, daemon=True).start()

    # ---------- playit.gg ----------
    def _toggle_playit(self):
        if self.playit_agent and self.playit_agent.is_running():
            self._user_stopped_playit = True
            self.playit_agent.stop()
            self.playit_btn.configure(text="Start tunnel")
            self._update_guide()
            return

        if not playit.is_configured():
            self._info(
                "Remote access not configured",
                "Remote access is not set up yet.\n\n"
                "Click \"Enable remote access\" to configure it.")
            return

        secret = playit.load_config().get("secret", "")
        self._user_stopped_playit = False

        def work():
            try:
                playit.ensure_agent(log=self.log)
                self.log("Starting playit.gg tunnel...")
                self.playit_agent = playit.PlayitAgent(
                    on_output=self.log, on_exit=self._on_playit_exit)
                self.playit_agent.start(secret)
                self.ui(lambda: self.playit_btn.configure(text="Stop tunnel"))
                self.ui(self._update_guide)
                if self._playit_address:
                    self.log(f"Address to share with players: {self._playit_address}")
            except Exception as e:
                self.log(f"Playit error: {e}")
                self.set_status(f"Error: {e}")

        threading.Thread(target=work, daemon=True).start()

    def _on_playit_exit(self, code):
        self.log(f">>> playit stopped (code {code}) <<<")
        self.ui(lambda: self.playit_btn.configure(text="Start tunnel"))
        self.ui(self._update_guide)
        if code != 0 and not getattr(self, "_user_stopped_playit", False):
            self.ui(lambda: self._error(
                "Remote access interrupted",
                "The tunnel stopped unexpectedly.\n\n"
                "Most likely cause: the access key is invalid or expired.\n"
                "Run \"Enable remote access\" again to generate a new one."))

    def _config_playit(self):
        if not self._confirm(
                "Enable remote access",
                "The setup is fully automatic: it lets you play remotely without opening a port on your router.\n\n"
                "A playit.gg page will open:\n"
                "  1. sign in (or create an account and verify your e-mail);\n"
                "  2. click \"Allow\" to authorize the app.\n\n"
                "The app then retrieves the key and creates the tunnel automatically.",
                ok="Continue", cancel="Cancel"):
            return
        self._playit_cancel = False
        try:
            local_port = int(self.port_var.get())
        except (TypeError, ValueError):
            local_port = 25565

        def work():
            try:
                secret = playit.claim_interactive(
                    self.log, lambda: getattr(self, "_playit_cancel", False))
                playit.ensure_agent(log=self.log)
                if self.playit_agent and self.playit_agent.is_running():
                    self._user_stopped_playit = True
                    self.playit_agent.stop()
                    time.sleep(1)
                self._user_stopped_playit = False
                self.playit_agent = playit.PlayitAgent(
                    on_output=self.log, on_exit=self._on_playit_exit)
                self.playit_agent.start(secret)
                self.ui(lambda: self.playit_btn.configure(text="Stop tunnel"))
                self.log("Agent started, configuring Minecraft tunnel...")
                address = playit.ensure_tunnel(secret, local_port, self.log)
                playit.save_config(secret, address)
                self._playit_address = address
                self.ui(lambda: self.playit_addr.configure(text=address))
                self.ui(self._update_guide)
                self.ui(lambda: self._info(
                    "Remote access ready",
                    "Setup complete, the tunnel is active.\n\n"
                    f"Address to share with players (in Minecraft):\n\n    {address}\n\n"
                    "Start the server if it is not already running: the tunnel follows automatically."))
            except Exception as e:
                msg = str(e)
                self.log(f"playit setup failed: {msg}")
                self.ui(lambda: self._error(
                    "Remote access configuration",
                    f"Automatic configuration failed:\n\n{msg}"))

        threading.Thread(target=work, daemon=True).start()

    def _copy_playit(self):
        if self._playit_address:
            self._copy(self._playit_address)
        else:
            self.set_status("No playit address yet (click 'Configure').")

    # ---------- Persistent profile ----------
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
            "=== Play on the same network (LAN / Wi‑Fi) ===\n"
            f"Address to share with players:  {self._lan_ip}:{port}\n\n"
            "=== Play remotely (Internet) ===\n"
            f"Public IP:  {self._public_ip}:{port}\n\n"
            f"To allow outside access, open port {port} (TCP) on your router:\n"
            "  1. Router admin page (often http://192.168.1.1)\n"
            "  2. Section 'NAT/PAT' or 'Port forwarding'\n"
            f"  3. Forward port {port} (TCP) to {self._lan_ip}\n\n"
            "Alternative without router config: use the tunnel (button \"Enable remote access\").\n"
            "Also allow java.exe through the Windows firewall."
        )
        self._info("Server access", msg)

    def _open_folder(self):
        target = self.current_dir or paths.DATA_DIR
        try:
            os.startfile(target)
        except OSError:
            webbrowser.open(target)

    # ---------- Automatic crash report configuration ----------
    def _ask_secret(self, title, prompt):
        """Small modal for masked password entry."""
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
        ttk.Button(btns, text="Cancel", command=top.destroy).pack(side="left", padx=4)
        ent.bind("<Return>", lambda e: ok())
        self.root.wait_window(top)
        return result["val"]

    def _config_mail(self):
        configured = " (already configured)" if mailer.is_configured() else ""
        if not self._confirm(
                "Automatic crash reports" + configured,
                "To send error reports automatically, a Gmail account and an "
                "application password (16 characters) are required.\n\n"
                "You can create one at https://myaccount.google.com/apppasswords (2FA required).",
                ok="Configure", cancel="Cancel"):
            return
        address = simpledialog.askstring(
            "Gmail address", "Gmail address that SENDS the reports:",
            initialvalue=crash_reporter.DEV_EMAIL, parent=self.root)
        if not address:
            return
        pwd = self._ask_secret("Application password",
                               "Gmail application password (16 chars):")
        if not pwd:
            return
        mailer.save_creds(address.strip(), pwd)
        if self._confirm("Credentials saved",
                         "Send a test e-mail to verify the configuration?",
                         ok="Send test", cancel="Later"):
            self._test_mail()

    def _test_mail(self):
        def work():
            try:
                mailer.send(crash_reporter.DEV_EMAIL, "EZ Launcher - test",
                            "This is a test e-mail: automatic crash reports are working.")
                self.ui(lambda: self._info("Test successful", "The test e-mail was sent successfully."))
            except Exception as e:
                self.ui(lambda: self._error(
                    "Send failed",
                    f"Sending failed:\n{e}\n\n"
                    "Check the address and application password."))
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

    # Crash report: automatic SMTP send if configured, otherwise open the mail client.
    def on_report(path, mode):
        try:
            if mode == "auto":
                detail = "The report was sent automatically to the developer."
            else:
                detail = ("Your mail client opened: click Send.\n"
                          "(Enable automatic sending via the 'Auto reports' button in the Server tab.)")
            messagebox.showerror(
                "Crash",
                f"An error occurred.\n\n{detail}\n\nReport file:\n{path}")
        except tk.TclError:
            pass

    handler = crash_reporter.install(app.crash_context, APP_VERSION, on_report)
    root.report_callback_exception = lambda et, e, tb: handler(et, e, tb)

    def on_close():
        if app.server and app.server.is_running():
            if not app._confirm("Quit application",
                                 "The server is running. Stop it and quit?",
                                 ok="Stop and quit", cancel="Cancel"):
                return
            app.server.kill()
        if app.playit_agent and app.playit_agent.is_running():
            app._user_stopped_playit = True
            app.playit_agent.stop()
        app._save_config()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
