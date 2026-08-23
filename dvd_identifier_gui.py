#!/usr/bin/env python3
"""
dvd_identifier_gui.py
=====================
A simple, self-contained desktop GUI (Tkinter) for the DVD episode identifier -
the point-and-click front end for organising DVD rips for Plex on Windows.

Why Tkinter?
    Tkinter ships with the standard CPython installer on Windows, so there is
    NOTHING extra to "pip install" for the window itself. The identification
    engine still needs the project's runtime deps (ffmpeg, fpcalc, vosk, pydub)
    exactly as the command-line tools do.

What it does (three tabs)
    1. Identify   - pick your fingerprint DB + a folder (or single file) of DVD
                    rips, tweak a couple of options, hit Identify. Results appear
                    in a sortable table with a per-file "needs review" flag, and
                    can be exported to CSV/JSON or auto-renamed for Plex.
    2. Build      - grow the reference library: add subtitle files (.srt/.vtt)
                    for phonetic matching and/or video files for acoustic
                    matching. Runs the existing create_*.py tools under the hood.
    3. Log        - the raw engine log for the last run (handy when something
                    needs debugging).

Run it:
    python dvd_identifier_gui.py
or double-click  DVD_Identifier.bat  on Windows.
"""
from __future__ import annotations

import os
import sys
import queue
import shutil
import logging
import threading
import subprocess
from dataclasses import dataclass
from types import SimpleNamespace
from typing import List, Optional

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Make sure we can import the project modules regardless of the CWD Windows uses
# when you double-click the .bat / .py.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Engine imports (the same code the CLI uses).
import identify_dvd_episodes as dvd            # noqa: E402
from identify_dvd_episodes import (            # noqa: E402
    FileResult, discover_media, identify_one, write_csv, write_json,
    episode_id_str,
)
from fingerprint_core import (                 # noqa: E402
    FingerprintDB, FingerprintConfig, load_config,
)
import acoustic_fingerprint as af             # noqa: E402
from acoustic_fingerprint import AcousticConfig, FpcalcNotFoundError  # noqa: E402


APP_TITLE = "DVD Episode Identifier - Plex helper"
DEFAULT_DB = os.path.join(HERE, "fingerprints.db")
DEFAULT_CONFIG = os.path.join(HERE, "config.json")


# ---------------------------------------------------------------------------
# Logging plumbing: route the engine's logging + our messages into a queue that
# the Tk main loop drains onto the Log tab (Tk is not thread-safe, so the worker
# thread never touches widgets directly).
# ---------------------------------------------------------------------------
class QueueLogHandler(logging.Handler):
    def __init__(self, q: "queue.Queue[str]"):
        super().__init__()
        self.q = q

    def emit(self, record):
        try:
            self.q.put(self.format(record))
        except Exception:
            pass


class DvdIdentifierGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x680")
        self.minsize(820, 560)

        self.log_q: "queue.Queue[str]" = queue.Queue()
        # Cross-thread UI events. Worker threads must NEVER touch Tk (not even
        # self.after, which registers a command off-thread); they only put
        # ("kind", payload) tuples here and the main-thread pump applies them.
        self.ui_q: "queue.Queue[tuple]" = queue.Queue()
        self.results: List[FileResult] = []
        self._worker: Optional[threading.Thread] = None

        # shared state vars
        self.db_var = tk.StringVar(value=DEFAULT_DB if os.path.exists(DEFAULT_DB) else "")
        self.config_var = tk.StringVar(value=DEFAULT_CONFIG if os.path.exists(DEFAULT_CONFIG) else "")
        self.source_var = tk.StringVar(value="")
        self.samples_var = tk.IntVar(value=5)
        self.sample_len_var = tk.DoubleVar(value=12.0)
        self.phonetic_var = tk.BooleanVar(value=True)
        self.review_conf_var = tk.DoubleVar(value=0.35)
        self.min_agree_var = tk.DoubleVar(value=0.5)
        self.status_var = tk.StringVar(value="Ready.")

        self._install_logging()
        self._build_ui()
        self.after(120, self._drain_log)
        self.after(80, self._pump_ui)

    # ----- logging -----
    def _install_logging(self):
        handler = QueueLogHandler(self.log_q)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                                               "%H:%M:%S"))
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        # avoid duplicate handlers if the window is recreated
        root.handlers = [h for h in root.handlers if not isinstance(h, QueueLogHandler)]
        root.addHandler(handler)

    def log(self, msg: str):
        self.log_q.put(msg)

    def _drain_log(self):
        try:
            while True:
                line = self.log_q.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", line + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(120, self._drain_log)

    def _pump_ui(self):
        """Drain cross-thread UI events on the main thread (Tk-safe)."""
        try:
            while True:
                kind, payload = self.ui_q.get_nowait()
                if kind == "status":
                    self.status_var.set(payload)
                elif kind == "row":
                    self._add_result_row(payload)
                elif kind == "identify_done":
                    self._on_identify_done(payload)
                elif kind == "build":
                    self._append_build(payload)
                elif kind == "build_done":
                    self.build_progress.stop()
                    self.status_var.set("Library task finished.")
        except queue.Empty:
            pass
        self.after(80, self._pump_ui)

    # ----- UI construction -----
    def _build_ui(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self.tab_identify = ttk.Frame(nb)
        self.tab_build = ttk.Frame(nb)
        self.tab_log = ttk.Frame(nb)
        nb.add(self.tab_identify, text="  Identify  ")
        nb.add(self.tab_build, text="  Build library  ")
        nb.add(self.tab_log, text="  Log  ")

        self._build_identify_tab()
        self._build_build_tab()
        self._build_log_tab()

        status = ttk.Frame(self)
        status.pack(fill="x", side="bottom")
        ttk.Separator(status, orient="horizontal").pack(fill="x")
        ttk.Label(status, textvariable=self.status_var, anchor="w",
                  padding=(8, 4)).pack(fill="x")

    def _build_identify_tab(self):
        f = self.tab_identify

        # --- reference DB ---
        top = ttk.LabelFrame(f, text="Reference fingerprint database", padding=8)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Entry(top, textvariable=self.db_var).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(top, text="Browse...", command=self._pick_db).grid(row=0, column=1)
        top.columnconfigure(0, weight=1)

        # --- source ---
        src = ttk.LabelFrame(f, text="DVD rips to identify", padding=8)
        src.pack(fill="x", padx=8, pady=6)
        ttk.Entry(src, textvariable=self.source_var).grid(row=0, column=0, columnspan=2,
                                                          sticky="ew", padx=(0, 6))
        ttk.Button(src, text="Folder...", command=self._pick_dir).grid(row=0, column=2, padx=(0, 4))
        ttk.Button(src, text="File...", command=self._pick_file).grid(row=0, column=3)
        src.columnconfigure(0, weight=1)

        # --- options ---
        opt = ttk.LabelFrame(f, text="Options", padding=8)
        opt.pack(fill="x", padx=8, pady=6)
        ttk.Label(opt, text="Samples per file:").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(opt, from_=1, to=15, width=5, textvariable=self.samples_var).grid(row=0, column=1, sticky="w", padx=(4, 16))
        ttk.Label(opt, text="Sample length (s):").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(opt, from_=4, to=30, width=5, textvariable=self.sample_len_var).grid(row=0, column=3, sticky="w", padx=(4, 16))
        ttk.Checkbutton(opt, text="Phonetic fallback (dialogue)",
                        variable=self.phonetic_var).grid(row=0, column=4, sticky="w", padx=(4, 0))
        ttk.Label(opt, text="Review below confidence:").grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Spinbox(opt, from_=0.05, to=0.95, increment=0.05, width=5,
                    textvariable=self.review_conf_var, format="%.2f").grid(row=1, column=2, sticky="w", pady=(6, 0))

        # --- actions ---
        act = ttk.Frame(f)
        act.pack(fill="x", padx=8, pady=(2, 6))
        self.run_btn = ttk.Button(act, text="Identify", command=self._run_identify)
        self.run_btn.pack(side="left")
        self.progress = ttk.Progressbar(act, mode="indeterminate", length=160)
        self.progress.pack(side="left", padx=10)
        ttk.Button(act, text="Export CSV...", command=self._export_csv).pack(side="right")
        ttk.Button(act, text="Export JSON...", command=self._export_json).pack(side="right", padx=(0, 6))
        ttk.Button(act, text="Rename for Plex...", command=self._rename_plex).pack(side="right", padx=(0, 6))

        # --- results table ---
        cols = ("file", "episode", "title", "confidence", "method", "agreement", "review", "notes")
        tv = ttk.Treeview(f, columns=cols, show="headings", height=12)
        headings = {
            "file": ("File", 220), "episode": ("Episode", 80),
            "title": ("Title", 170), "confidence": ("Conf.", 60),
            "method": ("Method", 80), "agreement": ("Agree", 60),
            "review": ("Review?", 70), "notes": ("Notes", 180),
        }
        for c, (txt, w) in headings.items():
            tv.heading(c, text=txt)
            tv.column(c, width=w, anchor="w")
        tv.tag_configure("review", background="#fff3cd")
        tv.tag_configure("ok", background="#e7f6e7")
        vsb = ttk.Scrollbar(f, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        tv.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        vsb.pack(side="left", fill="y", pady=(0, 8), padx=(0, 8))
        self.tree = tv

    def _build_build_tab(self):
        f = self.tab_build
        intro = ("Grow the reference library. Phonetic matching needs subtitle "
                 "files (.srt/.vtt); acoustic matching needs the actual video/"
                 "audio files. You can do either or both.")
        ttk.Label(f, text=intro, wraplength=900, justify="left",
                  padding=(4, 8)).pack(fill="x", padx=8)

        subs = ttk.LabelFrame(f, text="1) Add subtitles  (phonetic reference)", padding=8)
        subs.pack(fill="x", padx=8, pady=6)
        self.subs_var = tk.StringVar()
        ttk.Entry(subs, textvariable=self.subs_var).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(subs, text="Folder...", command=lambda: self._pick_into(self.subs_var, True)).grid(row=0, column=1, padx=(0, 4))
        ttk.Button(subs, text="File...", command=lambda: self._pick_into(self.subs_var, False,
                   [("Subtitles", "*.srt *.vtt"), ("All", "*.*")])).grid(row=0, column=2)
        ttk.Button(subs, text="Add subtitles to library",
                   command=self._build_subs).grid(row=1, column=0, sticky="w", pady=(8, 0))
        subs.columnconfigure(0, weight=1)

        vids = ttk.LabelFrame(f, text="2) Add video/audio  (acoustic reference)", padding=8)
        vids.pack(fill="x", padx=8, pady=6)
        self.vids_var = tk.StringVar()
        ttk.Entry(vids, textvariable=self.vids_var).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(vids, text="Folder...", command=lambda: self._pick_into(self.vids_var, True)).grid(row=0, column=1, padx=(0, 4))
        ttk.Button(vids, text="File...", command=lambda: self._pick_into(self.vids_var, False)).grid(row=0, column=2)
        ttk.Button(vids, text="Add acoustic fingerprints to library",
                   command=self._build_acoustic).grid(row=1, column=0, sticky="w", pady=(8, 0))
        vids.columnconfigure(0, weight=1)

        self.build_progress = ttk.Progressbar(f, mode="indeterminate", length=200)
        self.build_progress.pack(padx=8, pady=4, anchor="w")

        ttk.Label(f, text="Build output (see the Log tab for full detail):",
                  padding=(4, 4)).pack(fill="x", padx=8)
        self.build_out = tk.Text(f, height=10, state="disabled", wrap="word")
        self.build_out.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _build_log_tab(self):
        f = self.tab_log
        self.log_text = tk.Text(f, state="disabled", wrap="none")
        vsb = ttk.Scrollbar(f, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=vsb.set)
        self.log_text.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        vsb.pack(side="left", fill="y", pady=8, padx=(0, 8))

    # ----- file pickers -----
    def _pick_db(self):
        p = filedialog.askopenfilename(title="Select fingerprint DB",
                                       filetypes=[("SQLite DB", "*.db"), ("All", "*.*")])
        if p:
            self.db_var.set(p)

    def _pick_dir(self):
        p = filedialog.askdirectory(title="Select folder of DVD rips")
        if p:
            self.source_var.set(p)

    def _pick_file(self):
        p = filedialog.askopenfilename(title="Select a video/audio file")
        if p:
            self.source_var.set(p)

    def _pick_into(self, var, is_dir, filetypes=None):
        if is_dir:
            p = filedialog.askdirectory()
        else:
            p = filedialog.askopenfilename(filetypes=filetypes or [("All", "*.*")])
        if p:
            var.set(p)

    # ----- identify workflow -----
    def _run_identify(self):
        if self._worker and self._worker.is_alive():
            return
        db_path = self.db_var.get().strip()
        source = self.source_var.get().strip()
        if not db_path or not os.path.exists(db_path):
            messagebox.showerror(APP_TITLE, "Please select a valid fingerprint DB.")
            return
        if not source or not os.path.exists(source):
            messagebox.showerror(APP_TITLE, "Please select a folder or file to identify.")
            return

        self.run_btn.configure(state="disabled")
        self.progress.start(12)
        self.status_var.set("Identifying...")
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.results = []

        # Snapshot ALL Tk variables here, on the main thread. Tk is not
        # thread-safe, so the worker must never touch a tk.Variable directly.
        n = max(1, int(self.samples_var.get()))
        points = [0.5] if n == 1 else [round((i + 1) / (n + 1), 4) for i in range(n)]
        params = SimpleNamespace(
            config_path=self.config_var.get().strip() or None,
            points=points,
            sample_len=float(self.sample_len_var.get()),
            no_phonetic=(not self.phonetic_var.get()),
            review_confidence=float(self.review_conf_var.get()),
            min_agreement=float(self.min_agree_var.get()),
            runtime_tolerance=4.0,
        )

        self._worker = threading.Thread(target=self._identify_worker,
                                        args=(db_path, source, params),
                                        daemon=True)
        self._worker.start()

    def _identify_worker(self, db_path: str, source: str, params: SimpleNamespace):
        try:
            cfg = load_config(params.config_path)
            db = FingerprintDB(db_path)
            fp_cfg = FingerprintConfig.from_config(cfg)
            ac_cfg = AcousticConfig.from_config(cfg)
            try:
                af.check_fpcalc(ac_cfg.fpcalc_path)
            except FpcalcNotFoundError as exc:
                self._finish_identify(error=f"fpcalc not found: {exc}\n"
                                      "See INSTALL_WINDOWS.md to set it up.")
                db.close()
                return

            args = SimpleNamespace(
                points=params.points,
                sample_len=params.sample_len,
                no_phonetic=params.no_phonetic,
                review_confidence=params.review_confidence,
                min_agreement=params.min_agreement,
                runtime_tolerance=params.runtime_tolerance,
            )

            if os.path.isdir(source):
                media = discover_media(source)
            else:
                media = [source]
            if not media:
                self._finish_identify(error="No media files found in that folder.")
                db.close()
                return

            self.log(f"Identifying {len(media)} file(s) against {os.path.basename(db_path)}")
            transcriber_box: dict = {}
            results: List[FileResult] = []
            for i, path in enumerate(media, 1):
                self.ui_q.put(("status",
                               f"Identifying {i}/{len(media)}: {os.path.basename(path)}"))
                try:
                    r = identify_one(path, db, fp_cfg, ac_cfg, cfg, args,
                                     transcriber_box, None)
                    results.append(r)
                    self.ui_q.put(("row", r))
                except FpcalcNotFoundError as exc:
                    self.results = results
                    self._finish_identify(error=f"fpcalc error: {exc}")
                    db.close()
                    return
                except Exception as exc:
                    self.log(f"ERROR on {os.path.basename(path)}: {exc}")
            db.close()
            self.results = results
            self._finish_identify(count=len(results))
        except Exception as exc:
            self._finish_identify(error=str(exc))

    def _add_result_row(self, r: FileResult):
        row = r.to_row()
        tag = "review" if r.needs_review else "ok"
        self.tree.insert("", "end", tags=(tag,), values=(
            row["filename"], row["episode_id"], row["title"],
            f"{row['confidence']:.0%}", row["method"], row["agreement"],
            "REVIEW" if r.needs_review else "ok", row["notes"],
        ))

    def _finish_identify(self, count: int = 0, error: Optional[str] = None):
        # Called from the worker thread -> hand off to the main-thread pump.
        self.ui_q.put(("identify_done", {"count": count, "error": error}))

    def _on_identify_done(self, payload: dict):
        """Main-thread completion handler for an identify run."""
        self.progress.stop()
        self.run_btn.configure(state="normal")
        error = payload.get("error")
        count = payload.get("count", 0)
        if error:
            self.status_var.set("Error.")
            messagebox.showerror(APP_TITLE, error)
        else:
            ok = sum(1 for r in self.results if not r.needs_review)
            self.status_var.set(
                f"Done. {ok}/{count} identified confidently; "
                f"{count - ok} need review.")

    # ----- exports -----
    def _export_csv(self):
        if not self.results:
            messagebox.showinfo(APP_TITLE, "Nothing to export yet.")
            return
        p = filedialog.asksaveasfilename(defaultextension=".csv",
                                         filetypes=[("CSV", "*.csv")],
                                         initialfile="episode_map.csv")
        if p:
            write_csv(self.results, p)
            self.status_var.set(f"CSV written: {p}")

    def _export_json(self):
        if not self.results:
            messagebox.showinfo(APP_TITLE, "Nothing to export yet.")
            return
        p = filedialog.asksaveasfilename(defaultextension=".json",
                                         filetypes=[("JSON", "*.json")],
                                         initialfile="episode_map.json")
        if p:
            write_json(self.results, p)
            self.status_var.set(f"JSON written: {p}")

    def _rename_plex(self):
        if not self.results:
            messagebox.showinfo(APP_TITLE, "Identify some files first.")
            return
        renamable = [r for r in self.results
                     if r.guess and not r.needs_review
                     and r.guess.season is not None and r.guess.episode is not None]
        skipped = len(self.results) - len(renamable)
        if not renamable:
            messagebox.showinfo(APP_TITLE,
                                "No confidently-identified TV episodes to rename.\n"
                                "(Files flagged for review are never touched.)")
            return
        dest = filedialog.askdirectory(
            title="Choose destination folder for renamed copies")
        if not dest:
            return
        msg = (f"Copy {len(renamable)} identified file(s) into a Plex layout "
               f"under:\n{dest}\n\n"
               f"{skipped} file(s) flagged for review will be skipped.\n\n"
               "Originals are COPIED, never moved. Continue?")
        if not messagebox.askyesno(APP_TITLE, msg):
            return
        done, errors = 0, []
        for r in renamable:
            g = r.guess
            show = g.title or "Show"
            season_dir = os.path.join(dest, self._safe(show),
                                      f"Season {g.season:02d}")
            os.makedirs(season_dir, exist_ok=True)
            ext = os.path.splitext(r.path)[1]
            newname = f"{self._safe(show)} - {episode_id_str(g.season, g.episode)}{ext}"
            target = os.path.join(season_dir, newname)
            try:
                shutil.copy2(r.path, target)
                done += 1
                self.log(f"copied -> {target}")
            except Exception as exc:
                errors.append(f"{r.filename}: {exc}")
        summary = f"Copied {done} file(s) into Plex layout under:\n{dest}"
        if errors:
            summary += "\n\nErrors:\n" + "\n".join(errors[:8])
        messagebox.showinfo(APP_TITLE, summary)
        self.status_var.set(f"Renamed/copied {done} file(s) for Plex.")

    @staticmethod
    def _safe(name: str) -> str:
        for ch in '<>:"/\\|?*':
            name = name.replace(ch, "_")
        return name.strip().rstrip(".")

    # ----- build library workflow -----
    def _build_subs(self):
        path = self.subs_var.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showerror(APP_TITLE, "Pick a subtitle file or folder first.")
            return
        flag = "--dir" if os.path.isdir(path) else "--file"
        self._run_build([sys.executable, os.path.join(HERE, "create_fingerprint.py"),
                         flag, path, "--db", self.db_var.get().strip() or DEFAULT_DB])

    def _build_acoustic(self):
        path = self.vids_var.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showerror(APP_TITLE, "Pick a video/audio file or folder first.")
            return
        flag = "--dir" if os.path.isdir(path) else "--file"
        self._run_build([sys.executable,
                         os.path.join(HERE, "create_acoustic_fingerprint.py"),
                         flag, path, "--db", self.db_var.get().strip() or DEFAULT_DB])

    def _run_build(self, cmd: List[str]):
        if self._worker and self._worker.is_alive():
            messagebox.showinfo(APP_TITLE, "A task is already running.")
            return
        self.build_progress.start(12)
        self.status_var.set("Building library...")
        self._append_build(f"$ {' '.join(cmd)}\n")
        self._worker = threading.Thread(target=self._build_worker, args=(cmd,),
                                        daemon=True)
        self._worker.start()

    def _build_worker(self, cmd: List[str]):
        try:
            proc = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    bufsize=1)
            for line in proc.stdout:
                self.ui_q.put(("build", line))
                self.log(line.rstrip())
            proc.wait()
            self.ui_q.put(("build", f"\n[exit code {proc.returncode}]\n"))
        except Exception as exc:
            self.ui_q.put(("build", f"\nERROR: {exc}\n"))
        finally:
            self.ui_q.put(("build_done", None))

    def _append_build(self, text: str):
        self.build_out.configure(state="normal")
        self.build_out.insert("end", text)
        self.build_out.see("end")
        self.build_out.configure(state="disabled")


def main():
    app = DvdIdentifierGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
