import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
import subprocess
import threading
import sys
import re
import json
import shutil
import tempfile
from pathlib import Path

DEFAULT_OUTPUT_DIR = ""
DEFAULT_3D_VAR = "${KICAD_USER_3DMODEL_DIR}"

# Lines from easyeda2kicad output that are shown in compact mode
_INFO_RE = re.compile(r"\[INFO\]|\[WARNING\]|\[ERROR\]|-- easyeda2kicad")


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


# ── MPN fetch ─────────────────────────────────────────────────────────────────
def _fetch_mpn(lcsc_id: str):
    """Return component MPN from EasyEDA API, or None on failure."""
    try:
        from easyeda2kicad.easyeda.easyeda_api import EasyedaApi
        from easyeda2kicad.easyeda.easyeda_importer import EasyedaSymbolImporter
        cad = EasyedaApi().get_cad_data_of_component(lcsc_id)
        if cad:
            return EasyedaSymbolImporter(easyeda_cp_cad_data=cad).get_symbol().info.name
    except Exception:
        pass
    return None


# ── Description fetch & inject ──────────────────────────────────────────────
def _fetch_description(lcsc_id: str) -> str:
    """Fetch product description from JLCPCB parts API by LCSC ID."""
    try:
        from easyeda2kicad.easyeda.easyeda_api import EasyedaApi
        results = EasyedaApi().search_jlcpcb_components(keyword=lcsc_id, page_size=5)
        for r in results.get("results", []):
            if r.get("lcsc", "").upper() == lcsc_id.upper():
                return r.get("description", "").strip()
    except Exception:
        pass
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
        return False, "Keine Symbole in generierter Datei gefunden."

    sym_blocks = sym_blocks.replace(f'"{old_fp_lib}:', f'"{new_fp_lib}:')
    names = re.findall(r'^\(symbol "([^"]+)"', sym_blocks, re.MULTILINE)

    if target_path.exists():
        target = target_path.read_text(encoding="utf-8")
        for name in names:
            if f'(symbol "{name}"' in target:
                if not overwrite:
                    return False, f"'{name}' existiert bereits (Überschreiben aktivieren)."
                target = _remove_symbol_block(target, name)
        last = target.rfind(')')
        if last == -1:
            return False, "Ungültige .kicad_sym-Datei (kein schließendes ')')."
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
def merge_into_libs(output_base: str, import_mode: str) -> list:
    """Merge generated temp files into the configured target libraries.

    Returns list of (message, tag) tuples for the log.
    """
    msgs = []
    mpn    = Path(output_base).name
    sym_lib = entry_merge_sym.get().strip()
    fp_lib  = entry_merge_fp.get().strip()
    dir_3d  = entry_merge_3d.get().strip()

    do_sym = import_mode in ("full", "symbol")
    do_fp  = import_mode in ("full", "footprint")
    do_3d  = import_mode in ("full", "3d")

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
            msgs.append((f"  Symbol: Quelldatei nicht gefunden ({sym_src.name})\n", "error"))

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
                    msgs.append((f"  Footprint {kmod.name}: bereits vorhanden.\n", "error"))
                    continue
                txt = kmod.read_text(encoding="utf-8").replace(
                    f'"{abs_3d_pfx}', f'"{new_3d_pfx}')
                dst.write_text(txt, encoding="utf-8")
                msgs.append((f"  Footprint: {kmod.name} → {fp_dst_dir.name}\n", "ok"))
        else:
            msgs.append((f"  Footprint: Quellordner nicht gefunden ({fp_src_dir.name})\n", "error"))

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
                    msgs.append((f"  3D {model.name}: bereits vorhanden.\n", "error"))
                    continue
                shutil.copy2(model, dst)
                msgs.append((f"  3D: {model.name} → {dst_3d.name}\n", "ok"))
        else:
            msgs.append((f"  3D: Quellordner nicht gefunden ({src_3d.name})\n", "error"))

    return msgs


# ── New-library distributor ───────────────────────────────────────────────────
def distribute_new_lib(output_base: str, import_mode: str) -> list:
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

    do_sym = import_mode in ("full", "symbol")
    do_fp  = import_mode in ("full", "footprint")
    do_3d  = import_mode in ("full", "3d")

    # ── Symbol ────────────────────────────────────────────────────────────────
    if do_sym:
        if not sym_dir:
            msgs.append(("  Symbol: Symbols-Ordner nicht angegeben.\n", "error"))
        else:
            src = Path(output_base + ".kicad_sym")
            if src.exists():
                dst_dir = Path(sym_dir)
                dst_dir.mkdir(parents=True, exist_ok=True)
                dst = dst_dir / src.name
                if dst.exists() and not var_overwrite.get():
                    msgs.append((f"  Symbol: {src.name} bereits vorhanden.\n", "error"))
                else:
                    shutil.copy2(src, dst)
                    msgs.append((f"  Symbol: {src.name} → {dst_dir.name}\\\n", "ok"))
            else:
                msgs.append((f"  Symbol: Quelldatei nicht gefunden ({src.name})\n", "error"))

    # ── Footprint ─────────────────────────────────────────────────────────────
    if do_fp:
        if not fp_dir:
            msgs.append(("  Footprint: Footprints-Ordner nicht angegeben.\n", "error"))
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
                    msgs.append((f"  Footprint: {fp_src.name} bereits vorhanden.\n", "error"))
                else:
                    if fp_dst.exists():
                        shutil.rmtree(fp_dst)
                    shutil.copytree(fp_src, fp_dst)
                    msgs.append((f"  Footprint: {fp_src.name} → {fp_dst_parent.name}\\\n", "ok"))
            else:
                msgs.append((f"  Footprint: Quellordner nicht gefunden ({fp_src.name})\n", "error"))

    # ── 3D Models ─────────────────────────────────────────────────────────────
    if do_3d:
        if not dir_3d:
            msgs.append(("  3D: 3D-Ordner nicht angegeben.\n", "error"))
        else:
            src_3d = Path(output_base + ".3dshapes")
            if src_3d.exists():
                dst_parent = Path(dir_3d)
                dst_parent.mkdir(parents=True, exist_ok=True)
                dst_3d = dst_parent / src_3d.name  # MPN.3dshapes
                if dst_3d.exists() and not var_overwrite.get():
                    msgs.append((f"  3D: {src_3d.name} bereits vorhanden.\n", "error"))
                else:
                    if dst_3d.exists():
                        shutil.rmtree(dst_3d)
                    shutil.copytree(src_3d, dst_3d)
                    msgs.append((f"  3D: {src_3d.name} → {dst_parent.name}\\\n", "ok"))
            else:
                msgs.append((f"  3D: Quellordner nicht gefunden ({src_3d.name})\n", "error"))

    return msgs


# ── UI callbacks ─────────────────────────────────────────────────────────────
_mpn_timer = None
_last_fetched_id = None


def browse_newlib_sym():
    path = filedialog.askdirectory(
        initialdir=entry_newlib_sym.get() or DEFAULT_OUTPUT_DIR,
        title="Symbols-Ordner auswählen (MPN.kicad_sym wird hier abgelegt)",
    )
    if path:
        entry_newlib_sym.delete(0, tk.END)
        entry_newlib_sym.insert(0, path)
        _save_config()


def browse_newlib_fp():
    path = filedialog.askdirectory(
        initialdir=entry_newlib_fp.get() or DEFAULT_OUTPUT_DIR,
        title="Footprints-Ordner auswählen (MPN.pretty/ wird hier erstellt)",
    )
    if path:
        entry_newlib_fp.delete(0, tk.END)
        entry_newlib_fp.insert(0, path)
        _save_config()


def browse_newlib_3d():
    path = filedialog.askdirectory(
        initialdir=entry_newlib_3d.get() or DEFAULT_OUTPUT_DIR,
        title="3D-Basisordner auswählen (MPN.3dshapes/ wird hier erstellt)",
    )
    if path:
        entry_newlib_3d.delete(0, tk.END)
        entry_newlib_3d.insert(0, path)
        _save_config()


def browse_merge_sym():
    init = str(Path(entry_merge_sym.get()).parent) if entry_merge_sym.get() else DEFAULT_OUTPUT_DIR
    path = filedialog.askopenfilename(
        initialdir=init,
        filetypes=[("KiCad Symbol-Library", "*.kicad_sym"), ("Alle Dateien", "*.*")],
        title="Symbol-Library auswählen",
    )
    if path:
        entry_merge_sym.delete(0, tk.END)
        entry_merge_sym.insert(0, path)
        _save_config()


def browse_merge_fp():
    path = filedialog.askdirectory(
        initialdir=entry_merge_fp.get() or DEFAULT_OUTPUT_DIR,
        title="Footprint-Library (.pretty Ordner) auswählen",
    )
    if path:
        entry_merge_fp.delete(0, tk.END)
        entry_merge_fp.insert(0, path)
        _save_config()


def browse_merge_3d():
    path = filedialog.askdirectory(
        initialdir=entry_merge_3d.get() or DEFAULT_OUTPUT_DIR,
        title="3D-Modell-Ordner (.3dshapes Ordner) auswählen",
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
        if not _name_edited.get():
            # Debounce: fetch MPN 700ms after user stops typing
            if _mpn_timer:
                root.after_cancel(_mpn_timer)
            _mpn_timer = root.after(700, lambda: _trigger_mpn_fetch(ids[0]))
    else:
        # Multiple IDs – name field not applicable
        entry_name.config(state=tk.DISABLED)


def _trigger_mpn_fetch(lcsc_id: str):
    global _last_fetched_id
    if lcsc_id == _last_fetched_id or _name_edited.get():
        return
    entry_name.config(state=tk.NORMAL)
    entry_name.delete(0, tk.END)
    entry_name.insert(0, "…")

    def worker():
        mpn = _fetch_mpn(lcsc_id)
        result = mpn if mpn else lcsc_id
        root.after(0, lambda: _apply_mpn(lcsc_id, result))

    threading.Thread(target=worker, daemon=True).start()


def _apply_mpn(lcsc_id: str, name: str):
    global _last_fetched_id
    _last_fetched_id = lcsc_id
    if not _name_edited.get():
        entry_name.config(state=tk.NORMAL)
        entry_name.delete(0, tk.END)
        entry_name.insert(0, name)


def _on_name_keypress(*_):
    _name_edited.set(True)


def _build_cmd(lcsc_id: str, output_base: str) -> list:
    cmd = [sys.executable, "-m", "easyeda2kicad"]
    mode = var_mode.get()
    if mode == "full":       cmd.append("--full")
    elif mode == "symbol":   cmd.append("--symbol")
    elif mode == "footprint":cmd.append("--footprint")
    elif mode == "3d":       cmd.append("--3d")
    cmd += ["--lcsc_id", lcsc_id, "--output", output_base]
    if var_overwrite.get(): cmd.append("--overwrite")
    if var_cache.get():     cmd.append("--use-cache")
    if var_projrel.get():   cmd.append("--project-relative")
    if var_debug.get():     cmd.append("--debug")
    custom = entry_custom.get().strip()
    if custom:
        cmd += ["--custom-field"] + custom.split()
    return cmd


def _run_one(lcsc_id: str, name: str):
    """Run import for a single ID and post-process. Called from worker thread."""
    tmpdir = tempfile.mkdtemp(prefix="lcsc_import_")
    output_base = str(Path(tmpdir) / name)
    root.after(0, lambda: log(f"► {lcsc_id}  →  {name}\n", "info"))
    cmd = _build_cmd(lcsc_id, output_base)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        raw = result.stdout + result.stderr
        if var_verbose.get():
            display = raw
        else:
            lines = [l for l in raw.splitlines() if _INFO_RE.search(l)]
            display = "\n".join(lines) + "\n" if lines else raw
        tag = "ok" if result.returncode == 0 else "error"
        root.after(0, lambda d=display, t=tag: log(d if d.strip() else "(keine Ausgabe)\n", t))

        mode = var_mode.get()
        if result.returncode == 0:
            desc = _fetch_description(lcsc_id)
            if desc:
                _patch_description(output_base, desc)
                root.after(0, lambda d=desc: log(f"  Beschreibung: {d[:80]}\n", "info"))
            post_msgs = (merge_into_libs if var_merge_mode.get() else distribute_new_lib)(
                output_base, mode)
            for msg, t in post_msgs:
                root.after(0, lambda m=msg, t=t: log(m, t))
    except FileNotFoundError:
        root.after(0, lambda: log("Fehler: Python oder easyeda2kicad nicht gefunden.\n", "error"))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_import():
    raw_input = entry_lcsc.get().strip()
    ids = _parse_ids(raw_input)

    if not ids:
        log("Fehler: Keine LCSC-ID eingegeben.\n", "error")
        return

    # Duplicate info
    raw_count = len([p for p in re.split(r"[,;\s]+", raw_input.strip()) if p])
    if raw_count > len(ids):
        log(f"Info: {raw_count - len(ids)} Duplikat(e) entfernt.\n", "info")

    btn_run.config(state=tk.DISABLED)

    if len(ids) == 1:
        name = entry_name.get().strip() or ids[0]
        threading.Thread(
            target=_batch_worker, args=([ids[0]], {ids[0]: name}), daemon=True
        ).start()
    else:
        # Batch: fetch all MPNs first, then show confirm dialog with the list
        _start_batch_resolve(ids)


def _start_batch_resolve(ids: list):
    """Show loading indicator while fetching MPNs, then open confirm dialog."""
    dlg = tk.Toplevel(root)
    dlg.title("Lade Komponentennamen\u2026")
    dlg.resizable(False, False)
    dlg.grab_set()
    ttk.Label(dlg, text=f"Rufe Namen f\u00fcr {len(ids)} Komponenten ab\u2026",
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
    dlg.title("Import best\u00e4tigen")
    dlg.resizable(False, False)
    dlg.grab_set()

    ttk.Label(dlg, text=f"{len(ids)} Komponenten erkannt:",
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
        suffix = "  \u26a0 Name nicht gefunden" if mpn == lcsc_id else ""
        listbox.insert(tk.END, f"  {lcsc_id:<12}  \u2192  {mpn}{suffix}")

    ttk.Label(dlg, text="Alle importieren?", padding=(12, 6, 12, 2)).pack(anchor="w")

    frame_btn = ttk.Frame(dlg)
    frame_btn.pack(pady=(4, 12))

    confirmed = [False]

    def on_yes():
        confirmed[0] = True
        dlg.destroy()

    ttk.Button(frame_btn, text="Ja, importieren", command=on_yes).pack(side=tk.LEFT, padx=8)
    ttk.Button(frame_btn, text="Abbrechen", command=dlg.destroy).pack(side=tk.LEFT, padx=8)

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
        root.after(0, lambda: btn_run.config(state=tk.NORMAL))


def _batch_worker(ids: list, id_name: dict):
    """Run imports sequentially. Called from a background thread."""
    for lcsc_id in ids:
        _run_one(lcsc_id, id_name[lcsc_id])
    root.after(0, lambda: btn_run.config(state=tk.NORMAL))


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
root = tk.Tk()
root.title("LCSC → KiCad Importer")
root.resizable(False, False)

_name_edited   = tk.BooleanVar(value=False)
var_tooltips   = tk.BooleanVar(value=True)
var_merge_mode = tk.BooleanVar(value=False)
ToolTip.enabled = var_tooltips
pad = {"padx": 8, "pady": 3}

frame_top = ttk.Frame(root, padding=10)
frame_top.grid(row=0, column=0, sticky="ew")
frame_top.columnconfigure(1, weight=1)

# ── Row 0: LCSC IDs ──────────────────────────────────────────────────────────
ttk.Label(frame_top, text="LCSC-ID(s):").grid(row=0, column=0, sticky="w", **pad)
entry_lcsc = ttk.Entry(frame_top, width=44)
entry_lcsc.grid(row=0, column=1, columnspan=2, sticky="ew", **pad)
entry_lcsc.insert(0, "C6022114")
entry_lcsc.bind("<KeyRelease>", _on_lcsc_keyrelease)
ToolTip(entry_lcsc,
        "Einzelne oder mehrere LCSC-IDs.\n"
        "Trennzeichen: Komma, Semikolon oder Leerzeichen.\n"
        "Beispiel: C6022114, C2040, C15234\n"
        "Duplikate werden automatisch entfernt.\n"
        "Bei mehreren IDs erscheint eine Bestätigungsabfrage.")

# ── Row 1: Name (MPN) ────────────────────────────────────────────────────────
ttk.Label(frame_top, text="Name (MPN):").grid(row=1, column=0, sticky="w", **pad)
entry_name = ttk.Entry(frame_top, width=30)
entry_name.grid(row=1, column=1, sticky="w", **pad)
entry_name.insert(0, "C6022114")
entry_name.bind("<Key>", _on_name_keypress)
ttk.Label(frame_top, text="← aus API", foreground="gray").grid(
    row=1, column=2, sticky="w", padx=(0, 8))
ToolTip(entry_name,
        "Dateiname der generierten Library-Dateien (z.B. DRV8317HREER).\n"
        "Wird automatisch mit dem MPN aus der EasyEDA API befüllt\n"
        "sobald eine einzelne LCSC-ID eingegeben wird.\n"
        "Kann manuell überschrieben werden.\n"
        "Bei mehreren IDs: pro Komponente eigener MPN-Name.")

# ── Row 2: Ausgabe-Modus toggle ───────────────────────────────────────────────
ttk.Label(frame_top, text="Ausgabe:").grid(row=2, column=0, sticky="w", **pad)
frame_mode_toggle = ttk.Frame(frame_top)
frame_mode_toggle.grid(row=2, column=1, columnspan=2, sticky="w", **pad)
ttk.Radiobutton(frame_mode_toggle, text="Neue Library",
                variable=var_merge_mode, value=False,
                command=_on_merge_mode_change).pack(side=tk.LEFT, padx=(0, 12))
ttk.Radiobutton(frame_mode_toggle, text="In bestehende Library mergen",
                variable=var_merge_mode, value=True,
                command=_on_merge_mode_change).pack(side=tk.LEFT)

# ── Row 3: Output section (switchable) ───────────────────────────────────────
frame_output_section = ttk.Frame(frame_top)
frame_output_section.grid(row=3, column=0, columnspan=3, sticky="ew")
frame_output_section.columnconfigure(0, weight=1)

# Sub-frame A: New Library (3 separate target dirs)
frame_newlib = ttk.Frame(frame_output_section)
frame_newlib.columnconfigure(1, weight=1)

ttk.Label(frame_newlib, text="Symbols-Ordner:").grid(row=0, column=0, sticky="w", **pad)
entry_newlib_sym = ttk.Entry(frame_newlib, width=44)
entry_newlib_sym.grid(row=0, column=1, sticky="ew", **pad)
ttk.Button(frame_newlib, text="…", width=3, command=browse_newlib_sym).grid(row=0, column=2, **pad)
ToolTip(entry_newlib_sym,
        "Ordner wo die Symbol-Datei abgelegt wird.\n"
        "Ergebnis: <Ordner>\\MPN.kicad_sym\n\n"
        "Beispiel: C:\\…\\Kicad Data\\Symbols\\")
entry_newlib_sym.bind("<FocusOut>", lambda _: _save_config())

ttk.Label(frame_newlib, text="Footprints-Ordner:").grid(row=1, column=0, sticky="w", **pad)
entry_newlib_fp = ttk.Entry(frame_newlib, width=44)
entry_newlib_fp.grid(row=1, column=1, sticky="ew", **pad)
ttk.Button(frame_newlib, text="…", width=3, command=browse_newlib_fp).grid(row=1, column=2, **pad)
ToolTip(entry_newlib_fp,
        "Ordner wo der Footprint-Unterordner erstellt wird.\n"
        "Ergebnis: <Ordner>\\MPN.pretty\\\n\n"
        "Beispiel: C:\\…\\Kicad Data\\Footprints\\")
entry_newlib_fp.bind("<FocusOut>", lambda _: _save_config())

ttk.Label(frame_newlib, text="3D-Ordner:").grid(row=2, column=0, sticky="w", **pad)
entry_newlib_3d = ttk.Entry(frame_newlib, width=44)
entry_newlib_3d.grid(row=2, column=1, sticky="ew", **pad)
ttk.Button(frame_newlib, text="…", width=3, command=browse_newlib_3d).grid(row=2, column=2, **pad)
ToolTip(entry_newlib_3d,
        "Ordner wo der 3D-Modell-Unterordner erstellt wird.\n"
        "Ergebnis: <Ordner>\\MPN.3dshapes\\\n\n"
        "Die 3D-Variable (unten) muss auf diesen Ordner zeigen.\n"
        "Beispiel: C:\\…\\Kicad Data\\3D Data\\")
entry_newlib_3d.bind("<FocusOut>", lambda _: _save_config())

# Sub-frame B: Merge into existing libraries
frame_merge = ttk.Frame(frame_output_section)
frame_merge.columnconfigure(1, weight=1)

ttk.Label(frame_merge, text="Symbol-Lib:").grid(row=0, column=0, sticky="w", **pad)
entry_merge_sym = ttk.Entry(frame_merge, width=44)
entry_merge_sym.grid(row=0, column=1, sticky="ew", **pad)
ttk.Button(frame_merge, text="…", width=3, command=browse_merge_sym).grid(row=0, column=2, **pad)
ToolTip(entry_merge_sym,
        "Ziel-Symbol-Library (.kicad_sym Datei).\n"
        "Das neue Symbol wird in diese Datei eingemergt.\n"
        "Die Datei wird erstellt falls sie noch nicht existiert.\n"
        "Beispiel: C:/…/Kicad Data/Symbols/Mycomponents.kicad_sym")
entry_merge_sym.bind("<FocusOut>", lambda _: _save_config())

ttk.Label(frame_merge, text="Footprint-Lib:").grid(row=1, column=0, sticky="w", **pad)
entry_merge_fp = ttk.Entry(frame_merge, width=44)
entry_merge_fp.grid(row=1, column=1, sticky="ew", **pad)
ttk.Button(frame_merge, text="…", width=3, command=browse_merge_fp).grid(row=1, column=2, **pad)
ToolTip(entry_merge_fp,
        "Ziel-Footprint-Library (.pretty Ordner).\n"
        "Alle .kicad_mod Dateien werden in diesen Ordner kopiert.\n"
        "Der Ordner wird erstellt falls er noch nicht existiert.\n"
        "Beispiel: C:/…/Kicad Data/Footprints/Mycomponents.pretty")
entry_merge_fp.bind("<FocusOut>", lambda _: _save_config())

ttk.Label(frame_merge, text="3D-Ordner:").grid(row=2, column=0, sticky="w", **pad)
entry_merge_3d = ttk.Entry(frame_merge, width=44)
entry_merge_3d.grid(row=2, column=1, sticky="ew", **pad)
ttk.Button(frame_merge, text="…", width=3, command=browse_merge_3d).grid(row=2, column=2, **pad)
ToolTip(entry_merge_3d,
        "Ziel-3D-Ordner (.3dshapes Ordner).\n"
        "Alle 3D-Modelle (.wrl / .step) werden in diesen Ordner kopiert.\n"
        "Der Ordner wird erstellt falls er noch nicht existiert.\n"
        "Beispiel: C:/…/Kicad Data/3D Data/Mycomponents.3dshapes\n\n"
        "Die 3D-Variable (unten) muss auf den ÜBERGEORDNETEN Ordner zeigen,\n"
        "also z.B. auf 'C:/…/Kicad Data/3D Data/'.")
entry_merge_3d.bind("<FocusOut>", lambda _: _save_config())

# Initially show new-library frame; load config and apply
frame_newlib.pack(fill=tk.X)

# ── Row 4: Import mode ───────────────────────────────────────────────────────
ttk.Label(frame_top, text="Import:").grid(row=4, column=0, sticky="w", **pad)
frame_mode = ttk.Frame(frame_top)
frame_mode.grid(row=4, column=1, columnspan=2, sticky="w", **pad)
var_mode = tk.StringVar(value="full")
for val, lbl, tip in [
    ("full",       "Alles",      "Symbol + Footprint + 3D-Modell importieren (--full)"),
    ("symbol",     "Symbol",     "Nur das KiCad-Symbol (.kicad_sym) importieren"),
    ("footprint",  "Footprint",  "Nur den Footprint (.kicad_mod) importieren"),
    ("3d",         "3D-Modell",  "Nur das 3D-Modell (.wrl / .step) importieren"),
]:
    rb = ttk.Radiobutton(frame_mode, text=lbl, variable=var_mode, value=val)
    rb.pack(side=tk.LEFT, padx=4)
    ToolTip(rb, tip)

# ── Row 5: Checkboxes ────────────────────────────────────────────────────────
frame_opts = ttk.Frame(frame_top)
frame_opts.grid(row=5, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 0))
var_overwrite = tk.BooleanVar()
var_cache     = tk.BooleanVar()
var_projrel   = tk.BooleanVar()
var_debug     = tk.BooleanVar()
var_verbose   = tk.BooleanVar()

for lbl, var, tip in [
    ("Überschreiben", var_overwrite,
     "Bestehende Komponente überschreiben (--overwrite).\n"
     "Ohne diese Option schlägt der Import fehl, wenn die Komponente bereits existiert."),
    ("Cache",         var_cache,
     "API-Antworten lokal zwischenspeichern (--use-cache).\n"
     "Beschleunigt Wiederholungen, verhindert unnötige Netzwerkzugriffe.\n"
     "Cache liegt in .easyeda_cache/ im aktuellen Verzeichnis."),
    ("Proj-relativ",  var_projrel,
     "3D-Pfad relativ zum KiCad-Projekt speichern (--project-relative).\n"
     "Sinnvoll nur wenn --output innerhalb des Projektordners liegt.\n"
     "Verwendet ${KIPRJMOD} als Basis."),
    ("Debug",         var_debug,
     "Ausführliches Debug-Logging von easyeda2kicad aktivieren (--debug).\n"
     "Nützlich bei Problemen mit dem API-Abruf oder der Konvertierung."),
]:
    cb = ttk.Checkbutton(frame_opts, text=lbl, variable=var)
    cb.pack(side=tk.LEFT, padx=4)
    ToolTip(cb, tip)

ttk.Separator(frame_opts, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
cb_v = ttk.Checkbutton(frame_opts, text="Verbose Log", variable=var_verbose)
cb_v.pack(side=tk.LEFT, padx=4)
ToolTip(cb_v,
        "Alle Ausgaben von easyeda2kicad anzeigen.\n"
        "Ohne diese Option: nur [INFO]/[WARNING]/[ERROR]-Zeilen sichtbar.")
ttk.Separator(frame_opts, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
ttk.Checkbutton(frame_opts, text="Tooltips", variable=var_tooltips).pack(side=tk.LEFT, padx=4)

# ── Row 6: 3D variable ───────────────────────────────────────────────────────
ttk.Label(frame_top, text="3D-Variable:").grid(row=6, column=0, sticky="w", **pad)
entry_3dvar = ttk.Entry(frame_top, width=36)
entry_3dvar.grid(row=6, column=1, sticky="w", **pad)
entry_3dvar.insert(0, DEFAULT_3D_VAR)
ToolTip(entry_3dvar,
        "KiCad-Pfadvariable für 3D-Modelle.\n\n"
        "easyeda2kicad schreibt in .kicad_mod-Dateien den absoluten Ausgabepfad\n"
        "als 3D-Modell-Pfad – das ist ein bekannter Bug. Dieser Importer ersetzt\n"
        "diesen absoluten Pfad automatisch durch die hier eingestellte Variable.\n\n"
        "Neue Library: Variable muss auf den Ausgabeordner zeigen.\n"
        "Merge-Modus:  Variable muss auf den Ordner ÜBER dem .3dshapes-Ordner zeigen.\n\n"
        "Beispiel: ${KICAD_USER_3DMODEL_DIR}\n"
        "→ In KiCad unter Preferences → Configure Paths setzen.")

# ── Row 7: Custom fields ─────────────────────────────────────────────────────
ttk.Label(frame_top, text="Custom Fields:").grid(row=7, column=0, sticky="w", **pad)
entry_custom = ttk.Entry(frame_top, width=44)
entry_custom.grid(row=7, column=1, columnspan=2, sticky="ew", **pad)
ToolTip(entry_custom,
        "Eigene Symbol-Properties hinzufügen (--custom-field).\n"
        "Leerzeichen-getrennte KEY:VALUE Paare.\n"
        "Beispiel: Mfr:TI Package:QFN-36 Datasheet:https://ti.com/lit/ds/...")
ttk.Label(frame_top, text="z.B.  Mfr:TI  Package:QFN-36", foreground="gray").grid(
    row=8, column=1, columnspan=2, sticky="w", padx=8)

# ── Row 9: Buttons ───────────────────────────────────────────────────────────
frame_btn = ttk.Frame(frame_top)
frame_btn.grid(row=9, column=0, columnspan=3, pady=(8, 0))
btn_run = ttk.Button(frame_btn, text="Import starten", command=run_import)
btn_run.pack(side=tk.LEFT, padx=4)
ttk.Button(frame_btn, text="Log leeren", command=clear_log).pack(side=tk.LEFT, padx=4)

# ── Log area ─────────────────────────────────────────────────────────────────
frame_log = ttk.LabelFrame(root, text="Ausgabe", padding=6)
frame_log.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

text_log = scrolledtext.ScrolledText(frame_log, width=72, height=10, state=tk.DISABLED,
                                     font=("Consolas", 9), wrap=tk.WORD)
text_log.pack(fill=tk.BOTH, expand=True)
text_log.tag_config("error", foreground="#cc0000")
text_log.tag_config("ok",    foreground="#007700")
text_log.tag_config("info",  foreground="#0055aa")

# ── Apply persisted config ────────────────────────────────────────────────────
_cfg = _load_config()
if _cfg.get("newlib_sym_dir"):
    entry_newlib_sym.insert(0, _cfg["newlib_sym_dir"])
if _cfg.get("newlib_fp_dir"):
    entry_newlib_fp.insert(0, _cfg["newlib_fp_dir"])
if _cfg.get("newlib_3d_dir"):
    entry_newlib_3d.insert(0, _cfg["newlib_3d_dir"])
if _cfg.get("merge_sym_lib"):
    entry_merge_sym.insert(0, _cfg["merge_sym_lib"])
if _cfg.get("merge_fp_lib"):
    entry_merge_fp.insert(0, _cfg["merge_fp_lib"])
if _cfg.get("merge_3d_dir"):
    entry_merge_3d.insert(0, _cfg["merge_3d_dir"])
if _cfg.get("merge_mode"):
    var_merge_mode.set(True)
    frame_newlib.pack_forget()
    frame_merge.pack(fill=tk.X)

root.protocol("WM_DELETE_WINDOW", lambda: (_save_config(), root.destroy()))

# Trigger initial MPN fetch for pre-filled ID
root.after(500, lambda: _trigger_mpn_fetch(entry_lcsc.get().strip()))

root.mainloop()
