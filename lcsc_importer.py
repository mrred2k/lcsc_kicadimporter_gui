import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
import threading
import logging
import locale
import re
import json
import os
import glob
import shutil
import tempfile
import webbrowser
from pathlib import Path

APP_VERSION = "0.3.0"
APP_URL     = "https://github.com/mrred2k/lcsc_kicadimporter_gui"

DEFAULT_OUTPUT_DIR = ""
DEFAULT_3D_VAR = "${KICAD_USER_3DMODEL_DIR}"


# ── Tooltip ───────────────────────────────────────────────────────────────────
_TOOLTIP_DELAY_MS = 600  # typical system tooltip delay


class ToolTip:
    """Hover tooltip with delay and global on/off toggle."""
    enabled: "tk.BooleanVar | None" = None  # assigned after root creation

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self._tip = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._cancel)
        widget.bind("<ButtonPress>", self._cancel)

    def _schedule(self, _=None):
        if ToolTip.enabled and not ToolTip.enabled.get():
            return
        self._cancel_after()
        self._after_id = self.widget.after(_TOOLTIP_DELAY_MS, self._show)

    def _cancel(self, _=None):
        self._cancel_after()
        self._hide()

    def _cancel_after(self):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self):
        if ToolTip.enabled and not ToolTip.enabled.get():
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        ttk.Label(tw, text=self.text, background="#ffffcc", relief="solid",
                  borderwidth=1, wraplength=340, justify=tk.LEFT,
                  padding=(5, 3)).pack()

    def _hide(self):
        if self._tip:
            self._tip.destroy()
            self._tip = None


# ── Language / i18n ──────────────────────────────────────────────────────────
try:
    _sys_locale = locale.getlocale()[0] or ""
except Exception:
    _sys_locale = ""
_LANG = "de" if _sys_locale.startswith("de") else "en"
_lang_widgets: list = []   # (widget, config_key, string_key)
_lang_tips:    list = []   # (ToolTip instance, string_key)

_STRINGS: dict = {
    "de": {
        "window_title":   "LCSC → KiCad Importer",
        "lbl_lcsc":       "LCSC-ID(s):",
        "lbl_name":       "MPN:",
        "frm_component":  "Bauteil",
        "lbl_from_api":   "← API",
        "btn_fetch":      "Daten abrufen",
        "frm_comp_data":  "Bauteil-Daten",
        "frm_specs":      "Spezifikationen",
        "col_param":      "Parameter",
        "col_value":      "Wert",
        "lbl_import_opts":"Import & Optionen",
        "status_ready":   "Bereit",
        "status_loading": "Lade {id}…",
        "batch_mpn":      "{n} Bauteile",
        "batch_hint":     "Keine Vorschau bei Mehrfacheingabe — Trockenübung nutzen oder Log prüfen",
        "lbl_datasheet":  "Datenblatt ↗",
        "lbl_matched":    "● gefunden",
        "lbl_not_found":  "○ nicht gefunden",
        "lbl_output":     "Ausgabe",
        "rb_newlib":      "Neue Library",
        "rb_merge":       "In bestehende Library mergen",
        "lbl_import":     "Import:",
        "rb_symbol":      "Symbol",
        "rb_footprint":   "Footprint",
        "rb_3d":          "3D-Modell",
        "cb_overwrite":   "Überschreiben",
        "cb_cache":       "Cache",
        "cb_projrel":     "Im Projektordner",
        "cb_debug":       "Debug",
        "cb_verbose":     "Verbose Log",
        "cb_tooltips":    "Tooltips",
        "lbl_3dvar":      "3D-Variable:",
        "lbl_custom":     "Custom Fields:",
        "lbl_custom_hint":"z.B.  Mfr:TI  Package:QFN-36",
        "btn_run":        "Import starten",
        "btn_dryrun":     "Trockenübung",
        "btn_clear":      "Log leeren",
        "frm_log":        "Ausgabe",
        "lbl_newlib_sym": "Symbols-Ordner:",
        "lbl_newlib_fp":  "Footprints-Ordner:",
        "lbl_newlib_3d":  "3D-Ordner:",
        "lbl_merge_sym":  "Symbol-Lib:",
        "lbl_merge_fp":   "Footprint-Lib:",
        "lbl_merge_3d":   "3D-Ordner:",
        # browse dialog titles
        "browse_newlib_sym": "Symbols-Ordner auswählen (MPN.kicad_sym wird hier abgelegt)",
        "browse_newlib_fp":  "Footprints-Ordner auswählen (MPN.pretty/ wird hier erstellt)",
        "browse_newlib_3d":  "3D-Basisordner auswählen (MPN.3dshapes/ wird hier erstellt)",
        "browse_merge_sym":  "Symbol-Library auswählen",
        "browse_merge_fp":   "Footprint-Library (.pretty Ordner) auswählen",
        "browse_merge_3d":   "3D-Modell-Ordner (.3dshapes Ordner) auswählen",
        "filetype_sym":      "KiCad Symbol-Library",
        "filetype_all":      "Alle Dateien",
        # tooltips
        "tip_lcsc": (
            "Einzelne oder mehrere LCSC-IDs.\n"
            "Trennzeichen: Komma, Semikolon oder Leerzeichen.\n"
            "Beispiel: C6022114, C2040, C15234\n"
            "Duplikate werden automatisch entfernt.\n"
            "Bei mehreren IDs erscheint eine Bestätigungsabfrage."
        ),
        "tip_name": (
            "Dateiname der generierten Library-Dateien (z.B. DRV8317HREER).\n"
            "Wird automatisch mit dem MPN aus der EasyEDA API befüllt\n"
            "sobald eine einzelne LCSC-ID eingegeben wird.\n"
            "Kann manuell überschrieben werden.\n"
            "Bei mehreren IDs: pro Komponente eigener MPN-Name."
        ),
        "tip_newlib_sym": (
            "Ordner wo die Symbol-Datei abgelegt wird.\n"
            "Ergebnis: <Ordner>\\MPN.kicad_sym\n\n"
            "Beispiel: C:\\…\\Kicad Data\\Symbols\\"
        ),
        "tip_newlib_fp": (
            "Ordner wo der Footprint-Unterordner erstellt wird.\n"
            "Ergebnis: <Ordner>\\MPN.pretty\\\n\n"
            "Beispiel: C:\\…\\Kicad Data\\Footprints\\"
        ),
        "tip_newlib_3d": (
            "Ordner wo der 3D-Modell-Unterordner erstellt wird.\n"
            "Ergebnis: <Ordner>\\MPN.3dshapes\\\n\n"
            "Die 3D-Variable (unten) muss auf diesen Ordner zeigen.\n"
            "Beispiel: C:\\…\\Kicad Data\\3D Data\\"
        ),
        "tip_merge_sym": (
            "Ziel-Symbol-Library (.kicad_sym Datei).\n"
            "Das neue Symbol wird in diese Datei eingemergt.\n"
            "Die Datei wird erstellt falls sie noch nicht existiert.\n"
            "Beispiel: C:/…/Kicad Data/Symbols/Mycomponents.kicad_sym"
        ),
        "tip_merge_fp": (
            "Ziel-Footprint-Library (.pretty Ordner).\n"
            "Alle .kicad_mod Dateien werden in diesen Ordner kopiert.\n"
            "Der Ordner wird erstellt falls er noch nicht existiert.\n"
            "Beispiel: C:/…/Kicad Data/Footprints/Mycomponents.pretty"
        ),
        "tip_merge_3d": (
            "Ziel-3D-Ordner (.3dshapes Ordner).\n"
            "Alle 3D-Modelle (.wrl / .step) werden in diesen Ordner kopiert.\n"
            "Der Ordner wird erstellt falls er noch nicht existiert.\n"
            "Beispiel: C:/…/Kicad Data/3D Data/Mycomponents.3dshapes\n\n"
            "Die 3D-Variable (unten) muss auf den ÜBERGEORDNETEN Ordner zeigen,\n"
            "also z.B. auf 'C:/…/Kicad Data/3D Data/'."
        ),
        "tip_rb_symbol":    "KiCad-Symbol (.kicad_sym) importieren",
        "tip_rb_footprint": "Footprint (.kicad_mod) importieren",
        "tip_rb_3d":        "3D-Modell (.wrl / .step) importieren",
        "tip_cb_overwrite": (
            "Bestehende Komponente überschreiben (--overwrite).\n"
            "Ohne diese Option schlägt der Import fehl, wenn die Komponente bereits existiert."
        ),
        "tip_cb_cache": (
            "CAD-Daten (Symbol, Footprint, 3D) lokal zwischenspeichern (--use-cache).\n"
            "Beschleunigt den Import bei erneuter Verwendung derselben Komponente.\n"
            "Cache liegt in .easyeda_cache/ im aktuellen Verzeichnis.\n"
            "Hinweis: betrifft nur den Import, nicht die Bauteil-Vorschau."
        ),
        "tip_cb_projrel": (
            "Speichert den 3D-Pfad relativ zum KiCad-Projekt (--project-relative).\n"
            "Verwendet ${KIPRJMOD} — den Ordner der .kicad_pro-Datei — als Basis.\n\n"
            "Zwei Systeme:\n"
            "  Global: 3D-Variable oben setzen, diese Option AUS\n"
            "  Projektordner: Ausgabe liegt im Projektordner, diese Option AN\n\n"
            "Bei globalen Libraries (z.B. git-Submodule) diese Option NICHT verwenden."
        ),
        "tip_cb_debug": (
            "Ausführliches Debug-Logging von easyeda2kicad aktivieren (--debug).\n"
            "Nützlich bei Problemen mit dem API-Abruf oder der Konvertierung."
        ),
        "tip_cb_verbose": (
            "Alle Ausgaben von easyeda2kicad anzeigen.\n"
            "Ohne diese Option: nur [INFO]/[WARNING]/[ERROR]-Zeilen sichtbar."
        ),
        "tip_3dvar": (
            "KiCad-Pfadvariable für 3D-Modelle.\n\n"
            "easyeda2kicad schreibt in .kicad_mod-Dateien den absoluten Ausgabepfad\n"
            "als 3D-Modell-Pfad – das ist ein bekannter Bug. Dieser Importer ersetzt\n"
            "diesen absoluten Pfad automatisch durch die hier eingestellte Variable.\n\n"
            "Neue Library: Variable muss auf den Ausgabeordner zeigen.\n"
            "Merge-Modus:  Variable muss auf den Ordner ÜBER dem .3dshapes-Ordner zeigen.\n\n"
            "Vorschläge werden automatisch aus der KiCad-Konfiguration gelesen\n"
            "(alle installierten Versionen, neueste zuerst).\n"
            "→ In KiCad unter Preferences → Configure Paths setzen."
        ),
        "tip_custom": (
            "Eigene Symbol-Properties hinzufügen (--custom-field).\n"
            "Leerzeichen-getrennte KEY:VALUE Paare.\n"
            "Beispiel: Mfr:TI Package:QFN-36 Datasheet:https://ti.com/lit/ds/..."
        ),
        "tip_dryrun": (
            "Simuliert den Import ohne Dateien zu schreiben.\n"
            "Zeigt welche Dateien angelegt, überschrieben oder\n"
            "übersprungen würden — nützlich vor großen Batch-Imports."
        ),
        "btn_kicad_vars":  "Von KiCad",
        "tip_kicad_vars": (
            "Pfadvariablen aus KiCad-Konfiguration laden.\n"
            "Liest kicad_common.json aller installierten Versionen\n"
            "(neueste zuerst) und bietet die gefundenen Variablen\n"
            "als Vorschläge in der 3D-Variable-Auswahl an."
        ),
        "log_kicad_vars_found": "KiCad-Variablen geladen (v{version}):\n",
        "log_kicad_vars_none":  "Keine KiCad-Konfiguration gefunden.\n",
        # dialogs / log messages
        "dlg_loading_title": "Lade Komponentennamen…",
        "dlg_loading_msg":   "Rufe Namen für {n} Komponenten ab…",
        "dlg_confirm_title": "Import bestätigen",
        "dlg_confirm_count": "{n} Komponenten erkannt:",
        "dlg_confirm_q":     "Alle importieren?",
        "btn_yes":           "Ja, importieren",
        "btn_cancel":        "Abbrechen",
        "warn_no_name":      "  ⚠ Name nicht gefunden",
        "err_no_id":         "Fehler: Keine LCSC-ID eingegeben.\n",
        "err_no_modes":      "Fehler: Kein Import-Typ ausgewählt (Symbol/Footprint/3D).\n",
        "info_dups":         "Info: {n} Duplikat(e) entfernt.\n",
        "no_output":         "(keine Ausgabe)\n",
        "err_not_found":     "Fehler: Python oder easyeda2kicad nicht gefunden.\n",
        # dry run
        "dryrun_header":      "TROCKENÜBUNG — kein Import, keine Dateien werden geschrieben",
        "dryrun_footer":      "── Ende Trockenübung ──",
        "dryrun_create":      "NEU",
        "dryrun_overwrite":   "ÜBERSCHREIBEN",
        "dryrun_skip":        "SKIP (existiert, Überschreiben aus)",
        "dryrun_new_lib":     "neue Datei",
        "dryrun_batch_note":  "  (Name noch nicht aufgelöst — LCSC-ID als Platzhalter)\n",
        # merge/distribute messages
        "merge_no_syms":   "Keine Symbole in generierter Datei gefunden.",
        "merge_exists":    "'{name}' existiert bereits (Überschreiben aktivieren).",
        "merge_invalid":   "Ungültige .kicad_sym-Datei (kein schließendes ')').",
        "sym_src_missing": "Quelldatei nicht gefunden ({name})",
        "fp_src_missing":  "Quellordner nicht gefunden ({name})",
        "td_src_missing":  "Quellordner nicht gefunden ({name})",
        "fp_exists":       "Footprint {name}: bereits vorhanden.",
        "td_exists":       "3D {name}: bereits vorhanden.",
        "sym_exists":      "{name} bereits vorhanden.",
        "no_sym_dir":      "Symbols-Ordner nicht angegeben.",
        "no_fp_dir":       "Footprints-Ordner nicht angegeben.",
        "no_3d_dir":       "3D-Ordner nicht angegeben.",
        "tip_lang":        "Switch to English",
        "tip_fetch":       "Erneut abrufen (wird beim Tippen automatisch ausgelöst)",
        "btn_preview":     "Vorschau",
        "tip_preview": (
            "Symbol und Footprint als SVG im Browser anzeigen.\n"
            "Kein Import — nur zur Ansicht vor dem eigentlichen Import."
        ),
        "preview_loading":   "Lade Vorschau für {lcsc_id}…\n",
        "preview_not_found": "Vorschau: Komponente nicht gefunden.\n",
        "preview_error":     "Vorschau-Fehler: {error}\n",
        "btn_about":       "ℹ",
        "tip_about":       "Über dieses Programm",
        "dlg_about_title": "Über LCSC → KiCad Importer",
        "about_desc":      "GUI-Wrapper für easyeda2kicad.\nImportiert LCSC-Komponenten in KiCad-Libraries.",
        "about_source":    "Quelle:",
        "about_license":   "Lizenz: MIT",
    },
    "en": {
        "window_title":   "LCSC → KiCad Importer",
        "lbl_lcsc":       "LCSC ID(s):",
        "lbl_name":       "MPN:",
        "frm_component":  "Component",
        "lbl_from_api":   "← API",
        "btn_fetch":      "Fetch data",
        "frm_comp_data":  "Component Data",
        "frm_specs":      "Specifications",
        "col_param":      "Parameter",
        "col_value":      "Value",
        "lbl_import_opts":"Import & Options",
        "status_ready":   "Ready",
        "status_loading": "Loading {id}…",
        "batch_mpn":      "{n} components",
        "batch_hint":     "No preview for batch input — use Dry-run or check the log",
        "lbl_datasheet":  "Datasheet ↗",
        "lbl_matched":    "● matched",
        "lbl_not_found":  "○ not found",
        "lbl_output":     "Output",
        "rb_newlib":      "New Library",
        "rb_merge":       "Merge into existing Library",
        "lbl_import":     "Import:",
        "rb_symbol":      "Symbol",
        "rb_footprint":   "Footprint",
        "rb_3d":          "3D Model",
        "cb_overwrite":   "Overwrite",
        "cb_cache":       "Cache",
        "cb_projrel":     "In project folder",
        "cb_debug":       "Debug",
        "cb_verbose":     "Verbose Log",
        "cb_tooltips":    "Tooltips",
        "lbl_3dvar":      "3D Variable:",
        "lbl_custom":     "Custom Fields:",
        "lbl_custom_hint":"e.g.  Mfr:TI  Package:QFN-36",
        "btn_run":        "Start Import",
        "btn_dryrun":     "Dry Run",
        "btn_clear":      "Clear Log",
        "frm_log":        "Output",
        "lbl_newlib_sym": "Symbols Folder:",
        "lbl_newlib_fp":  "Footprints Folder:",
        "lbl_newlib_3d":  "3D Folder:",
        "lbl_merge_sym":  "Symbol Lib:",
        "lbl_merge_fp":   "Footprint Lib:",
        "lbl_merge_3d":   "3D Folder:",
        # browse dialog titles
        "browse_newlib_sym": "Select Symbols Folder (MPN.kicad_sym will be placed here)",
        "browse_newlib_fp":  "Select Footprints Folder (MPN.pretty/ will be created here)",
        "browse_newlib_3d":  "Select 3D Base Folder (MPN.3dshapes/ will be created here)",
        "browse_merge_sym":  "Select Symbol Library",
        "browse_merge_fp":   "Select Footprint Library (.pretty folder)",
        "browse_merge_3d":   "Select 3D Model Folder (.3dshapes folder)",
        "filetype_sym":      "KiCad Symbol Library",
        "filetype_all":      "All Files",
        # tooltips
        "tip_lcsc": (
            "Single or multiple LCSC IDs.\n"
            "Separator: comma, semicolon or space.\n"
            "Example: C6022114, C2040, C15234\n"
            "Duplicates are removed automatically.\n"
            "For multiple IDs a confirmation dialog is shown."
        ),
        "tip_name": (
            "Filename of the generated library files (e.g. DRV8317HREER).\n"
            "Auto-filled with the MPN from the EasyEDA API\n"
            "when a single LCSC ID is entered.\n"
            "Can be overridden manually.\n"
            "For multiple IDs: each component gets its own MPN name."
        ),
        "tip_newlib_sym": (
            "Folder where the symbol file will be placed.\n"
            "Result: <folder>\\MPN.kicad_sym\n\n"
            "Example: C:\\…\\Kicad Data\\Symbols\\"
        ),
        "tip_newlib_fp": (
            "Folder where the footprint subfolder will be created.\n"
            "Result: <folder>\\MPN.pretty\\\n\n"
            "Example: C:\\…\\Kicad Data\\Footprints\\"
        ),
        "tip_newlib_3d": (
            "Folder where the 3D model subfolder will be created.\n"
            "Result: <folder>\\MPN.3dshapes\\\n\n"
            "The 3D variable (below) must point to this folder.\n"
            "Example: C:\\…\\Kicad Data\\3D Data\\"
        ),
        "tip_merge_sym": (
            "Target symbol library (.kicad_sym file).\n"
            "The new symbol will be merged into this file.\n"
            "The file will be created if it does not exist.\n"
            "Example: C:/…/Kicad Data/Symbols/Mycomponents.kicad_sym"
        ),
        "tip_merge_fp": (
            "Target footprint library (.pretty folder).\n"
            "All .kicad_mod files will be copied into this folder.\n"
            "The folder will be created if it does not exist.\n"
            "Example: C:/…/Kicad Data/Footprints/Mycomponents.pretty"
        ),
        "tip_merge_3d": (
            "Target 3D folder (.3dshapes folder).\n"
            "All 3D models (.wrl / .step) will be copied into this folder.\n"
            "The folder will be created if it does not exist.\n"
            "Example: C:/…/Kicad Data/3D Data/Mycomponents.3dshapes\n\n"
            "The 3D variable (below) must point to the PARENT folder,\n"
            "e.g. 'C:/…/Kicad Data/3D Data/'."
        ),
        "tip_rb_symbol":    "Import the KiCad symbol (.kicad_sym)",
        "tip_rb_footprint": "Import the footprint (.kicad_mod)",
        "tip_rb_3d":        "Import the 3D model (.wrl / .step)",
        "tip_cb_overwrite": (
            "Overwrite existing component (--overwrite).\n"
            "Without this option the import fails if the component already exists."
        ),
        "tip_cb_cache": (
            "Cache CAD data (symbol, footprint, 3D) locally (--use-cache).\n"
            "Speeds up re-importing the same component without re-downloading.\n"
            "Cache is stored in .easyeda_cache/ in the current directory.\n"
            "Note: only affects import, not the component info preview."
        ),
        "tip_cb_projrel": (
            "Stores the 3D path relative to the KiCad project (--project-relative).\n"
            "Uses ${KIPRJMOD} — the folder of the .kicad_pro file — as base.\n\n"
            "Two systems:\n"
            "  Global: set 3D variable above, leave this OFF\n"
            "  Project folder: output is inside the project folder, turn this ON\n\n"
            "Do NOT use for global libraries (e.g. git submodules)."
        ),
        "tip_cb_debug": (
            "Enable verbose debug logging from easyeda2kicad (--debug).\n"
            "Useful when troubleshooting API fetches or conversions."
        ),
        "tip_cb_verbose": (
            "Show all output from easyeda2kicad.\n"
            "Without this option: only [INFO]/[WARNING]/[ERROR] lines are shown."
        ),
        "tip_3dvar": (
            "KiCad path variable for 3D models.\n\n"
            "easyeda2kicad writes the absolute output path into .kicad_mod files\n"
            "as the 3D model path — this is a known bug. This importer replaces\n"
            "the absolute path automatically with the variable set here.\n\n"
            "New Library: variable must point to the output folder.\n"
            "Merge mode:  variable must point to the folder ABOVE the .3dshapes folder.\n\n"
            "Suggestions are loaded automatically from the KiCad configuration\n"
            "(all installed versions, newest first).\n"
            "→ Set in KiCad under Preferences → Configure Paths."
        ),
        "tip_custom": (
            "Add custom symbol properties (--custom-field).\n"
            "Space-separated KEY:VALUE pairs.\n"
            "Example: Mfr:TI Package:QFN-36 Datasheet:https://ti.com/lit/ds/..."
        ),
        "tip_dryrun": (
            "Simulates the import without writing any files.\n"
            "Shows which files would be created, overwritten,\n"
            "or skipped — useful before large batch imports."
        ),
        "btn_kicad_vars":  "From KiCad",
        "tip_kicad_vars": (
            "Load path variables from KiCad configuration.\n"
            "Reads kicad_common.json from all installed versions\n"
            "(newest first) and offers the found variables\n"
            "as suggestions in the 3D variable dropdown."
        ),
        "log_kicad_vars_found": "KiCad variables loaded (v{version}):\n",
        "log_kicad_vars_none":  "No KiCad configuration found.\n",
        # dialogs / log messages
        "dlg_loading_title": "Loading component names…",
        "dlg_loading_msg":   "Fetching names for {n} components…",
        "dlg_confirm_title": "Confirm Import",
        "dlg_confirm_count": "{n} components detected:",
        "dlg_confirm_q":     "Import all?",
        "btn_yes":           "Yes, import",
        "btn_cancel":        "Cancel",
        "warn_no_name":      "  ⚠ Name not found",
        "err_no_id":         "Error: No LCSC ID entered.\n",
        "err_no_modes":      "Error: No import type selected (Symbol/Footprint/3D).\n",
        "info_dups":         "Info: {n} duplicate(s) removed.\n",
        "no_output":         "(no output)\n",
        "err_not_found":     "Error: Python or easyeda2kicad not found.\n",
        # dry run
        "dryrun_header":      "DRY RUN — no import, no files will be written",
        "dryrun_footer":      "── End Dry Run ──",
        "dryrun_create":      "CREATE",
        "dryrun_overwrite":   "OVERWRITE",
        "dryrun_skip":        "SKIP (exists, overwrite off)",
        "dryrun_new_lib":     "new file",
        "dryrun_batch_note":  "  (name not yet resolved — using LCSC ID as placeholder)\n",
        # merge/distribute messages
        "merge_no_syms":   "No symbols found in generated file.",
        "merge_exists":    "'{name}' already exists (enable Overwrite).",
        "merge_invalid":   "Invalid .kicad_sym file (no closing ')').",
        "sym_src_missing": "Source file not found ({name})",
        "fp_src_missing":  "Source folder not found ({name})",
        "td_src_missing":  "Source folder not found ({name})",
        "fp_exists":       "Footprint {name}: already exists.",
        "td_exists":       "3D {name}: already exists.",
        "sym_exists":      "{name} already exists.",
        "no_sym_dir":      "Symbols folder not specified.",
        "no_fp_dir":       "Footprints folder not specified.",
        "no_3d_dir":       "3D folder not specified.",
        "tip_lang":        "Auf Deutsch wechseln",
        "tip_fetch":       "Re-fetch (auto-triggers while you type)",
        "btn_preview":     "Preview",
        "tip_preview": (
            "Show symbol and footprint as SVG in the browser.\n"
            "No import — just for inspection before the actual import."
        ),
        "preview_loading":   "Loading preview for {lcsc_id}…\n",
        "preview_not_found": "Preview: component not found.\n",
        "preview_error":     "Preview error: {error}\n",
        "btn_about":       "ℹ",
        "tip_about":       "About this program",
        "dlg_about_title": "About LCSC → KiCad Importer",
        "about_desc":      "GUI wrapper for easyeda2kicad.\nImports LCSC components into KiCad libraries.",
        "about_source":    "Source:",
        "about_license":   "License: MIT",
    },
}


def _t(key: str, **kwargs) -> str:
    s = _STRINGS[_LANG].get(key, _STRINGS["de"].get(key, key))
    return s.format(**kwargs) if kwargs else s


def _apply_lang():
    """Update all registered widgets and tooltips to the current language."""
    root.title(_t("window_title"))
    for widget, cfg_key, str_key in _lang_widgets:
        try:
            widget.config(**{cfg_key: _t(str_key)})
        except Exception:
            pass
    for tip, str_key in _lang_tips:
        tip.text = _t(str_key)


def _toggle_lang():
    global _LANG
    _LANG = "en" if _LANG == "de" else "de"
    # Show the flag of the language you'd switch TO next
    btn_lang.config(image=_flag_de if _LANG == "en" else _flag_uk)
    _apply_lang()


def _reg(widget, key: str):
    """Register widget for language updates and return it."""
    _lang_widgets.append((widget, "text", key))
    return widget


def _tip(widget, key: str):
    """Create a ToolTip with translation key and register it for updates."""
    t = ToolTip(widget, _t(key))
    _lang_tips.append((t, key))
    return t


# ── ID parsing ────────────────────────────────────────────────────────────────
def _parse_ids(raw: str) -> list:
    """Split comma/semicolon/whitespace separated IDs, deduplicate, keep order."""
    parts = re.split(r"[,;\s]+", raw.strip())
    seen, result = set(), []
    for p in parts:
        p = p.strip()
        if p and p not in seen:
            seen.add(p)
            result.append(p)
    return result


# ── Design color tokens (used by UI and callback functions) ──────────────────
_ACCENT          = "#2469c4"
_MUTED           = "#6b7480"
_TEXT            = "#20262e"
_CHIP_NEUTRAL_BG = "#f0f2f5"
_CHIP_NEUTRAL_FG = "#3a4048"
_CHIP_STOCK_BG   = "#e4f3ea"
_CHIP_STOCK_FG   = "#1f8a4d"
_CHIP_EXT_BG     = "#fff3e0"
_CHIP_EXT_FG     = "#b85c00"
_CHIP_ROHS_BG    = "#e6f0fb"
_CHIP_ROHS_FG    = "#2469c4"
_CANVAS_SYM_BG   = "white"
_CANVAS_FP_BG    = "#0c0d10"

# ── API fetch helpers ─────────────────────────────────────────────────────────
_API_TIMEOUT = 10  # seconds for all external API calls


def _fetch_info(lcsc_id: str) -> tuple[str, str]:
    """Return (name, description) via get_info_from_easyeda_api — one fast call.
    The title field contains the MPN; no CAD download needed for display."""
    import socket
    old = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(_API_TIMEOUT)
        from easyeda2kicad.easyeda.easyeda_api import EasyedaApi
        result = EasyedaApi().get_info_from_easyeda_api(lcsc_id).get("result", {})
        name = result.get("title", "").strip()
        desc = result.get("description", "").strip()
        if not desc:
            tags = result.get("tags", [])
            desc = ", ".join(tags) if tags else ""
        if var_debug.get():  # type: ignore[name-defined]
            root.after(0, lambda: log(f"[debug] info: name={name!r}  desc={desc[:60]!r}\n", "info"))  # type: ignore[name-defined]
        return name or lcsc_id, desc
    except Exception as e:
        if var_debug.get():  # type: ignore[name-defined]
            root.after(0, lambda: log(f"[debug] _fetch_info({lcsc_id}): {e}\n", "info"))  # type: ignore[name-defined]
    finally:
        socket.setdefaulttimeout(old)
    return lcsc_id, ""


def _fetch_mpn(lcsc_id: str) -> str | None:
    """Return MPN from full EasyEDA CAD data — used for batch name resolution
    (more accurate than title for some components)."""
    import socket
    old = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(_API_TIMEOUT)
        from easyeda2kicad.easyeda.easyeda_api import EasyedaApi
        from easyeda2kicad.easyeda.easyeda_importer import EasyedaSymbolImporter
        cad = EasyedaApi().get_cad_data_of_component(lcsc_id)
        if cad:
            return EasyedaSymbolImporter(easyeda_cp_cad_data=cad).get_symbol().info.name
    except Exception as e:
        if var_debug.get():  # type: ignore[name-defined]
            root.after(0, lambda: log(f"[debug] _fetch_mpn({lcsc_id}): {e}\n", "info"))  # type: ignore[name-defined]
    finally:
        socket.setdefaulttimeout(old)
    return None


# ── JLCPCB assembly data fetch ───────────────────────────────────────────────
def _fetch_jlcpcb(lcsc_id: str) -> dict:
    """Fetch assembly info from the public JLCPCB parts API (no auth needed)."""
    import socket
    old = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(_API_TIMEOUT)
        from easyeda2kicad.easyeda.easyeda_api import EasyedaApi
        results = EasyedaApi().search_jlcpcb_components(keyword=lcsc_id, page_size=5)
        for r in results.get("results", []):
            if r.get("lcsc", "").upper() == lcsc_id.upper():
                if var_debug.get():  # type: ignore[name-defined]
                    root.after(0, lambda r=r: log(f"[debug] JLCPCB: {r.get('name')}  stock={r.get('stock')}  type={r.get('type')}\n", "info"))  # type: ignore[name-defined]
                return r
        if results.get("results"):
            r0 = results["results"][0]
            if var_debug.get():  # type: ignore[name-defined]
                root.after(0, lambda r=r0: log(f"[debug] JLCPCB (first): {r.get('name')}  stock={r.get('stock')}\n", "info"))  # type: ignore[name-defined]
            return r0
    except Exception as e:
        if var_debug.get():  # type: ignore[name-defined]
            root.after(0, lambda: log(f"[debug] _fetch_jlcpcb({lcsc_id}): {e}\n", "info"))  # type: ignore[name-defined]
    finally:
        socket.setdefaulttimeout(old)
    return {}


# ── Description fetch & inject ──────────────────────────────────────────────
def _fetch_description(lcsc_id: str) -> str:
    """Get description from EasyEDA API. Falls back to tags if description is empty."""
    import socket
    old = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(_API_TIMEOUT)
        from easyeda2kicad.easyeda.easyeda_api import EasyedaApi
        result = EasyedaApi().get_info_from_easyeda_api(lcsc_id).get("result", {})
        desc = result.get("description", "").strip()
        if not desc:
            tags = result.get("tags", [])
            desc = ", ".join(tags) if tags else ""
        if var_debug.get():  # type: ignore[name-defined]
            root.after(0, lambda: log(f"[debug] description: {desc!r}\n", "info"))  # type: ignore[name-defined]
        return desc
    except Exception as e:
        if var_debug.get():  # type: ignore[name-defined]
            root.after(0, lambda: log(f"[debug] _fetch_description({lcsc_id}): {e}\n", "info"))  # type: ignore[name-defined]
    finally:
        socket.setdefaulttimeout(old)
    return ""


def _kicad_escape(s: str) -> str:
    """Escape a string for embedding in a KiCad S-expression."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _patch_description(output_base: str, description: str) -> None:
    """Inject description into generated .kicad_sym and .kicad_mod files if empty."""
    escaped = _kicad_escape(description)

    sym_file = Path(output_base + ".kicad_sym")
    if sym_file.exists():
        content = sym_file.read_text(encoding="utf-8")
        new_content = re.sub(
            r'\(property "(Description|ki_description)" ""',
            f'(property "\\1" "{escaped}"',
            content,
        )
        if new_content != content:
            sym_file.write_text(new_content, encoding="utf-8")

    pretty_dir = Path(output_base + ".pretty")
    if pretty_dir.exists():
        for kmod in pretty_dir.glob("*.kicad_mod"):
            content = kmod.read_text(encoding="utf-8")
            new_content = content.replace('(descr "")', f'(descr "{escaped}")', 1)
            if new_content != content:
                kmod.write_text(new_content, encoding="utf-8")


# ── 3D path post-processing ───────────────────────────────────────────────────
def fix_3d_paths(output_base: str, var_3d: str) -> list:
    """Replace absolute 3D model paths in .kicad_mod files with a KiCad variable.

    easyeda2kicad hardcodes the absolute output directory as the 3D path in
    .kicad_mod files. This replaces e.g.
      "C:/Users/.../Kicad Data/DRV8317.3dshapes/model.wrl"
    with
      "${KICAD_USER_3DMODEL_DIR}/DRV8317.3dshapes/model.wrl"
    """
    pretty_dir = Path(output_base + ".pretty")
    if not pretty_dir.exists():
        return []
    # The absolute prefix easyeda2kicad writes is: parent_of_output_base + "/"
    abs_prefix = Path(output_base).parent.as_posix() + "/"
    new_prefix = var_3d.rstrip("/") + "/"
    patched = []
    for kmod in pretty_dir.glob("*.kicad_mod"):
        txt = kmod.read_text(encoding="utf-8")
        new_txt = txt.replace(f'"{abs_prefix}', f'"{new_prefix}')
        if new_txt != txt:
            kmod.write_text(new_txt, encoding="utf-8")
            patched.append(kmod.name)
    return patched


# ── Config persistence ────────────────────────────────────────────────────────
CONFIG_FILE = Path(__file__).parent / "lcsc_importer_config.json"


def _load_config() -> dict:
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_config():
    try:
        cfg = {
            "merge_mode":     var_merge_mode.get(),
            "newlib_sym_dir": entry_newlib_sym.get(),
            "newlib_fp_dir":  entry_newlib_fp.get(),
            "newlib_3d_dir":  entry_newlib_3d.get(),
            "merge_sym_lib":  entry_merge_sym.get(),
            "merge_fp_lib":   entry_merge_fp.get(),
            "merge_3d_dir":   entry_merge_3d.get(),
        }
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass


# ── KiCad configuration reader ────────────────────────────────────────────────
def _load_kicad_vars() -> tuple:
    """Read path variables from KiCad's kicad_common.json (any installed version).

    Scans all version subdirectories under the platform-specific KiCad config
    folder and returns (vars_dict, config_path) from the most recently modified
    config found. Returns ({}, '') if nothing is found.
    """
    search_roots = []
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        search_roots.append(os.path.join(appdata, "kicad"))
    home = Path.home()
    search_roots += [
        str(home / ".config" / "kicad"),
        str(home / "Library" / "Preferences" / "kicad"),
    ]
    for kicad_root in search_roots:
        configs = glob.glob(os.path.join(kicad_root, "*", "kicad_common.json"))
        if configs:
            configs.sort(key=os.path.getmtime, reverse=True)
            try:
                data = json.loads(Path(configs[0]).read_text(encoding="utf-8"))
                return data.get("environment", {}).get("vars", {}), configs[0]
            except Exception:
                pass
    return {}, ""


def _reload_kicad_vars():
    """Load KiCad path variables, update the 3D variable Combobox, and log results."""
    kicad_vars, config_path = _load_kicad_vars()
    suggestions = [f"${{{k}}}" for k in kicad_vars]
    if DEFAULT_3D_VAR not in suggestions:
        suggestions.insert(0, DEFAULT_3D_VAR)
    entry_3dvar["values"] = suggestions

    if kicad_vars:
        version = Path(config_path).parent.name  # e.g. "9.0"
        log(_t("log_kicad_vars_found", version=version), "info")
        for k, v in kicad_vars.items():
            log(f"  ${{{k}}} = {v}\n", "ok")
    else:
        log(_t("log_kicad_vars_none"), "error")


# ── Symbol merge helpers ──────────────────────────────────────────────────────
def _extract_symbol_blocks(content: str) -> str:
    """Extract all top-level (symbol ...) blocks from a .kicad_sym string."""
    parts = []
    i, n, depth = 0, len(content), 0
    while i < n:
        c = content[i]
        if c == '(':
            depth += 1
            if depth == 2 and content[i:i+8] == '(symbol ':
                start, inner, j = i, 1, i + 1
                while j < n and inner > 0:
                    if content[j] == '(':    inner += 1
                    elif content[j] == ')': inner -= 1
                    j += 1
                parts.append(content[start:j])
                i, depth = j, 1
                continue
        elif c == ')':
            depth -= 1
        i += 1
    return '\n'.join(parts)


def _remove_symbol_block(content: str, name: str) -> str:
    """Remove the top-level (symbol "name" ...) block from .kicad_sym content."""
    search = f'(symbol "{name}"'
    idx = content.find(search)
    if idx == -1:
        return content
    depth, i = 0, idx
    while i < len(content):
        if content[i] == '(':    depth += 1
        elif content[i] == ')':
            depth -= 1
            if depth == 0:
                start = idx
                while start > 0 and content[start - 1] in ' \t':
                    start -= 1
                if start > 0 and content[start - 1] == '\n':
                    start -= 1
                return content[:start] + content[i + 1:]
        i += 1
    return content


def merge_symbol_into_lib(sym_content: str, target_path: Path,
                           old_fp_lib: str, new_fp_lib: str,
                           overwrite: bool) -> tuple:
    """Insert symbol(s) from sym_content into a .kicad_sym file at target_path.

    Renames footprint library references from old_fp_lib to new_fp_lib.
    Returns (success: bool, message: str).
    """
    sym_blocks = _extract_symbol_blocks(sym_content)
    if not sym_blocks:
        return False, _t("merge_no_syms")

    sym_blocks = sym_blocks.replace(f'"{old_fp_lib}:', f'"{new_fp_lib}:')
    names = re.findall(r'^\(symbol "([^"]+)"', sym_blocks, re.MULTILINE)

    if target_path.exists():
        target = target_path.read_text(encoding="utf-8")
        for name in names:
            if f'(symbol "{name}"' in target:
                if not overwrite:
                    return False, _t("merge_exists", name=name)
                target = _remove_symbol_block(target, name)
        last = target.rfind(')')
        if last == -1:
            return False, _t("merge_invalid")
        new_target = target[:last].rstrip() + '\n' + sym_blocks + '\n)\n'
    else:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        new_target = (
            '(kicad_symbol_lib\n'
            '  (version 20231120)\n'
            '  (generator "easyeda2kicad")\n'
            + sym_blocks + '\n)\n'
        )

    target_path.write_text(new_target, encoding="utf-8")
    name_list = ', '.join(names) if names else '?'
    return True, f"'{name_list}' → {target_path.name}"


# ── Merge-into-libs orchestrator ──────────────────────────────────────────────
def merge_into_libs(output_base: str, import_modes: set) -> list:
    """Merge generated temp files into the configured target libraries.

    Returns list of (message, tag) tuples for the log.
    """
    msgs = []
    mpn    = Path(output_base).name
    sym_lib = entry_merge_sym.get().strip()
    fp_lib  = entry_merge_fp.get().strip()
    dir_3d  = entry_merge_3d.get().strip()

    do_sym = "symbol" in import_modes
    do_fp  = "footprint" in import_modes
    do_3d  = "3d" in import_modes

    # ── Symbol ────────────────────────────────────────────────────────────────
    if do_sym and sym_lib:
        sym_src = Path(output_base + ".kicad_sym")
        if sym_src.exists():
            fp_lib_stem = Path(fp_lib).stem if fp_lib else mpn
            ok, msg = merge_symbol_into_lib(
                sym_src.read_text(encoding="utf-8"),
                Path(sym_lib),
                old_fp_lib=mpn,
                new_fp_lib=fp_lib_stem,
                overwrite=var_overwrite.get(),
            )
            msgs.append((f"  Symbol: {msg}\n", "ok" if ok else "error"))
        else:
            msgs.append((f"  Symbol: {_t('sym_src_missing', name=sym_src.name)}\n", "error"))

    # ── Footprint ─────────────────────────────────────────────────────────────
    if do_fp and fp_lib:
        fp_src_dir = Path(output_base + ".pretty")
        fp_dst_dir = Path(fp_lib)
        if fp_src_dir.exists():
            fp_dst_dir.mkdir(parents=True, exist_ok=True)
            var3d        = entry_3dvar.get().strip() or DEFAULT_3D_VAR
            abs_3d_pfx   = Path(output_base + ".3dshapes").as_posix() + "/"
            target_3d_nm = Path(dir_3d).name if dir_3d else mpn + ".3dshapes"
            new_3d_pfx   = var3d.rstrip("/") + "/" + target_3d_nm + "/"
            for kmod in fp_src_dir.glob("*.kicad_mod"):
                dst = fp_dst_dir / kmod.name
                if dst.exists() and not var_overwrite.get():
                    msgs.append((_t("fp_exists", name=kmod.name) + "\n", "error"))
                    continue
                txt = kmod.read_text(encoding="utf-8").replace(
                    f'"{abs_3d_pfx}', f'"{new_3d_pfx}')
                dst.write_text(txt, encoding="utf-8")
                msgs.append((f"  Footprint: {kmod.name} → {fp_dst_dir.name}\n", "ok"))
        else:
            msgs.append((f"  Footprint: {_t('fp_src_missing', name=fp_src_dir.name)}\n", "error"))

    # ── 3D Models ─────────────────────────────────────────────────────────────
    if do_3d and dir_3d:
        src_3d = Path(output_base + ".3dshapes")
        dst_3d = Path(dir_3d)
        if src_3d.exists():
            dst_3d.mkdir(parents=True, exist_ok=True)
            for model in src_3d.iterdir():
                if not model.is_file():
                    continue
                dst = dst_3d / model.name
                if dst.exists() and not var_overwrite.get():
                    msgs.append((_t("td_exists", name=model.name) + "\n", "error"))
                    continue
                shutil.copy2(model, dst)
                msgs.append((f"  3D: {model.name} → {dst_3d.name}\n", "ok"))
        else:
            msgs.append((f"  3D: {_t('td_src_missing', name=src_3d.name)}\n", "error"))

    return msgs


# ── New-library distributor ───────────────────────────────────────────────────
def distribute_new_lib(output_base: str, import_modes: set) -> list:
    """Copy generated temp files into the configured target directories.

    Neue Library mode: MPN.kicad_sym → sym_dir/,
                       MPN.pretty/   → fp_dir/MPN.pretty/,
                       MPN.3dshapes/ → dir_3d/MPN.3dshapes/
    Returns list of (message, tag) tuples for the log.
    """
    msgs = []
    mpn    = Path(output_base).name
    sym_dir = entry_newlib_sym.get().strip()
    fp_dir  = entry_newlib_fp.get().strip()
    dir_3d  = entry_newlib_3d.get().strip()
    var3d   = entry_3dvar.get().strip() or DEFAULT_3D_VAR

    do_sym = "symbol" in import_modes
    do_fp  = "footprint" in import_modes
    do_3d  = "3d" in import_modes

    # ── Symbol ────────────────────────────────────────────────────────────────
    if do_sym:
        if not sym_dir:
            msgs.append((f"  Symbol: {_t('no_sym_dir')}\n", "error"))
        else:
            src = Path(output_base + ".kicad_sym")
            if src.exists():
                dst_dir = Path(sym_dir)
                dst_dir.mkdir(parents=True, exist_ok=True)
                dst = dst_dir / src.name
                if dst.exists() and not var_overwrite.get():
                    msgs.append((f"  Symbol: {_t('sym_exists', name=src.name)}\n", "error"))
                else:
                    shutil.copy2(src, dst)
                    msgs.append((f"  Symbol: {src.name} → {dst_dir.name}\\\n", "ok"))
            else:
                msgs.append((f"  Symbol: {_t('sym_src_missing', name=src.name)}\n", "error"))

    # ── Footprint ─────────────────────────────────────────────────────────────
    if do_fp:
        if not fp_dir:
            msgs.append((f"  Footprint: {_t('no_fp_dir')}\n", "error"))
        else:
            fp_src = Path(output_base + ".pretty")
            if fp_src.exists():
                # Fix 3D paths before copying
                abs_3d_pfx = Path(output_base + ".3dshapes").as_posix() + "/"
                new_3d_pfx = var3d.rstrip("/") + "/" + mpn + ".3dshapes/"
                for kmod in fp_src.glob("*.kicad_mod"):
                    txt = kmod.read_text(encoding="utf-8").replace(
                        f'"{abs_3d_pfx}', f'"{new_3d_pfx}')
                    kmod.write_text(txt, encoding="utf-8")
                fp_dst_parent = Path(fp_dir)
                fp_dst_parent.mkdir(parents=True, exist_ok=True)
                fp_dst = fp_dst_parent / fp_src.name  # MPN.pretty
                if fp_dst.exists() and not var_overwrite.get():
                    msgs.append((f"  Footprint: {_t('sym_exists', name=fp_src.name)}\n", "error"))
                else:
                    if fp_dst.exists():
                        shutil.rmtree(fp_dst)
                    shutil.copytree(fp_src, fp_dst)
                    msgs.append((f"  Footprint: {fp_src.name} → {fp_dst_parent.name}\\\n", "ok"))
            else:
                msgs.append((f"  Footprint: {_t('fp_src_missing', name=fp_src.name)}\n", "error"))

    # ── 3D Models ─────────────────────────────────────────────────────────────
    if do_3d:
        if not dir_3d:
            msgs.append((f"  3D: {_t('no_3d_dir')}\n", "error"))
        else:
            src_3d = Path(output_base + ".3dshapes")
            if src_3d.exists():
                dst_parent = Path(dir_3d)
                dst_parent.mkdir(parents=True, exist_ok=True)
                dst_3d = dst_parent / src_3d.name  # MPN.3dshapes
                if dst_3d.exists() and not var_overwrite.get():
                    msgs.append((f"  3D: {_t('sym_exists', name=src_3d.name)}\n", "error"))
                else:
                    if dst_3d.exists():
                        shutil.rmtree(dst_3d)
                    shutil.copytree(src_3d, dst_3d)
                    msgs.append((f"  3D: {src_3d.name} → {dst_parent.name}\\\n", "ok"))
            else:
                msgs.append((f"  3D: {_t('td_src_missing', name=src_3d.name)}\n", "error"))

    return msgs


# ── UI callbacks ─────────────────────────────────────────────────────────────
_mpn_timer = None
_last_fetched_id = None
_last_jlc: dict = {}
_prev_3dvar = ""  # saved when switching into project-relative mode
_inline_sym_svg: str = ""
_inline_fp_svg: str = ""
_photo_img_url: str = ""
_photo_pil_image = None  # original PIL Image, kept for zoom popup


def _on_projrel_change():
    global _prev_3dvar
    if var_projrel.get():
        _prev_3dvar = entry_3dvar.get()
        entry_3dvar.set("${KIPRJMOD}")
    else:
        entry_3dvar.set(_prev_3dvar or DEFAULT_3D_VAR)


def browse_newlib_sym():
    path = filedialog.askdirectory(
        initialdir=entry_newlib_sym.get() or DEFAULT_OUTPUT_DIR,
        title=_t("browse_newlib_sym"),
    )
    if path:
        entry_newlib_sym.delete(0, tk.END)
        entry_newlib_sym.insert(0, path)
        _save_config()


def browse_newlib_fp():
    path = filedialog.askdirectory(
        initialdir=entry_newlib_fp.get() or DEFAULT_OUTPUT_DIR,
        title=_t("browse_newlib_fp"),
    )
    if path:
        entry_newlib_fp.delete(0, tk.END)
        entry_newlib_fp.insert(0, path)
        _save_config()


def browse_newlib_3d():
    path = filedialog.askdirectory(
        initialdir=entry_newlib_3d.get() or DEFAULT_OUTPUT_DIR,
        title=_t("browse_newlib_3d"),
    )
    if path:
        entry_newlib_3d.delete(0, tk.END)
        entry_newlib_3d.insert(0, path)
        _save_config()


def browse_merge_sym():
    init = str(Path(entry_merge_sym.get()).parent) if entry_merge_sym.get() else DEFAULT_OUTPUT_DIR
    path = filedialog.askopenfilename(
        initialdir=init,
        filetypes=[(_t("filetype_sym"), "*.kicad_sym"), (_t("filetype_all"), "*.*")],
        title=_t("browse_merge_sym"),
    )
    if path:
        entry_merge_sym.delete(0, tk.END)
        entry_merge_sym.insert(0, path)
        _save_config()


def browse_merge_fp():
    path = filedialog.askdirectory(
        initialdir=entry_merge_fp.get() or DEFAULT_OUTPUT_DIR,
        title=_t("browse_merge_fp"),
    )
    if path:
        entry_merge_fp.delete(0, tk.END)
        entry_merge_fp.insert(0, path)
        _save_config()


def browse_merge_3d():
    path = filedialog.askdirectory(
        initialdir=entry_merge_3d.get() or DEFAULT_OUTPUT_DIR,
        title=_t("browse_merge_3d"),
    )
    if path:
        entry_merge_3d.delete(0, tk.END)
        entry_merge_3d.insert(0, path)
        _save_config()


def _on_merge_mode_change():
    if var_merge_mode.get():
        frame_newlib.pack_forget()
        frame_merge.pack(fill=tk.X)
    else:
        frame_merge.pack_forget()
        frame_newlib.pack(fill=tk.X)
    _save_config()


def _on_lcsc_keyrelease(*_):
    global _mpn_timer
    ids = _parse_ids(entry_lcsc.get())
    if len(ids) == 1:
        btn_preview.config(state=tk.NORMAL)
        if _mpn_timer:
            root.after_cancel(_mpn_timer)
        _var_fetch_hint.set("⟳ …")
        _mpn_timer = root.after(700, lambda: _trigger_mpn_fetch(ids[0]))
    else:
        btn_preview.config(state=tk.DISABLED)
        _clear_jlc_info()
        lbl_mpn_large.config(text=_t("batch_mpn", n=len(ids)), foreground=_MUTED)  # type: ignore[name-defined]
        _var_desc.set(_t("batch_hint"))
        lbl_desc_sub.config(foreground=_TEXT)                                       # type: ignore[name-defined]
        canvas_sym_thumb.delete("all")  # type: ignore[name-defined]
        canvas_fp_thumb.delete("all")   # type: ignore[name-defined]


def _trigger_mpn_fetch(lcsc_id: str, force: bool = False):
    global _last_fetched_id
    if lcsc_id == _last_fetched_id and not force:
        return
    _var_mpn.set("…")
    _var_desc.set("…")
    _set_status(_t("status_loading", id=lcsc_id), "loading")
    _dim_card()

    def worker():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fut_info = pool.submit(_fetch_info,   lcsc_id)
            fut_jlc  = pool.submit(_fetch_jlcpcb, lcsc_id)
            name, desc = fut_info.result()
            root.after(0, lambda n=name, d=desc: _apply_info(lcsc_id, n, d))
            jlc = fut_jlc.result()
        root.after(0, lambda j=jlc: _apply_jlc(lcsc_id, j))

    threading.Thread(target=worker, daemon=True).start()


def _apply_info(lcsc_id: str, name: str, desc: str) -> None:
    """Phase 1 (~0.14 s): show name + description as soon as _fetch_info returns."""
    if len(_parse_ids(entry_lcsc.get())) != 1:
        return
    _var_mpn.set(name)
    _var_desc.set(desc)
    lbl_mpn_large.config(text=name, foreground=_TEXT)                          # type: ignore[name-defined]
    lbl_desc_sub.config(foreground=_MUTED)                                     # type: ignore[name-defined]
    lbl_mfr_sub.config(foreground=_MUTED)                                      # type: ignore[name-defined]
    _set_status(_t("status_loading", id=lcsc_id), "loading")


def _apply_jlc(lcsc_id: str, jlc: dict | None) -> None:
    """Phase 2 (~2 s): update chips, links, specs table and preview once JLCPCB data arrives."""
    global _last_fetched_id, _last_jlc
    _last_fetched_id = lcsc_id
    _last_jlc = jlc or {}
    if len(_parse_ids(entry_lcsc.get())) == 1:
        _update_jlc_info(jlc or {}, lcsc_id)
        btn_preview.config(state=tk.NORMAL)
        _update_inline_preview(lcsc_id)
        _set_status(_t("status_ready"))
        _var_fetch_hint.set("← API")


def _set_status(text: str, level: str = "ok") -> None:
    _var_status.set(text)                                                       # type: ignore[name-defined]
    colors = {"ok": _CHIP_STOCK_FG, "loading": _CHIP_EXT_FG,
              "error": "#cc0000", "idle": _MUTED}
    lbl_status_dot.config(foreground=colors.get(level, _MUTED))                # type: ignore[name-defined]


def _load_part_photo(url: str) -> None:
    """Async: fetch product image from URL and render into canvas_photo."""
    global _photo_img_url
    _photo_img_url = url
    def worker():
        try:
            from PIL import Image, ImageTk
            import io, urllib.request
            with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310
                data = r.read()
            img = Image.open(io.BytesIO(data)).convert("RGB")
            global _photo_pil_image
            _photo_pil_image = img.copy()
            img.thumbnail((52, 62))
            photo = ImageTk.PhotoImage(img)
            root.after(0, lambda p=photo: _apply_photo(p))      # type: ignore[name-defined]
        except Exception:
            pass
    threading.Thread(target=worker, daemon=True).start()


def _apply_photo(photo) -> None:                                                # type: ignore[name-defined]
    canvas_photo.delete("all")                                                  # type: ignore[name-defined]
    canvas_photo.image = photo  # keep reference                                # type: ignore[name-defined]
    canvas_photo.create_image(26, 31, image=photo)                              # type: ignore[name-defined]
    canvas_photo.config(cursor="hand2")
    canvas_photo.bind("<Button-1>", lambda *_: _open_photo_popup())


def _open_photo_popup() -> None:
    if _photo_pil_image is None:
        return
    try:
        from PIL import Image, ImageTk
    except ImportError:
        return

    orig = _photo_pil_image
    orig_w, orig_h = orig.size
    try:
        resample = Image.Resampling.LANCZOS  # Pillow ≥9.1
    except AttributeError:
        resample = Image.LANCZOS  # type: ignore[attr-defined]

    MAX_W, MAX_H = 500, 600
    init_scale = min(MAX_W / orig_w, MAX_H / orig_h)
    state = {"scale": init_scale, "photo": None}

    dlg = tk.Toplevel(root)
    dlg.title("Photo")
    dlg.resizable(False, False)

    header = ttk.Frame(dlg)
    header.pack(fill=tk.X, padx=4, pady=(4, 0))
    ttk.Label(header, text="Scroll to zoom · click to close").pack(side=tk.LEFT)
    ttk.Button(header, text="✕", width=3, command=dlg.destroy).pack(side=tk.RIGHT)

    c = tk.Canvas(dlg, bg="#f0f2f5", highlightthickness=0, cursor="hand2")
    c.pack(padx=4, pady=4)

    def _redraw():
        s = state["scale"]
        nw = max(1, int(orig_w * s))
        nh = max(1, int(orig_h * s))
        img = orig.resize((nw, nh), resample)
        photo = ImageTk.PhotoImage(img)
        state["photo"] = photo  # keep reference
        c.config(width=nw, height=nh)
        c.delete("all")
        c.create_image(nw // 2, nh // 2, image=photo)

    def _on_scroll(event):
        delta = 1 if (event.delta > 0 or event.num == 4) else -1
        state["scale"] = max(0.1, min(10.0, state["scale"] * (1.15 ** delta)))
        _redraw()

    c.bind("<Button-1>",  lambda *_: dlg.destroy())
    c.bind("<MouseWheel>", _on_scroll)
    c.bind("<Button-4>",  _on_scroll)
    c.bind("<Button-5>",  _on_scroll)
    dlg.bind("<Escape>",  lambda *_: dlg.destroy())

    _redraw()

    dlg.update_idletasks()
    x = root.winfo_x() + max(0, (root.winfo_width()  - dlg.winfo_reqwidth())  // 2)
    y = root.winfo_y() + max(0, (root.winfo_height() - dlg.winfo_reqheight()) // 2)
    dlg.geometry(f"+{x}+{y}")


def _update_jlc_info(jlc: dict, lcsc_id: str = "") -> None:
    jtype = jlc.get("type", "")
    stock = jlc.get("stock", 0)
    price = jlc.get("price")
    pkg   = jlc.get("package", "")
    brand = jlc.get("brand", "")
    sheet = jlc.get("datasheet", "")
    url   = jlc.get("url", "")
    attrs = jlc.get("attributes", [])

    sub = f"{brand}  ·  LCSC {lcsc_id}" if brand and lcsc_id else (brand or (f"LCSC {lcsc_id}" if lcsc_id else ""))
    lbl_mfr_sub.config(text=sub, foreground=_MUTED)                             # type: ignore[name-defined]

    if jtype == "Basic":
        lbl_chip_type.config(text="● Basic",    bg=_CHIP_STOCK_BG, fg=_CHIP_STOCK_FG)  # type: ignore[name-defined]
    elif jtype == "Extended":
        lbl_chip_type.config(text="◆ Extended", bg=_CHIP_EXT_BG,   fg=_CHIP_EXT_FG)   # type: ignore[name-defined]
    else:
        lbl_chip_type.config(text="",           bg=_CHIP_NEUTRAL_BG, fg=_CHIP_NEUTRAL_FG)  # type: ignore[name-defined]

    lbl_chip_stock.config(text=f"{stock:,} in stock" if stock else "")         # type: ignore[name-defined]
    lbl_chip_pkg.config(text=pkg or "")                                         # type: ignore[name-defined]
    lbl_chip_rohs.config(text="RoHS" if jlc else "")                           # type: ignore[name-defined]
    lbl_chip_price.config(text=f"${float(price):.3f} @1+" if price else "")    # type: ignore[name-defined]

    if sheet:
        btn_datasheet.config(fg=_ACCENT, cursor="hand2")                        # type: ignore[name-defined]
        btn_datasheet.bind("<Button-1>", lambda *_, u=sheet: __import__("webbrowser").open(u))  # type: ignore[name-defined]
    else:
        btn_datasheet.config(fg=_CHIP_NEUTRAL_FG, cursor="")                    # type: ignore[name-defined]
        btn_datasheet.unbind("<Button-1>")                                       # type: ignore[name-defined]

    if url:
        btn_lcsc.config(fg=_ACCENT, cursor="hand2")                             # type: ignore[name-defined]
        btn_lcsc.bind("<Button-1>", lambda *_, u=url: __import__("webbrowser").open(u))  # type: ignore[name-defined]
    else:
        btn_lcsc.config(fg=_CHIP_NEUTRAL_FG, cursor="")                         # type: ignore[name-defined]
        btn_lcsc.unbind("<Button-1>")                                            # type: ignore[name-defined]

    lbl_matched.config(text=_t("lbl_matched") if jlc else _t("lbl_not_found"), # type: ignore[name-defined]
                       foreground=_CHIP_STOCK_FG if jlc else _MUTED)

    for row in tree_specs.get_children():                                       # type: ignore[name-defined]
        tree_specs.delete(row)                                                  # type: ignore[name-defined]
    for i, attr in enumerate(attrs):
        tree_specs.insert("", "end", values=(attr.get("name",""), attr.get("value","")),  # type: ignore[name-defined]
                          tags=("odd",) if i % 2 else ())
    tree_specs.tag_configure("odd", background="#fafbfc")                       # type: ignore[name-defined]

    if url:
        def _fetch_photo():
            try:
                from easyeda2kicad.easyeda.easyeda_api import EasyedaApi
                img_url = EasyedaApi().get_product_image_url(url)
                if img_url:
                    _load_part_photo(img_url)
            except Exception:
                pass
        threading.Thread(target=_fetch_photo, daemon=True).start()


_DIM = "#b8bdc4"  # colour used to grey-out stale card content during loading

def _dim_card() -> None:
    """Grey out the component card while a fetch is in progress."""
    lbl_mpn_large.config(foreground=_DIM)                                        # type: ignore[name-defined]
    lbl_mfr_sub.config(foreground=_DIM)                                          # type: ignore[name-defined]
    lbl_matched.config(foreground=_DIM)                                          # type: ignore[name-defined]
    lbl_desc_sub.config(foreground=_DIM)                                         # type: ignore[name-defined]
    for chip in (lbl_chip_type, lbl_chip_stock, lbl_chip_pkg,   # type: ignore[name-defined]
                 lbl_chip_rohs, lbl_chip_price):                # type: ignore[name-defined]
        chip.config(fg=_DIM)
    for _lnk in (btn_datasheet, btn_lcsc):                      # type: ignore[name-defined]
        _lnk.config(fg=_DIM, cursor="")
        _lnk.unbind("<Button-1>")


def _clear_jlc_info() -> None:
    lbl_mpn_large.config(text="—")                                              # type: ignore[name-defined]
    lbl_mfr_sub.config(text="")                                                 # type: ignore[name-defined]
    lbl_matched.config(text="")                                                 # type: ignore[name-defined]
    for chip in (lbl_chip_type, lbl_chip_stock, lbl_chip_pkg,   # type: ignore[name-defined]
                 lbl_chip_rohs, lbl_chip_price):               # type: ignore[name-defined]
        chip.config(text="", bg=_CHIP_NEUTRAL_BG, fg=_CHIP_NEUTRAL_FG)
    for _lnk in (btn_datasheet, btn_lcsc):                                       # type: ignore[name-defined]
        _lnk.config(fg=_CHIP_NEUTRAL_FG, cursor="")
        _lnk.unbind("<Button-1>")
    for row in tree_specs.get_children():                                       # type: ignore[name-defined]
        tree_specs.delete(row)                                                  # type: ignore[name-defined]
    canvas_photo.delete("all")                                                  # type: ignore[name-defined]
    canvas_photo.create_text(26, 31, text="IMG", fill="#9aa3ad",                # type: ignore[name-defined]
                              font=("Segoe UI", 9))
    canvas_photo.config(cursor="")                                              # type: ignore[name-defined]
    canvas_photo.unbind("<Button-1>")                                           # type: ignore[name-defined]
    global _photo_img_url, _photo_pil_image
    _photo_img_url = ""
    _photo_pil_image = None
    _var_fetch_hint.set("← API")


def _get_modes() -> set:
    """Return set of enabled import types from the checkboxes."""
    modes = set()
    if var_mode_sym.get(): modes.add("symbol")
    if var_mode_fp.get():  modes.add("footprint")
    if var_mode_3d.get():  modes.add("3d")
    return modes


def _run_one(lcsc_id: str, name: str):
    """Import a single component via the easyeda2kicad library API."""
    from easyeda2kicad.easyeda.easyeda_api import EasyedaApi
    from easyeda2kicad.easyeda.easyeda_importer import (
        EasyedaSymbolImporter, EasyedaFootprintImporter, Easyeda3dModelImporter,
    )
    from easyeda2kicad.kicad.export_kicad_symbol import ExporterSymbolKicad
    from easyeda2kicad.kicad.export_kicad_footprint import ExporterFootprintKicad
    from easyeda2kicad.kicad.export_kicad_3d_model import Exporter3dModelKicad

    tmpdir = tempfile.mkdtemp(prefix="lcsc_import_")
    output_base = str(Path(tmpdir) / name)
    modes = _get_modes()

    desc = _fetch_description(lcsc_id)
    desc_str = f"  –  {desc}" if desc else ""
    root.after(0, lambda: log(f"► {lcsc_id}  →  {name}{desc_str}\n", "info"))

    class _GUIHandler(logging.Handler):
        def emit(self, record):
            if not var_verbose.get() and record.levelno < logging.INFO:
                return
            msg = self.format(record)
            tag = "error" if record.levelno >= logging.WARNING else "ok"
            root.after(0, lambda m=msg + "\n", t=tag: log(m, t))

    e2k_log = logging.getLogger("easyeda2kicad")
    handler = _GUIHandler()
    handler.setFormatter(logging.Formatter("  %(levelname)s %(message)s"))
    saved_level = e2k_log.level
    e2k_log.addHandler(handler)
    e2k_log.setLevel(logging.DEBUG if var_debug.get() else logging.INFO)

    try:
        custom_fields = {}
        for pair in entry_custom.get().strip().split():
            if ":" in pair:
                k, v = pair.split(":", 1)
                custom_fields[k] = v

        api = EasyedaApi(use_cache=var_cache.get())
        cad_data = api.get_cad_data_of_component(lcsc_id=lcsc_id)
        if not cad_data:
            root.after(0, lambda: log("  ERROR: component not found in EasyEDA.\n", "error"))
            return

        # ── Symbol ────────────────────────────────────────────────────────────
        if "symbol" in modes:
            sym = EasyedaSymbolImporter(easyeda_cp_cad_data=cad_data).get_symbol()
            ExporterSymbolKicad(
                symbol=sym,
                lib_path=f"{output_base}.kicad_sym",
                custom_fields=custom_fields or None,
            ).save_to_lib(
                lib_path=f"{output_base}.kicad_sym",
                footprint_lib_name=name,
                overwrite=True,
            )

        # ── Footprint ─────────────────────────────────────────────────────────
        if "footprint" in modes:
            fp = EasyedaFootprintImporter(easyeda_cp_cad_data=cad_data).get_footprint()
            fp_dir = Path(f"{output_base}.pretty")
            fp_dir.mkdir(parents=True, exist_ok=True)
            ExporterFootprintKicad(footprint=fp).export(
                footprint_full_path=str(fp_dir / f"{fp.info.name}.kicad_mod"),
                model_3d_path=Path(f"{output_base}.3dshapes").as_posix(),
            )

        # ── 3D model ──────────────────────────────────────────────────────────
        if "3d" in modes:
            model_3d = Easyeda3dModelImporter(
                easyeda_cp_cad_data=cad_data,
                download_raw_3d_model=True,
                api=api,
            ).output
            exp_3d = Exporter3dModelKicad(model_3d=model_3d)
            if exp_3d.output:
                exp_3d.export(output_dir=f"{output_base}.3dshapes", overwrite=True)
            else:
                root.after(0, lambda: log("  INFO: no 3D model available.\n", "ok"))

        # ── Post-process & distribute ─────────────────────────────────────────
        if desc:
            _patch_description(output_base, desc)
        post_msgs = (merge_into_libs if var_merge_mode.get() else distribute_new_lib)(
            output_base, modes)
        for msg, t in post_msgs:
            root.after(0, lambda m=msg, t=t: log(m, t))

    except Exception as exc:
        root.after(0, lambda e=exc: log(f"  ERROR: {e}\n", "error"))
    finally:
        e2k_log.removeHandler(handler)
        e2k_log.setLevel(saved_level)
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Dry run ───────────────────────────────────────────────────────────────────
def _dry_run_one(lcsc_id: str, name: str, modes: set, jlc: dict | None = None):
    """Log what would happen for one component without writing any files."""
    log(f"► {lcsc_id}  →  {name}\n", "info")

    jlc = jlc or {}
    brand   = jlc.get("brand", "")
    desc    = jlc.get("description", "")
    jtype   = jlc.get("type", "")
    stock   = jlc.get("stock", 0)
    price   = jlc.get("price")
    pkg     = jlc.get("package", "")

    if brand or jtype or stock or price:
        type_str  = f"{'● Basic' if jtype == 'Basic' else '◆ Extended' if jtype == 'Extended' else jtype}"
        stock_str = f"{stock:,} in stock" if stock else ""
        price_str = f"${float(price):.3f} @1+" if price else ""
        pkg_str   = pkg or ""
        parts = [p for p in [brand, type_str, pkg_str, stock_str, price_str] if p]
        log(f"  {' · '.join(parts)}\n", "info")
    if desc:
        log(f"  {desc}\n", "info")

    ow = var_overwrite.get()
    do_sym = "symbol" in modes
    do_fp  = "footprint" in modes
    do_3d  = "3d" in modes

    def _check(label: str, path: Path):
        if path.exists():
            if ow:
                log(f"  {label}: ⚠ {_t('dryrun_overwrite')}: {path.name}\n", "info")
            else:
                log(f"  {label}: ✗ {_t('dryrun_skip')}: {path.name}\n", "error")
        else:
            log(f"  {label}: ✓ {_t('dryrun_create')}: {path.name}\n", "ok")

    if var_merge_mode.get():
        sym_lib = entry_merge_sym.get().strip()
        fp_lib  = entry_merge_fp.get().strip()
        dir_3d  = entry_merge_3d.get().strip()

        if do_sym:
            if not sym_lib:
                log(f"  Symbol: – {_t('no_sym_dir')}\n", "error")
            else:
                p = Path(sym_lib)
                if p.exists():
                    try:
                        in_lib = f'(symbol "{name}"' in p.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        in_lib = False
                    if in_lib:
                        action = _t("dryrun_overwrite") if ow else _t("dryrun_skip")
                        tag = "info" if ow else "error"
                        icon = "⚠" if ow else "✗"
                        log(f"  Symbol: {icon} {action}: '{name}' in {p.name}\n", tag)
                    else:
                        log(f"  Symbol: ✓ {_t('dryrun_create')}: '{name}' → {p.name}\n", "ok")
                else:
                    log(f"  Symbol: ✓ {_t('dryrun_create')} ({_t('dryrun_new_lib')}): {p.name}\n", "ok")

        if do_fp:
            if not fp_lib:
                log(f"  Footprint: – {_t('no_fp_dir')}\n", "error")
            else:
                p = Path(fp_lib)
                n_ex = len(list(p.glob("*.kicad_mod"))) if p.exists() else 0
                log(f"  Footprint: ✓ → {p.name}  ({n_ex} existing files)\n", "ok")

        if do_3d:
            if not dir_3d:
                log(f"  3D: – {_t('no_3d_dir')}\n", "error")
            else:
                p = Path(dir_3d)
                n_ex = sum(1 for f in p.iterdir() if f.is_file()) if p.exists() else 0
                log(f"  3D: ✓ → {p.name}  ({n_ex} existing files)\n", "ok")
    else:
        sym_dir = entry_newlib_sym.get().strip()
        fp_dir  = entry_newlib_fp.get().strip()
        dir_3d  = entry_newlib_3d.get().strip()

        if do_sym:
            if not sym_dir:
                log(f"  Symbol: – {_t('no_sym_dir')}\n", "error")
            else:
                _check("Symbol", Path(sym_dir) / f"{name}.kicad_sym")

        if do_fp:
            if not fp_dir:
                log(f"  Footprint: – {_t('no_fp_dir')}\n", "error")
            else:
                _check("Footprint", Path(fp_dir) / f"{name}.pretty")

        if do_3d:
            if not dir_3d:
                log(f"  3D: – {_t('no_3d_dir')}\n", "error")
            else:
                _check("3D", Path(dir_3d) / f"{name}.3dshapes")


def _log_dryrun_params(modes: set) -> None:
    """Log a compact parameter summary block for the dry-run header."""
    tick = lambda v: "✓" if v else "✗"   # noqa: E731

    # Import types
    type_parts = []
    if "symbol"    in modes: type_parts.append("Symbol")
    if "footprint" in modes: type_parts.append("Footprint")
    if "3d"        in modes: type_parts.append("3D model")
    log(f"  Import:    {', '.join(type_parts)}\n", "info")

    # Output mode + paths
    if var_merge_mode.get():
        log("  Mode:      Merge into existing library\n", "info")
        if "symbol"    in modes: log(f"  Sym-lib:   {entry_merge_sym.get().strip() or '—'}\n", "info")
        if "footprint" in modes: log(f"  FP-lib:    {entry_merge_fp.get().strip() or '—'}\n",  "info")
        if "3d"        in modes: log(f"  3D-dir:    {entry_merge_3d.get().strip() or '—'}\n",  "info")
    else:
        log("  Mode:      New library\n", "info")
        if "symbol"    in modes: log(f"  Sym-dir:   {entry_newlib_sym.get().strip() or '—'}\n", "info")
        if "footprint" in modes: log(f"  FP-dir:    {entry_newlib_fp.get().strip() or '—'}\n",  "info")
        if "3d"        in modes: log(f"  3D-dir:    {entry_newlib_3d.get().strip() or '—'}\n",  "info")

    # 3D variable
    v3d = entry_3dvar.get().strip()
    if v3d and "3d" in modes:
        log(f"  3D var:    {v3d}\n", "info")

    # Options
    opts = (
        f"Overwrite {tick(var_overwrite.get())}  "
        f"Cache {tick(var_cache.get())}  "
        f"Proj-rel {tick(var_projrel.get())}  "
        f"Debug {tick(var_debug.get())}"
    )
    log(f"  Options:   {opts}\n", "info")

    # Custom fields
    cf = entry_custom.get().strip()
    if cf:
        log(f"  Custom:    {cf}\n", "info")


def dry_run():
    raw_input = entry_lcsc.get().strip()
    ids = _parse_ids(raw_input)
    if not ids:
        log(_t("err_no_id"), "error")
        return
    modes = _get_modes()
    if not modes:
        log(_t("err_no_modes"), "error")
        return

    sep = "─" * 56
    log(f"{sep}\n", "info")
    log(f"{_t('dryrun_header')}\n", "info")
    log(f"{sep}\n", "info")
    _log_dryrun_params(modes)
    log(f"{sep}\n", "info")

    raw_count = len([p for p in re.split(r"[,;\s]+", raw_input.strip()) if p])
    if raw_count > len(ids):
        log(_t("info_dups", n=raw_count - len(ids)), "info")

    if len(ids) == 1:
        name = _var_mpn.get().strip() or ids[0]
        jlc  = _last_jlc if _last_fetched_id == ids[0] else {}
        _dry_run_one(ids[0], name, modes, jlc)
        log(f"{sep}\n", "info")
        log(f"{_t('dryrun_footer')}\n", "info")
        log(f"{sep}\n", "info")
    else:
        _start_batch_dryrun(ids, modes, sep)


def run_import():
    raw_input = entry_lcsc.get().strip()
    ids = _parse_ids(raw_input)

    if not ids:
        log(_t("err_no_id"), "error")
        return

    if not _get_modes():
        log(_t("err_no_modes"), "error")
        return

    # Duplicate info
    raw_count = len([p for p in re.split(r"[,;\s]+", raw_input.strip()) if p])
    if raw_count > len(ids):
        log(_t("info_dups", n=raw_count - len(ids)), "info")

    btn_run.config(state=tk.DISABLED)
    btn_dry_run.config(state=tk.DISABLED)

    if len(ids) == 1:
        name = _var_mpn.get().strip() or ids[0]
        threading.Thread(
            target=_batch_worker, args=([ids[0]], {ids[0]: name}), daemon=True
        ).start()
    else:
        # Batch: fetch all MPNs first, then show confirm dialog with the list
        _start_batch_resolve(ids)


def _start_batch_dryrun(ids: list, modes: set, sep: str) -> None:
    """Resolve MPNs for all IDs in background, then log dry-run results."""
    dlg = tk.Toplevel(root)
    dlg.title(_t("dlg_loading_title"))
    dlg.resizable(False, False)
    dlg.grab_set()
    ttk.Label(dlg, text=_t("dlg_loading_msg", n=len(ids)),
              padding=(20, 14, 20, 6)).pack()
    pb = ttk.Progressbar(dlg, mode="indeterminate", length=260)
    pb.pack(padx=20, pady=(0, 16))
    pb.start(10)
    dlg.update_idletasks()
    x = root.winfo_x() + (root.winfo_width()  - dlg.winfo_reqwidth())  // 2
    y = root.winfo_y() + (root.winfo_height() - dlg.winfo_reqheight()) // 2
    dlg.geometry(f"+{x}+{y}")

    id_name: dict = {}
    id_jlc:  dict = {}

    def fetch_all():
        for lcsc_id in ids:
            mpn = _fetch_mpn(lcsc_id)
            id_name[lcsc_id] = mpn if mpn else lcsc_id
            id_jlc[lcsc_id]  = _fetch_jlcpcb(lcsc_id)
        root.after(0, lambda: _finish_batch_dryrun(dlg, ids, id_name, id_jlc, modes, sep))

    threading.Thread(target=fetch_all, daemon=True).start()


def _finish_batch_dryrun(dlg: tk.Toplevel, ids: list, id_name: dict,
                          id_jlc: dict, modes: set, sep: str) -> None:
    dlg.destroy()
    for lcsc_id in ids:
        _dry_run_one(lcsc_id, id_name[lcsc_id], modes, id_jlc.get(lcsc_id))
    log(f"{sep}\n", "info")
    log(f"{_t('dryrun_footer')}\n", "info")
    log(f"{sep}\n", "info")


def _start_batch_resolve(ids: list):
    """Show loading indicator while fetching MPNs, then open confirm dialog."""
    dlg = tk.Toplevel(root)
    dlg.title(_t("dlg_loading_title"))
    dlg.resizable(False, False)
    dlg.grab_set()
    ttk.Label(dlg, text=_t("dlg_loading_msg", n=len(ids)),
              padding=(20, 14, 20, 6)).pack()
    pb = ttk.Progressbar(dlg, mode="indeterminate", length=260)
    pb.pack(padx=20, pady=(0, 16))
    pb.start(10)
    dlg.update_idletasks()
    x = root.winfo_x() + (root.winfo_width()  - dlg.winfo_reqwidth())  // 2
    y = root.winfo_y() + (root.winfo_height() - dlg.winfo_reqheight()) // 2
    dlg.geometry(f"+{x}+{y}")

    id_name: dict = {}

    def fetch_all():
        for lcsc_id in ids:
            mpn = _fetch_mpn(lcsc_id)
            id_name[lcsc_id] = mpn if mpn else lcsc_id
        root.after(0, lambda: _show_batch_confirm(dlg, ids, id_name))

    threading.Thread(target=fetch_all, daemon=True).start()


def _show_batch_confirm(loading_dlg: tk.Toplevel, ids: list, id_name: dict):
    """Close loading dialog and show the styled confirmation list."""
    loading_dlg.destroy()

    dlg = tk.Toplevel(root)
    dlg.title(_t("dlg_confirm_title"))
    dlg.resizable(False, False)
    dlg.grab_set()

    ttk.Label(dlg, text=_t("dlg_confirm_count", n=len(ids)),
              padding=(12, 10, 12, 2)).pack(anchor="w")

    frame_list = ttk.Frame(dlg, relief="sunken", borderwidth=1)
    frame_list.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

    listbox = tk.Listbox(
        frame_list, width=54, height=min(len(ids), 14),
        selectmode=tk.NONE, font=("Consolas", 9), activestyle="none",
    )
    sb = ttk.Scrollbar(frame_list, orient=tk.VERTICAL, command=listbox.yview)
    listbox.config(yscrollcommand=sb.set)
    sb.pack(side=tk.RIGHT, fill=tk.Y)
    listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    for lcsc_id in ids:
        mpn = id_name[lcsc_id]
        suffix = _t("warn_no_name") if mpn == lcsc_id else ""
        listbox.insert(tk.END, f"  {lcsc_id:<12}  →  {mpn}{suffix}")

    ttk.Label(dlg, text=_t("dlg_confirm_q"), padding=(12, 6, 12, 2)).pack(anchor="w")

    frame_btn = ttk.Frame(dlg)
    frame_btn.pack(pady=(4, 12))

    confirmed = [False]

    def on_yes():
        confirmed[0] = True
        dlg.destroy()

    ttk.Button(frame_btn, text=_t("btn_yes"), command=on_yes).pack(side=tk.LEFT, padx=8)
    ttk.Button(frame_btn, text=_t("btn_cancel"), command=dlg.destroy).pack(side=tk.LEFT, padx=8)

    dlg.update_idletasks()
    x = root.winfo_x() + (root.winfo_width()  - dlg.winfo_reqwidth())  // 2
    y = root.winfo_y() + (root.winfo_height() - dlg.winfo_reqheight()) // 2
    dlg.geometry(f"+{x}+{y}")

    dlg.wait_window()

    if confirmed[0]:
        threading.Thread(
            target=_batch_worker, args=(ids, id_name), daemon=True
        ).start()
    else:
        root.after(0, lambda: _re_enable_buttons())


def _re_enable_buttons():
    btn_run.config(state=tk.NORMAL)
    btn_dry_run.config(state=tk.NORMAL)


def _batch_worker(ids: list, id_name: dict):
    """Run imports sequentially. Called from a background thread."""
    for lcsc_id in ids:
        _run_one(lcsc_id, id_name[lcsc_id])
    root.after(0, _re_enable_buttons)


def _svg_on_canvas(canvas: tk.Canvas, svg_text: str, canvas_w: int = 380, canvas_h: int = 380):
    """Render an easyeda2kicad SVG onto a Canvas (no extra dependencies)."""
    import xml.etree.ElementTree as ET
    import math

    root_el = ET.fromstring(svg_text)
    vb = root_el.get("viewBox", "")
    if vb:
        vb_parts = re.split(r"[,\s]+", vb.strip())
        vb_x, vb_y, vb_w, vb_h = float(vb_parts[0]), float(vb_parts[1]), float(vb_parts[2]), float(vb_parts[3])
    else:
        vb_x, vb_y = 0.0, 0.0
        vb_w = float(root_el.get("width",  400))
        vb_h = float(root_el.get("height", 300))

    if vb_w <= 0 or vb_h <= 0:
        return
    margin = 12
    scale = min((canvas_w - 2 * margin) / vb_w, (canvas_h - 2 * margin) / vb_h)
    ox = margin - vb_x * scale
    oy = margin - vb_y * scale

    def px(x): return ox + float(x) * scale
    def py(y): return oy + float(y) * scale
    def sw(v):
        try: return max(1.0, float(v) * scale)
        except (TypeError, ValueError): return 1.0

    def col(s, default=""):
        if not s or s == "none":
            return default
        if s.startswith("#") and len(s) == 9:   # #RRGGBBAA → strip alpha
            s = s[:7]
        return s

    # SVG text y = baseline (text body above it).
    # tkinter "sw/s/se" = bottom of bbox at y → text sits above y, approximating SVG baseline.
    _ANCHOR = {"start": "sw", "middle": "s", "end": "se"}

    def _parse_rotate(t):
        """Parse rotate(angle, cx, cy) → (angle_deg, cx, cy) or None."""
        m = re.search(r"rotate\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)", t)
        if m:
            return float(m.group(1)), float(m.group(2)), float(m.group(3))
        return None

    def _rot_pts(pts, angle_deg, cx, cy):
        a = math.radians(angle_deg)
        ca, sa = math.cos(a), math.sin(a)
        return [(cx + (x - cx) * ca - (y - cy) * sa,
                 cy + (x - cx) * sa + (y - cy) * ca) for x, y in pts]

    def render_path(d, stroke, fill, width):
        """Tokenising path parser with fill support for closed subpaths."""
        tokens = re.findall(r"[MmLlHhVvZzCcQqAa]|[-+]?\d*\.?\d+(?:[eE][+-]?\d+)?", d)
        idx = 0
        cx_pos = cy_pos = sx = sy = 0.0
        cmd = "M"
        sub_pts: list[tuple[float, float]] = []

        def nf():
            nonlocal idx
            v = float(tokens[idx]); idx += 1; return v

        def seg(x1, y1, x2, y2):
            if stroke:
                canvas.create_line(px(x1), py(y1), px(x2), py(y2), fill=stroke, width=width,
                                   capstyle=tk.ROUND)
            sub_pts.append((x2, y2))

        while idx < len(tokens):
            t = tokens[idx]
            if t.isalpha():
                cmd = t; idx += 1; continue
            try:
                if cmd == "M":
                    cx_pos, cy_pos = nf(), nf(); sx, sy = cx_pos, cy_pos
                    sub_pts[:] = [(cx_pos, cy_pos)]; cmd = "L"
                elif cmd == "m":
                    cx_pos += nf(); cy_pos += nf(); sx, sy = cx_pos, cy_pos
                    sub_pts[:] = [(cx_pos, cy_pos)]; cmd = "l"
                elif cmd == "L":
                    nx, ny = nf(), nf(); seg(cx_pos, cy_pos, nx, ny); cx_pos, cy_pos = nx, ny
                elif cmd == "l":
                    dx, dy = nf(), nf(); seg(cx_pos, cy_pos, cx_pos+dx, cy_pos+dy); cx_pos += dx; cy_pos += dy
                elif cmd == "H":
                    nx = nf(); seg(cx_pos, cy_pos, nx, cy_pos); cx_pos = nx
                elif cmd == "h":
                    dx = nf(); seg(cx_pos, cy_pos, cx_pos+dx, cy_pos); cx_pos += dx
                elif cmd == "V":
                    ny = nf(); seg(cx_pos, cy_pos, cx_pos, ny); cy_pos = ny
                elif cmd == "v":
                    dy = nf(); seg(cx_pos, cy_pos, cx_pos, cy_pos+dy); cy_pos += dy
                elif cmd in ("Z", "z"):
                    seg(cx_pos, cy_pos, sx, sy); cx_pos, cy_pos = sx, sy
                    if fill and len(sub_pts) >= 3:
                        flat = [c for p in sub_pts for c in (px(p[0]), py(p[1]))]
                        canvas.create_polygon(*flat, fill=fill, outline="", smooth=False)
                    sub_pts[:] = [(sx, sy)]
                elif cmd == "C":
                    nf(); nf(); nf(); nf(); ex, ey = nf(), nf()
                    seg(cx_pos, cy_pos, ex, ey); cx_pos, cy_pos = ex, ey
                elif cmd == "c":
                    nf(); nf(); nf(); nf(); dx, dy = nf(), nf()
                    seg(cx_pos, cy_pos, cx_pos+dx, cy_pos+dy); cx_pos += dx; cy_pos += dy
                elif cmd == "A":
                    nf(); nf(); nf(); nf(); nf(); ex, ey = nf(), nf()
                    seg(cx_pos, cy_pos, ex, ey); cx_pos, cy_pos = ex, ey
                elif cmd == "a":
                    nf(); nf(); nf(); nf(); nf(); dx, dy = nf(), nf()
                    seg(cx_pos, cy_pos, cx_pos+dx, cy_pos+dy); cx_pos += dx; cy_pos += dy
                elif cmd in ("Q", "q"):
                    nf(); nf()
                    if cmd == "Q":
                        ex, ey = nf(), nf(); seg(cx_pos, cy_pos, ex, ey); cx_pos, cy_pos = ex, ey
                    else:
                        dx, dy = nf(), nf(); seg(cx_pos, cy_pos, cx_pos+dx, cy_pos+dy); cx_pos += dx; cy_pos += dy
                else:
                    idx += 1
            except (IndexError, ValueError):
                break

    def render_el(el):
        tag = el.tag.split("}")[-1]
        if tag in ("title", "defs"):
            return

        fill   = col(el.get("fill"))
        stroke = col(el.get("stroke"))   # "" when not explicitly set → no outline
        sw_raw = el.get("stroke-width")
        width  = sw(sw_raw) if sw_raw else max(1.0, scale * 0.5)

        tf = el.get("transform", "")
        rot = _parse_rotate(tf) if tf else None

        if tag == "rect":
            x, y = float(el.get("x", 0)), float(el.get("y", 0))
            w, h = float(el.get("width", 0)), float(el.get("height", 0))
            if rot:
                corners = _rot_pts([(x, y), (x+w, y), (x+w, y+h), (x, y+h)], *rot)
                flat = [c for p in corners for c in (px(p[0]), py(p[1]))]
                canvas.create_polygon(*flat, fill=fill or "", outline=stroke,
                                      width=width if stroke else 0)
            else:
                canvas.create_rectangle(px(x), py(y), px(x+w), py(y+h),
                                        fill=fill or "", outline=stroke, width=width if stroke else 0)
        elif tag in ("circle", "ellipse"):
            cx, cy = float(el.get("cx", 0)), float(el.get("cy", 0))
            r_attr = el.get("r")
            rx = float(r_attr if r_attr else el.get("rx", 2))
            ry = float(r_attr if r_attr else el.get("ry", rx))
            if abs(rx - ry) < 0.01:
                # True circle
                canvas.create_oval(px(cx-rx), py(cy-ry), px(cx+rx), py(cy+ry),
                                   fill=fill or "", outline=stroke, width=width if stroke else 0)
            else:
                # Oblong/stadium pad: single polygon tracing both semicircle caps.
                # Avoids seam artifacts that occur when compositing 3 separate shapes.
                r = min(rx, ry)
                N = 16  # points per semicircle
                pts = []
                if rx > ry:  # horizontal
                    for i in range(N + 1):
                        a = -math.pi/2 + math.pi * i / N
                        pts.append((cx + (rx - r) + r * math.cos(a), cy + r * math.sin(a)))
                    for i in range(N + 1):
                        a = math.pi/2 + math.pi * i / N
                        pts.append((cx - (rx - r) + r * math.cos(a), cy + r * math.sin(a)))
                else:  # vertical
                    for i in range(N + 1):
                        a = math.pi * i / N
                        pts.append((cx + r * math.cos(a), cy + (ry - r) + r * math.sin(a)))
                    for i in range(N + 1):
                        a = math.pi + math.pi * i / N
                        pts.append((cx + r * math.cos(a), cy - (ry - r) + r * math.sin(a)))
                if rot:
                    pts = _rot_pts(pts, rot[0], rot[1], rot[2])
                coords = [c for p in pts for c in (px(p[0]), py(p[1]))]
                canvas.create_polygon(*coords, fill=fill or "", outline=stroke or "",
                                      width=width if stroke else 0, smooth=False)
        elif tag == "line":
            lc = stroke or "black"
            canvas.create_line(
                px(float(el.get("x1", 0))), py(float(el.get("y1", 0))),
                px(float(el.get("x2", 0))), py(float(el.get("y2", 0))),
                fill=lc, width=width, capstyle=tk.ROUND)
        elif tag in ("polyline", "polygon"):
            pts = [float(v) for v in re.split(r"[,\s]+", el.get("points", "").strip()) if v]
            if len(pts) >= 4:
                coords = [c for i in range(0, len(pts)//2*2, 2)
                          for c in (px(pts[i]), py(pts[i+1]))]
                if tag == "polygon":
                    canvas.create_polygon(*coords, fill=fill or "", outline=stroke, width=width if stroke else 0)
                else:
                    canvas.create_line(*coords, fill=stroke or "black", width=width,
                                       capstyle=tk.ROUND, joinstyle=tk.ROUND)
        elif tag == "path":
            render_path(el.get("d", ""), stroke, fill, width)
        elif tag == "text":
            text = (el.text or "").strip()
            if text:
                ta = el.get("text-anchor", "start")
                db = el.get("dominant-baseline", "")
                if db in ("central", "middle"):
                    # SVG: both axes centered → use center/w/e without vertical offset
                    anchor = {"middle": "center", "start": "w", "end": "e"}.get(ta, "center")
                else:
                    # SVG y = baseline → text body above y; "sw/s/se" puts bottom of bbox at y
                    anchor = _ANCHOR.get(ta, "sw")
                fs_svg = float(el.get("font-size") or 7)
                fs     = max(6, min(9, int(fs_svg * scale)))
                t_str  = el.get("transform", "")
                angle  = 0.0
                if t_str:
                    rm = _parse_rotate(t_str)
                    if rm:
                        angle = -rm[0]   # SVG rotate(+α) CW; tkinter angle(+α) CCW
                canvas.create_text(px(float(el.get("x", 0))), py(float(el.get("y", 0))),
                                   text=text, fill=fill or stroke or "black",
                                   font=("TkDefaultFont", fs), anchor=anchor, angle=angle)
        elif tag == "g":
            for child in el:
                render_el(child)

    def _is_white(el):
        f = col(el.get("fill", "")).lower()
        s = col(el.get("stroke", "")).lower()
        return f in ("white", "#ffffff") or s in ("white", "#ffffff")

    children = list(root_el)
    # Pass 0 — background rect (always first child of <svg>)
    if children:
        render_el(children[0])
    # Pass 1 — non-white filled paths (SOLIDREGION copper fills), under pads
    for el in children[1:]:
        if el.tag.split("}")[-1] == "path" and not _is_white(el):
            render_el(el)
    # Pass 2 — pads, tracks, outlines, text — skip white-fill elements
    for el in children[1:]:
        if el.tag.split("}")[-1] != "path" and not _is_white(el):
            render_el(el)
    # Pass 3 — white-fill elements last: drill holes, slot holes, HOLE, npth paths
    for el in children[1:]:
        if _is_white(el):
            render_el(el)


THUMB_W, THUMB_H = 160, 160


def _update_inline_preview(lcsc_id: str):
    """Fetch SVGs in background and paint the two thumbnail canvases."""
    def worker():
        try:
            from easyeda2kicad.easyeda.easyeda_api import EasyedaApi
            from easyeda2kicad.easyeda.easyeda_svg_renderer import (
                render_symbol_svg, render_footprint_svg,
            )
            api = EasyedaApi(use_cache=var_cache.get())
            cad_data = api.get_cad_data_of_component(lcsc_id=lcsc_id)
            if not cad_data:
                return
            sym = render_symbol_svg(cad_data)
            fp  = render_footprint_svg(cad_data)
            root.after(0, lambda s=sym, f=fp: _apply_inline_preview(lcsc_id, s, f))
        except Exception:
            pass
    threading.Thread(target=worker, daemon=True).start()


def _apply_inline_preview(lcsc_id: str, sym_svg: str, fp_svg: str):
    global _inline_sym_svg, _inline_fp_svg
    if _parse_ids(entry_lcsc.get()) != [lcsc_id]:
        return  # stale: user already changed the input
    _inline_sym_svg = sym_svg
    _inline_fp_svg  = fp_svg
    canvas_sym_thumb.delete("all")  # type: ignore[name-defined]
    canvas_fp_thumb.delete("all")   # type: ignore[name-defined]
    _svg_on_canvas(canvas_sym_thumb, sym_svg, THUMB_W, THUMB_H)  # type: ignore[name-defined]
    _svg_on_canvas(canvas_fp_thumb,  fp_svg,  THUMB_W, THUMB_H)  # type: ignore[name-defined]


def _open_large_preview(which: str):
    """Open a single enlarged preview popup; click or Escape closes it."""
    svg = _inline_sym_svg if which == "sym" else _inline_fp_svg
    if not svg:
        return
    bg    = "white" if which == "sym" else "black"
    label = "Symbol"    if which == "sym" else "Footprint"

    dlg = tk.Toplevel(root)
    dlg.title(label)
    dlg.resizable(False, False)

    header = ttk.Frame(dlg)
    header.pack(fill=tk.X, padx=4, pady=(4, 0))
    ttk.Label(header, text=label).pack(side=tk.LEFT)
    ttk.Button(header, text="✕", width=3, command=dlg.destroy).pack(side=tk.RIGHT)

    c = tk.Canvas(dlg, width=380, height=380, bg=bg, highlightthickness=0,
                  cursor="hand2")
    c.pack(padx=4, pady=4)
    _svg_on_canvas(c, svg, 380, 380)
    c.bind("<Button-1>", lambda *_: dlg.destroy())
    dlg.bind("<Escape>",  lambda *_: dlg.destroy())

    dlg.update_idletasks()
    x = root.winfo_x() + max(0, (root.winfo_width()  - dlg.winfo_reqwidth())  // 2)
    y = root.winfo_y() + max(0, (root.winfo_height() - dlg.winfo_reqheight()) // 2)
    dlg.geometry(f"+{x}+{y}")


def _show_preview():
    """Fetch SVGs for the current single LCSC ID and render inline."""
    ids = _parse_ids(entry_lcsc.get())
    if len(ids) != 1:
        return
    lcsc_id = ids[0]
    name = _var_mpn.get().strip() or lcsc_id

    btn_preview.config(state=tk.DISABLED)
    log(_t("preview_loading", lcsc_id=lcsc_id), "info")

    def worker():
        try:
            from easyeda2kicad.easyeda.easyeda_api import EasyedaApi
            from easyeda2kicad.easyeda.easyeda_svg_renderer import (
                render_symbol_svg, render_footprint_svg,
            )
            api = EasyedaApi(use_cache=var_cache.get())
            cad_data = api.get_cad_data_of_component(lcsc_id=lcsc_id)
            if not cad_data:
                root.after(0, lambda: log(_t("preview_not_found"), "error"))
                return
            sym_svg = render_symbol_svg(cad_data)
            fp_svg  = render_footprint_svg(cad_data)
            root.after(0, lambda s=sym_svg, f=fp_svg: _open_preview_window(lcsc_id, name, s, f))
        except Exception as exc:
            root.after(0, lambda e=exc: log(_t("preview_error", error=e), "error"))
        finally:
            root.after(0, lambda: btn_preview.config(state=tk.NORMAL))

    threading.Thread(target=worker, daemon=True).start()


def _open_preview_window(lcsc_id: str, name: str, sym_svg: str, fp_svg: str):
    dlg = tk.Toplevel(root)
    dlg.title(f"Preview: {lcsc_id} — {name}")
    dlg.resizable(True, True)

    CANVAS_W, CANVAS_H = 380, 380
    PHOTO_W, PHOTO_H   = 200, 380

    frame = ttk.Frame(dlg, padding=10)
    frame.pack(fill=tk.BOTH, expand=True)

    bg_colors = ["white", "black"]
    for col_idx, (label, svg) in enumerate([("Symbol", sym_svg), ("Footprint", fp_svg)]):
        panel = ttk.LabelFrame(frame, text=label, padding=4)
        panel.grid(row=0, column=col_idx, padx=(0, 8), sticky="nsew")
        frame.columnconfigure(col_idx, weight=1)
        frame.rowconfigure(0, weight=1)
        c = tk.Canvas(panel, width=CANVAS_W, height=CANVAS_H, bg=bg_colors[col_idx],
                      highlightthickness=0)
        c.pack(fill=tk.BOTH, expand=True)
        _svg_on_canvas(c, svg, CANVAS_W, CANVAS_H)

    # Photo panel (third column, fixed width)
    photo_panel = ttk.LabelFrame(frame, text="Photo", padding=4)
    photo_panel.grid(row=0, column=2, sticky="nsew")
    frame.columnconfigure(2, weight=0)
    canvas_prev_photo = tk.Canvas(photo_panel, width=PHOTO_W, height=PHOTO_H,
                                  bg="#f0f2f5", highlightthickness=0)
    canvas_prev_photo.pack(fill=tk.BOTH, expand=True)
    canvas_prev_photo.create_text(PHOTO_W // 2, PHOTO_H // 2, text="Loading…",
                                  fill="#9aa3ad", font=("Segoe UI", 10))

    if _photo_img_url:
        def _load_preview_photo(url: str, c: tk.Canvas, w: int, h: int):
            def worker():
                try:
                    from PIL import Image, ImageTk
                    import io, urllib.request
                    with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310
                        data = r.read()
                    img = Image.open(io.BytesIO(data)).convert("RGB")
                    img.thumbnail((w, h))
                    photo = ImageTk.PhotoImage(img)
                    def apply(p=photo):
                        c.delete("all")
                        c.image = p  # type: ignore[attr-defined]
                        c.create_image(w // 2, h // 2, image=p)
                    root.after(0, apply)
                except Exception:
                    root.after(0, lambda: (c.delete("all"), c.create_text(
                        w // 2, h // 2, text="No image",
                        fill="#9aa3ad", font=("Segoe UI", 10))))
            threading.Thread(target=worker, daemon=True).start()

        _load_preview_photo(_photo_img_url, canvas_prev_photo, PHOTO_W, PHOTO_H)
    else:
        canvas_prev_photo.delete("all")
        canvas_prev_photo.create_text(PHOTO_W // 2, PHOTO_H // 2, text="No image",
                                      fill="#9aa3ad", font=("Segoe UI", 10))

    dlg.update_idletasks()
    x = root.winfo_x() + max(0, (root.winfo_width()  - dlg.winfo_reqwidth())  // 2)
    y = root.winfo_y() + max(0, (root.winfo_height() - dlg.winfo_reqheight()) // 2)
    dlg.geometry(f"+{x}+{y}")


def _show_about():
    dlg = tk.Toplevel(root)
    dlg.title(_t("dlg_about_title"))
    dlg.resizable(False, False)
    dlg.grab_set()
    ttk.Label(dlg, text="LCSC → KiCad Importer",
              font=("TkDefaultFont", 12, "bold"), padding=(16, 12, 16, 4)).pack()
    ttk.Label(dlg, text=f"v{APP_VERSION}", foreground="gray",
              padding=(16, 0, 16, 8)).pack()
    ttk.Label(dlg, text=_t("about_desc"), justify=tk.CENTER,
              padding=(16, 0, 16, 8)).pack()
    ttk.Separator(dlg, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=4)
    frame_src = ttk.Frame(dlg)
    frame_src.pack(padx=16, pady=(0, 4))
    ttk.Label(frame_src, text=_t("about_source")).pack(side=tk.LEFT, padx=(0, 6))
    lbl_url = ttk.Label(frame_src, text=APP_URL, foreground="#0066cc", cursor="hand2")
    lbl_url.pack(side=tk.LEFT)
    lbl_url.bind("<Button-1>", lambda _: webbrowser.open(APP_URL))
    ttk.Label(dlg, text=_t("about_license"), foreground="gray",
              padding=(16, 0, 16, 12)).pack()
    ttk.Button(dlg, text="OK", command=dlg.destroy, width=8).pack(pady=(0, 12))
    dlg.update_idletasks()
    x = root.winfo_x() + (root.winfo_width()  - dlg.winfo_reqwidth())  // 2
    y = root.winfo_y() + (root.winfo_height() - dlg.winfo_reqheight()) // 2
    dlg.geometry(f"+{x}+{y}")


def log(msg, tag="normal"):
    text_log.config(state=tk.NORMAL)
    text_log.insert(tk.END, msg, tag)
    text_log.see(tk.END)
    text_log.config(state=tk.DISABLED)


def clear_log():
    text_log.config(state=tk.NORMAL)
    text_log.delete("1.0", tk.END)
    text_log.config(state=tk.DISABLED)


# ── Main window ───────────────────────────────────────────────────────────────
def _make_flags() -> "tuple[tk.PhotoImage, tk.PhotoImage]":
    """Return (flag_de, flag_uk) as 24×15 PhotoImages drawn programmatically."""
    W, H = 24, 15
    S = H // 3  # stripe height = 5

    flag_de = tk.PhotoImage(width=W, height=H)
    flag_de.put("#222222", to=(0,       0, W,   S))      # black
    flag_de.put("#dd0000", to=(0,       S, W, S*2))      # red
    flag_de.put("#ffcc00", to=(0,     S*2, W,   H))      # gold

    flag_uk = tk.PhotoImage(width=W, height=H)
    Wm, Hm = W - 1, H - 1
    norm = (Wm ** 2 + Hm ** 2) ** 0.5
    cx, cy = Wm / 2.0, Hm / 2.0
    rows = []
    for y in range(H):
        row = []
        for x in range(W):
            adx = abs(x - cx)
            ady = abs(y - cy)
            d1 = abs(Hm * x - Wm * y) / norm               # TL→BR diagonal
            d2 = abs(Hm * x + Wm * y - Hm * Wm) / norm    # TR→BL diagonal
            if adx < 1.5 or ady < 1.5:
                c = "#cc0000"   # St George cross (red)
            elif adx < 3.0 or ady < 3.0:
                c = "#ffffff"   # St George cross (white surround)
            elif d1 < 0.9 or d2 < 0.9:
                c = "#cc0000"   # St Patrick diagonal (red)
            elif d1 < 2.0 or d2 < 2.0:
                c = "#ffffff"   # St Andrew diagonal (white)
            else:
                c = "#003399"   # blue field
            row.append(c)
        rows.append("{" + " ".join(row) + "}")
    flag_uk.put(" ".join(rows))

    # Thin dark border on both flags so they read against any bg
    for img in (flag_de, flag_uk):
        img.put("#555555", to=(0, 0,   W,   1))
        img.put("#555555", to=(0, H-1, W,   H))
        img.put("#555555", to=(0, 0,   1,   H))
        img.put("#555555", to=(W-1, 0, W,   H))

    return flag_de, flag_uk


def _make_app_icon() -> tk.PhotoImage:
    """Draw a 32×32 PCB chip icon: green board, gold body, silver pins."""
    img = tk.PhotoImage(width=32, height=32)
    # Board (PCB green)
    img.put("#1e6b1e", to=(0,  0,  32, 32))
    img.put("#165016", to=(0,  0,  32,  1))   # top edge
    img.put("#165016", to=(0, 31,  32, 32))   # bottom edge
    img.put("#165016", to=(0,  0,   1, 32))   # left edge
    img.put("#165016", to=(31, 0,  32, 32))   # right edge
    # IC body (gold)
    img.put("#c8960c", to=(8,  7, 24, 25))
    img.put("#e0aa20", to=(8,  7, 24,  8))    # top highlight
    img.put("#7a5800", to=(8, 24, 24, 25))    # bottom shadow
    img.put("#7a5800", to=(23, 7, 24, 25))    # right shadow
    # Pins — 4 on each side
    for i in range(4):
        y = 9 + i * 4
        img.put("#c8c8c8", to=( 1, y,  8, y+2))   # left
        img.put("#888888", to=( 1, y+2, 8, y+3))   # pin shadow
        img.put("#c8c8c8", to=(24, y, 31, y+2))    # right
        img.put("#888888", to=(24, y+2, 31, y+3))  # pin shadow
    # Pin 1 marker (white dot, top-left of body)
    img.put("#ffffff", to=(9, 9, 12, 12))
    img.put("#cccccc", to=(9, 11, 12, 12))   # subtle shadow on dot
    return img


root = tk.Tk()
root.title(_t("window_title"))
_app_icon = _make_app_icon()
root.iconphoto(True, _app_icon)
_flag_de, _flag_uk = _make_flags()
root.resizable(True, True)
root.minsize(840, 560)
root.columnconfigure(0, weight=1)
root.rowconfigure(2, weight=1)  # log grows when window is resized

# ── Theme (sv-ttk light, fall back to clam) ───────────────────────────────────
try:
    import sv_ttk as _sv_ttk
    _sv_ttk.set_theme("light")
except ImportError:
    ttk.Style().theme_use("clam")

_style = ttk.Style()
_style.configure("Accent.TButton",  background=_ACCENT,        foreground="#ffffff",
                 borderwidth=0, focusthickness=0, relief="flat", padding=(10, 5))
_style.map(      "Accent.TButton",  background=[("active", "#1a56a8"), ("pressed", "#174899")])
_style.configure("Green.TButton",   background=_CHIP_STOCK_FG, foreground="#ffffff",
                 borderwidth=0, focusthickness=0, relief="flat", padding=(10, 5))
_style.map(      "Green.TButton",   background=[("active", "#196a3e")])
_style.configure("Specs.Treeview",  font=("Segoe UI", 10), rowheight=22,
                 background="#ffffff", fieldbackground="#ffffff")
_style.configure("Specs.Treeview.Heading", font=("Segoe UI", 10, "bold"))

_var_mpn        = tk.StringVar(value="")
var_tooltips    = tk.BooleanVar(value=True)
var_merge_mode  = tk.BooleanVar(value=False)
_var_desc       = tk.StringVar(value="")
_var_status     = tk.StringVar(value="")
_var_fetch_hint = tk.StringVar(value="← API")
ToolTip.enabled = var_tooltips

_W = 11  # window margin

# ── Row 0: Top input bar ─────────────────────────────────────────────────────
frame_topbar = ttk.Frame(root)
frame_topbar.grid(row=0, column=0, sticky="ew", padx=_W, pady=(_W, 0))
frame_topbar.columnconfigure(1, weight=1)

_reg(ttk.Label(frame_topbar, text=_t("lbl_lcsc"), font=("Segoe UI", 11, "bold")),
     "lbl_lcsc").grid(row=0, column=0, sticky="w", padx=(0, 5))
entry_lcsc = ttk.Entry(frame_topbar, font=("Consolas", 11))
entry_lcsc.grid(row=0, column=1, sticky="ew", padx=(0, 8))
entry_lcsc.insert(0, "C6022114")
entry_lcsc.bind("<KeyRelease>", _on_lcsc_keyrelease)
entry_lcsc.bind("<Return>", lambda *_: _trigger_mpn_fetch(entry_lcsc.get().strip(), force=True))
_tip(entry_lcsc, "tip_lcsc")

ttk.Label(frame_topbar, textvariable=_var_fetch_hint,
          foreground=_MUTED, font=("Segoe UI", 9)).grid(row=0, column=2, padx=(0, 8))

btn_fetch = _reg(ttk.Button(frame_topbar, text="↻", width=3,
                              command=lambda: _trigger_mpn_fetch(entry_lcsc.get().strip(), force=True)),
                 "btn_fetch")
btn_fetch.grid(row=0, column=3, padx=(0, 10))
_tip(btn_fetch, "tip_fetch")

ttk.Separator(frame_topbar, orient=tk.VERTICAL).grid(row=0, column=4, sticky="ns", padx=(0, 6))
btn_about = ttk.Button(frame_topbar, text="ℹ", width=3, command=_show_about)
btn_about.grid(row=0, column=5, padx=(0, 4))
_tip(btn_about, "tip_about")
btn_lang = ttk.Button(frame_topbar, image=_flag_uk if _LANG == "de" else _flag_de, command=_toggle_lang)
btn_lang.grid(row=0, column=6)
_tip(btn_lang, "tip_lang")

# ── Row 1: Main two-column area ──────────────────────────────────────────────
frame_main = ttk.Frame(root)
frame_main.grid(row=1, column=0, sticky="nsew", padx=_W, pady=(8, 0))
frame_main.columnconfigure(1, weight=1)
frame_main.rowconfigure(0, weight=1)

# ════════════════════════════════════════════════════════════ LEFT COLUMN ═════
frame_left = ttk.Frame(frame_main, width=370)
frame_left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
frame_left.columnconfigure(0, weight=1)
frame_left.rowconfigure(2, weight=1)
frame_left.grid_propagate(False)

_LW = 10  # label column width (chars)

# ─ Output target ─────────────────────────────────────────────────────────────
grp_output = _reg(ttk.LabelFrame(frame_left, text=_t("lbl_output"), padding=(9, 6)), "lbl_output")
grp_output.grid(row=0, column=0, sticky="ew", pady=(0, 8))
grp_output.columnconfigure(0, weight=1)

frame_mode_toggle = ttk.Frame(grp_output)
frame_mode_toggle.grid(row=0, column=0, sticky="w", pady=(0, 6))
_reg(ttk.Radiobutton(frame_mode_toggle, text=_t("rb_newlib"),
                     variable=var_merge_mode, value=False,
                     command=_on_merge_mode_change), "rb_newlib").pack(side=tk.LEFT, padx=(0, 16))
_reg(ttk.Radiobutton(frame_mode_toggle, text=_t("rb_merge"),
                     variable=var_merge_mode, value=True,
                     command=_on_merge_mode_change), "rb_merge").pack(side=tk.LEFT)

frame_output_section = ttk.Frame(grp_output)
frame_output_section.grid(row=1, column=0, sticky="ew")
frame_output_section.columnconfigure(0, weight=1)

frame_newlib = ttk.Frame(frame_output_section)
frame_newlib.columnconfigure(1, weight=1)

_reg(ttk.Label(frame_newlib, text=_t("lbl_newlib_sym"), width=_LW, anchor="e"),
     "lbl_newlib_sym").grid(row=0, column=0, sticky="e", padx=(0, 5), pady=2)
entry_newlib_sym = ttk.Entry(frame_newlib, font=("Consolas", 9))
entry_newlib_sym.grid(row=0, column=1, sticky="ew", pady=2)
ttk.Button(frame_newlib, text="…", width=2, command=browse_newlib_sym).grid(row=0, column=2, padx=(4,0), pady=2)
_tip(entry_newlib_sym, "tip_newlib_sym")
entry_newlib_sym.bind("<FocusOut>", lambda _: _save_config())

_reg(ttk.Label(frame_newlib, text=_t("lbl_newlib_fp"), width=_LW, anchor="e"),
     "lbl_newlib_fp").grid(row=1, column=0, sticky="e", padx=(0, 5), pady=2)
entry_newlib_fp = ttk.Entry(frame_newlib, font=("Consolas", 9))
entry_newlib_fp.grid(row=1, column=1, sticky="ew", pady=2)
ttk.Button(frame_newlib, text="…", width=2, command=browse_newlib_fp).grid(row=1, column=2, padx=(4,0), pady=2)
_tip(entry_newlib_fp, "tip_newlib_fp")
entry_newlib_fp.bind("<FocusOut>", lambda _: _save_config())

_reg(ttk.Label(frame_newlib, text=_t("lbl_newlib_3d"), width=_LW, anchor="e"),
     "lbl_newlib_3d").grid(row=2, column=0, sticky="e", padx=(0, 5), pady=2)
entry_newlib_3d = ttk.Entry(frame_newlib, font=("Consolas", 9))
entry_newlib_3d.grid(row=2, column=1, sticky="ew", pady=2)
ttk.Button(frame_newlib, text="…", width=2, command=browse_newlib_3d).grid(row=2, column=2, padx=(4,0), pady=2)
_tip(entry_newlib_3d, "tip_newlib_3d")
entry_newlib_3d.bind("<FocusOut>", lambda _: _save_config())

frame_merge = ttk.Frame(frame_output_section)
frame_merge.columnconfigure(1, weight=1)

_reg(ttk.Label(frame_merge, text=_t("lbl_merge_sym"), width=_LW, anchor="e"),
     "lbl_merge_sym").grid(row=0, column=0, sticky="e", padx=(0, 5), pady=2)
entry_merge_sym = ttk.Entry(frame_merge, font=("Consolas", 9))
entry_merge_sym.grid(row=0, column=1, sticky="ew", pady=2)
ttk.Button(frame_merge, text="…", width=2, command=browse_merge_sym).grid(row=0, column=2, padx=(4,0), pady=2)
_tip(entry_merge_sym, "tip_merge_sym")
entry_merge_sym.bind("<FocusOut>", lambda _: _save_config())

_reg(ttk.Label(frame_merge, text=_t("lbl_merge_fp"), width=_LW, anchor="e"),
     "lbl_merge_fp").grid(row=1, column=0, sticky="e", padx=(0, 5), pady=2)
entry_merge_fp = ttk.Entry(frame_merge, font=("Consolas", 9))
entry_merge_fp.grid(row=1, column=1, sticky="ew", pady=2)
ttk.Button(frame_merge, text="…", width=2, command=browse_merge_fp).grid(row=1, column=2, padx=(4,0), pady=2)
_tip(entry_merge_fp, "tip_merge_fp")
entry_merge_fp.bind("<FocusOut>", lambda _: _save_config())

_reg(ttk.Label(frame_merge, text=_t("lbl_merge_3d"), width=_LW, anchor="e"),
     "lbl_merge_3d").grid(row=2, column=0, sticky="e", padx=(0, 5), pady=2)
entry_merge_3d = ttk.Entry(frame_merge, font=("Consolas", 9))
entry_merge_3d.grid(row=2, column=1, sticky="ew", pady=2)
ttk.Button(frame_merge, text="…", width=2, command=browse_merge_3d).grid(row=2, column=2, padx=(4,0), pady=2)
_tip(entry_merge_3d, "tip_merge_3d")
entry_merge_3d.bind("<FocusOut>", lambda _: _save_config())

frame_newlib.pack(fill=tk.X)

# ─ Import & options ──────────────────────────────────────────────────────────
grp_import = _reg(ttk.LabelFrame(frame_left, text=_t("lbl_import_opts"), padding=(9, 6)), "lbl_import_opts")
grp_import.grid(row=1, column=0, sticky="ew", pady=(0, 8))
grp_import.columnconfigure(0, weight=1)

frame_import_types = ttk.Frame(grp_import)
frame_import_types.grid(row=0, column=0, sticky="w", pady=(0, 6))
var_mode_sym = tk.BooleanVar(value=True)
var_mode_fp  = tk.BooleanVar(value=True)
var_mode_3d  = tk.BooleanVar(value=True)
for _var, _lbl, _tip_k in [(var_mode_sym, "rb_symbol", "tip_rb_symbol"),
                             (var_mode_fp,  "rb_footprint", "tip_rb_footprint"),
                             (var_mode_3d,  "rb_3d", "tip_rb_3d")]:
    _cb = ttk.Checkbutton(frame_import_types, text=_t(_lbl), variable=_var)
    _cb.pack(side=tk.LEFT, padx=(0, 14))
    _reg(_cb, _lbl); _tip(_cb, _tip_k)

ttk.Separator(grp_import, orient=tk.HORIZONTAL).grid(row=1, column=0, sticky="ew", pady=(0, 6))

frame_opts_grid = ttk.Frame(grp_import)
frame_opts_grid.grid(row=2, column=0, sticky="w", pady=(0, 6))
var_overwrite = tk.BooleanVar()
var_cache     = tk.BooleanVar()
var_projrel   = tk.BooleanVar()
var_debug     = tk.BooleanVar()
var_verbose   = tk.BooleanVar()
for _i, (_lbl, _var, _tip_k, _cmd) in enumerate([
    ("cb_overwrite", var_overwrite, "tip_cb_overwrite", None),
    ("cb_cache",     var_cache,     "tip_cb_cache",     None),
    ("cb_projrel",   var_projrel,   "tip_cb_projrel",   _on_projrel_change),
    ("cb_debug",     var_debug,     "tip_cb_debug",     None),
    ("cb_verbose",   var_verbose,   "tip_cb_verbose",   None),
    ("cb_tooltips",  var_tooltips,  None,               None),
]):
    _cb = ttk.Checkbutton(frame_opts_grid, text=_t(_lbl), variable=_var,
                           **({"command": _cmd} if _cmd else {}))
    _cb.grid(row=_i // 2, column=_i % 2, sticky="w", padx=(0 if _i%2==0 else 8, 0), pady=2)
    _reg(_cb, _lbl)
    if _tip_k: _tip(_cb, _tip_k)

ttk.Separator(grp_import, orient=tk.HORIZONTAL).grid(row=3, column=0, sticky="ew", pady=(0, 6))

frame_3drow = ttk.Frame(grp_import)
frame_3drow.grid(row=4, column=0, sticky="ew", pady=(0, 4))
frame_3drow.columnconfigure(1, weight=1)
_reg(ttk.Label(frame_3drow, text=_t("lbl_3dvar"), width=_LW, anchor="e"),
     "lbl_3dvar").grid(row=0, column=0, sticky="e", padx=(0, 5))
entry_3dvar = ttk.Combobox(frame_3drow, font=("Consolas", 9))
entry_3dvar.grid(row=0, column=1, sticky="ew")
entry_3dvar.set(DEFAULT_3D_VAR)
_tip(entry_3dvar, "tip_3dvar")
btn_kicad_vars = _reg(ttk.Button(frame_3drow, text=_t("btn_kicad_vars"), command=_reload_kicad_vars),
                       "btn_kicad_vars")
btn_kicad_vars.grid(row=0, column=2, padx=(4, 0))
_tip(btn_kicad_vars, "tip_kicad_vars")

frame_customrow = ttk.Frame(grp_import)
frame_customrow.grid(row=5, column=0, sticky="ew")
frame_customrow.columnconfigure(1, weight=1)
_reg(ttk.Label(frame_customrow, text=_t("lbl_custom"), width=_LW, anchor="e"),
     "lbl_custom").grid(row=0, column=0, sticky="e", padx=(0, 5))
entry_custom = ttk.Entry(frame_customrow, font=("Consolas", 9))
entry_custom.grid(row=0, column=1, sticky="ew")
_tip(entry_custom, "tip_custom")

# ─ Action buttons ─────────────────────────────────────────────────────────────
frame_btn = ttk.Frame(frame_left)
frame_btn.grid(row=2, column=0, sticky="sew")
frame_btn.columnconfigure(0, weight=1)

btn_run = _reg(ttk.Button(frame_btn, text=_t("btn_run"), style="Green.TButton", command=run_import), "btn_run")
btn_run.grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=(0, 4))

frame_btn_sec = ttk.Frame(frame_btn)
frame_btn_sec.grid(row=1, column=0, sticky="w")
btn_dry_run = _reg(ttk.Button(frame_btn_sec, text=_t("btn_dryrun"), command=dry_run), "btn_dryrun")
btn_dry_run.pack(side=tk.LEFT, padx=(0, 4))
_tip(btn_dry_run, "tip_dryrun")
btn_preview = _reg(ttk.Button(frame_btn_sec, text=_t("btn_preview"), command=_show_preview, state=tk.DISABLED), "btn_preview")
btn_preview.pack(side=tk.LEFT, padx=(0, 4))
_tip(btn_preview, "tip_preview")
btn_clear_log = _reg(ttk.Button(frame_btn_sec, text=_t("btn_clear"), command=clear_log), "btn_clear")
btn_clear_log.pack(side=tk.LEFT)

# ════════════════════════════════════════════════════════════ RIGHT COLUMN ════
frame_right = ttk.Frame(frame_main)
frame_right.grid(row=0, column=1, sticky="nsew")
frame_right.columnconfigure(0, weight=1)
frame_right.rowconfigure(1, weight=1)

# ─ Component data card ────────────────────────────────────────────────────────
grp_comp = _reg(ttk.LabelFrame(frame_right, text=_t("frm_comp_data"), padding=(9, 6)), "frm_comp_data")
grp_comp.grid(row=0, column=0, sticky="ew", pady=(0, 8))
grp_comp.columnconfigure(1, weight=1)

canvas_photo = tk.Canvas(grp_comp, width=52, height=62, bg="#f0f2f5",
                          highlightthickness=1, highlightbackground="#d3d8de")
canvas_photo.grid(row=0, column=0, rowspan=4, sticky="nw", padx=(0, 10))
canvas_photo.create_text(26, 31, text="IMG", fill="#9aa3ad", font=("Segoe UI", 9))

frame_mpn_row = ttk.Frame(grp_comp)
frame_mpn_row.grid(row=0, column=1, sticky="ew")
frame_mpn_row.columnconfigure(0, weight=1)
lbl_mpn_large = ttk.Label(frame_mpn_row, text="—", font=("Segoe UI", 15, "bold"), foreground=_TEXT)
lbl_mpn_large.grid(row=0, column=0, sticky="w")
lbl_matched = ttk.Label(frame_mpn_row, text="", font=("Segoe UI", 9), foreground=_MUTED)
lbl_matched.grid(row=0, column=1, sticky="e", padx=(8, 0))

# Link chips — right-aligned below the matched indicator
frame_link_chips = ttk.Frame(frame_mpn_row)
frame_link_chips.grid(row=1, column=1, sticky="e", pady=(3, 0))

def _link_chip(parent, text, reg_key=None):
    lbl = tk.Label(parent, text=text, bg=_CHIP_NEUTRAL_BG, fg=_CHIP_NEUTRAL_FG,
                   font=("Segoe UI", 9, "bold"), padx=6, pady=2, bd=0, cursor="")
    lbl.pack(side=tk.LEFT, padx=(0, 4))
    if reg_key:
        _reg(lbl, reg_key)
    return lbl

btn_datasheet = _link_chip(frame_link_chips, _t("lbl_datasheet"), "lbl_datasheet")
btn_lcsc      = _reg(_link_chip(frame_link_chips, "LCSC ↗"), "btn_lcsc")

lbl_mfr_sub = ttk.Label(grp_comp, text="", font=("Segoe UI", 9), foreground=_MUTED)
lbl_mfr_sub.grid(row=1, column=1, sticky="w")

lbl_desc_sub = ttk.Label(grp_comp, textvariable=_var_desc, font=("Segoe UI", 9),
                           foreground=_MUTED, wraplength=400, justify=tk.LEFT)
lbl_desc_sub.grid(row=2, column=1, sticky="w", pady=(2, 4))

frame_chips = ttk.Frame(grp_comp)
frame_chips.grid(row=3, column=1, sticky="w")

def _chip(parent: ttk.Frame, bg: str = _CHIP_NEUTRAL_BG, fg: str = _CHIP_NEUTRAL_FG) -> tk.Label:
    lbl = tk.Label(parent, text="", bg=bg, fg=fg, font=("Segoe UI", 9, "bold"),
                   padx=7, pady=2, bd=0, relief=tk.FLAT)
    lbl.pack(side=tk.LEFT, padx=(0, 6))
    return lbl

lbl_chip_type  = _chip(frame_chips)
lbl_chip_price = _chip(frame_chips)
lbl_chip_stock = _chip(frame_chips, _CHIP_STOCK_BG, _CHIP_STOCK_FG)
lbl_chip_pkg   = _chip(frame_chips)
lbl_chip_rohs  = _chip(frame_chips, _CHIP_ROHS_BG,  _CHIP_ROHS_FG)

# ─ Specs table ────────────────────────────────────────────────────────────────
grp_specs = _reg(ttk.LabelFrame(frame_right, text=_t("frm_specs"), padding=(6, 4)), "frm_specs")
grp_specs.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
grp_specs.columnconfigure(0, weight=1)
grp_specs.rowconfigure(0, weight=1)

frame_tree = ttk.Frame(grp_specs)
frame_tree.grid(row=0, column=0, sticky="nsew")
frame_tree.columnconfigure(0, weight=1)
frame_tree.rowconfigure(0, weight=1)

tree_specs = ttk.Treeview(frame_tree, style="Specs.Treeview",
                            columns=("param", "value"), show="headings", selectmode="browse")
tree_specs.heading("param", text=_t("col_param"))
tree_specs.heading("value", text=_t("col_value"))
tree_specs.column("param", width=160, minwidth=90, stretch=False)
tree_specs.column("value", width=220, minwidth=90, stretch=True)
tree_specs.grid(row=0, column=0, sticky="nsew")
ttk.Scrollbar(frame_tree, orient=tk.VERTICAL,
              command=tree_specs.yview).grid(row=0, column=1, sticky="ns")
tree_specs.configure(yscrollcommand=ttk.Scrollbar(frame_tree).set)

# ─ Previews ──────────────────────────────────────────────────────────────────
frame_preview_strip = ttk.Frame(frame_right)
frame_preview_strip.grid(row=2, column=0, sticky="ew")
frame_preview_strip.columnconfigure(0, weight=1)
frame_preview_strip.columnconfigure(1, weight=1)

_sym_panel = ttk.LabelFrame(frame_preview_strip, text="Symbol", padding=4)
_sym_panel.grid(row=0, column=0, padx=(0, 4), sticky="nsew")
canvas_sym_thumb = tk.Canvas(_sym_panel, width=THUMB_W, height=THUMB_H,
                              bg=_CANVAS_SYM_BG, highlightthickness=0, cursor="hand2")
canvas_sym_thumb.pack()
canvas_sym_thumb.bind("<Button-1>", lambda *_: _open_large_preview("sym"))

_fp_panel = ttk.LabelFrame(frame_preview_strip, text="Footprint", padding=4)
_fp_panel.grid(row=0, column=1, padx=(4, 0), sticky="nsew")
canvas_fp_thumb = tk.Canvas(_fp_panel, width=THUMB_W, height=THUMB_H,
                             bg=_CANVAS_FP_BG, highlightthickness=0, cursor="hand2")
canvas_fp_thumb.pack()
canvas_fp_thumb.bind("<Button-1>", lambda *_: _open_large_preview("fp"))

# ── Row 2: Output log ─────────────────────────────────────────────────────────
frame_log_outer = ttk.Frame(root)
frame_log_outer.grid(row=2, column=0, sticky="nsew", padx=_W, pady=(8, 0))
frame_log_outer.columnconfigure(0, weight=1)
frame_log_outer.rowconfigure(1, weight=1)

frame_log_hdr = ttk.Frame(frame_log_outer)
frame_log_hdr.grid(row=0, column=0, sticky="ew", pady=(0, 3))
frame_log_hdr.columnconfigure(0, weight=1)
_reg(ttk.Label(frame_log_hdr, text=_t("frm_log"), font=("Segoe UI", 10, "bold")), "frm_log").grid(row=0, column=0, sticky="w")
ttk.Button(frame_log_hdr, text=_t("btn_clear"), command=clear_log).grid(row=0, column=1)

frame_log = ttk.Frame(frame_log_outer)
frame_log.grid(row=1, column=0, sticky="nsew")
frame_log.columnconfigure(0, weight=1)
frame_log.rowconfigure(0, weight=1)
text_log = scrolledtext.ScrolledText(frame_log, width=80, height=7, state=tk.DISABLED,
                                      font=("Consolas", 9), wrap=tk.WORD)
text_log.grid(row=0, column=0, sticky="nsew")
text_log.tag_config("error", foreground="#cc0000")
text_log.tag_config("ok",    foreground="#1f7a44")
text_log.tag_config("info",  foreground=_ACCENT)

# ── Row 3: Status bar ─────────────────────────────────────────────────────────
frame_status = ttk.Frame(root)
frame_status.grid(row=3, column=0, sticky="ew", padx=_W, pady=(4, _W))
frame_status.columnconfigure(1, weight=1)
lbl_status_dot = ttk.Label(frame_status, text="●", foreground=_CHIP_STOCK_FG, font=("Segoe UI", 10))
lbl_status_dot.grid(row=0, column=0, padx=(0, 5))
_var_status.set(_t("status_ready"))
ttk.Label(frame_status, textvariable=_var_status, font=("Segoe UI", 9), foreground=_MUTED).grid(row=0, column=1, sticky="w")

import importlib.metadata as _imeta
try:    _e2k_ver = _imeta.version("easyeda2kicad")
except Exception: _e2k_ver = "?"
ttk.Label(frame_status, text=f"easyeda2kicad {_e2k_ver}",
          font=("Consolas", 9), foreground=_MUTED).grid(row=0, column=2, sticky="e")

# ── Apply persisted config ────────────────────────────────────────────────────
_cfg = _load_config()
if _cfg.get("newlib_sym_dir"):  entry_newlib_sym.insert(0, _cfg["newlib_sym_dir"])
if _cfg.get("newlib_fp_dir"):   entry_newlib_fp.insert(0,  _cfg["newlib_fp_dir"])
if _cfg.get("newlib_3d_dir"):   entry_newlib_3d.insert(0,  _cfg["newlib_3d_dir"])
if _cfg.get("merge_sym_lib"):   entry_merge_sym.insert(0,  _cfg["merge_sym_lib"])
if _cfg.get("merge_fp_lib"):    entry_merge_fp.insert(0,   _cfg["merge_fp_lib"])
if _cfg.get("merge_3d_dir"):    entry_merge_3d.insert(0,   _cfg["merge_3d_dir"])
if _cfg.get("merge_mode"):
    var_merge_mode.set(True)
    frame_newlib.pack_forget()
    frame_merge.pack(fill=tk.X)

# ── Populate 3D variable Combobox with KiCad path variables ──────────────────
_kicad_vars, _kicad_config_path = _load_kicad_vars()
_3d_suggestions = [f"${{{k}}}" for k in _kicad_vars]
if DEFAULT_3D_VAR not in _3d_suggestions:
    _3d_suggestions.insert(0, DEFAULT_3D_VAR)
entry_3dvar["values"] = _3d_suggestions
if _kicad_vars:
    _kicad_version = Path(_kicad_config_path).parent.name
    log(_t("log_kicad_vars_found", version=_kicad_version), "info")
    for _k, _v in _kicad_vars.items():
        log(f"  ${{{_k}}} = {_v}\n", "ok")

root.protocol("WM_DELETE_WINDOW", lambda: (_save_config(), root.destroy()))

# Lock window size after layout so content changes never resize the window
root.update_idletasks()
root.geometry(root.geometry())

# Trigger initial MPN fetch for pre-filled ID
root.after(500, lambda: _trigger_mpn_fetch(entry_lcsc.get().strip()))

root.mainloop()
