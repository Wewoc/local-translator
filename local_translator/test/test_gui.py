#!/usr/bin/env python3
"""
test/test_gui.py — Tkinter GUI for the LocalTranslate batch quality runner.

Pick source texts, S1 models, target languages, and mindsets (individual
ones, "General" as the no-domain baseline, and/or "Auto-detect" via
/mindset/detect) by clicking — no config file to get a typo in. Builds the
full cartesian product (source × model × target × mindset) and runs it
against a locally running LocalTranslate server, one translation call at a
time, writing one result .md per combination.

Modeled on GLA-NeedfulThings/mcp-llm-tester/mcp_test_gui.py — same
threading model (worker thread does the actual run, GUI only polls a
queue), same start/stop/resume semantics, same run_<nr>_<date>_<rest>
run-folder naming.

Run: python test_gui.py  (or test_gui.bat on Windows). Requires the
LocalTranslate server running at runner_core.SERVER_URL.
"""

import queue
import re
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import runner_core as core

SOURCE_DIR = Path(__file__).parent / "source"
RESULTS_ROOT = Path(__file__).parent / "results"
PERF_LOG = Path(__file__).parent.parent / "logs" / "perf.csv"

_NO_S2_LABEL = "— no S2 —"

# config.yaml's language display names are German (matching the app's own
# dropdowns) — translated here for the GUI only, so the whole test runner
# reads as one language. Codes (what actually goes to the server) are
# untouched; a label config.yaml adds that isn't in this dict just shows
# up in German rather than crashing.
LANGUAGE_DISPLAY_EN = {
    "Deutsch": "German", "Englisch": "English", "Französisch": "French",
    "Spanisch": "Spanish", "Italienisch": "Italian", "Portugiesisch": "Portuguese",
    "Niederländisch": "Dutch", "Polnisch": "Polish",
    "Russisch": "Russian", "Chinesisch": "Chinese", "Japanisch": "Japanese",
}

# Same six levels as index.html's #coherenceLevelSelect — label -> level int.
COHERENCE_LEVELS = [
    ("1 — Soft", 1), ("2 — Standard", 2), ("3 — Strong", 3),
    ("4 — Rewrite Light", 4), ("5 — Rewrite Medium", 5), ("6 — Rewrite Heavy", 6),
]

# Fallback default for the mindset-detect model if the server's /config
# doesn't report one (config.yaml's pipeline_mindset_model) — kept in sync
# with local_translator/config.yaml's own default.
_FALLBACK_MINDSET_MODEL = "phi4-mini:latest"


def _sort_models_pinned(models: list[str]) -> list[str]:
    """Sorts alphabetically, but any model whose name starts with
    'translategemma' (case-insensitive) always comes first."""
    return sorted(models, key=lambda m: (0, m.lower()) if m.lower().startswith("translategemma") else (1, m.lower()))


def format_elapsed(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


def build_run_folder_name(nr: str, datum: str, freitext: str) -> str:
    nr, datum, freitext = nr.strip(), datum.strip(), freitext.strip()
    parts = [p for p in ["run", nr, datum, freitext] if p]
    return "_".join(parts)


def parse_run_folder_name(name: str) -> tuple[str, str, str] | None:
    match = re.match(r"^run_(\d+)_(\d{8})_(.*)$", name)
    if match is None:
        return None
    return match.groups()


class BatchTestGui:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("LocalTranslate — Batch Quality Test")
        self.root.geometry("1100x760")

        self._sources_all: list[Path] = []
        self._selected_sources: list[Path] = []
        self._models_all: list[str] = []
        self._selected_models: list[str] = []
        self._mindsets_all: list[core.MindsetChoice] = []
        self._selected_mindsets: list[core.MindsetChoice] = []
        self._targets_all: list[tuple[str, str]] = []  # (label, code)
        self._selected_targets: list[tuple[str, str]] = []
        self._source_lang_code = "DE"
        self._mindset_model = ""

        self._event_queue: "queue.Queue[dict]" = queue.Queue()
        self._stop_event: threading.Event | None = None
        self._worker_thread: threading.Thread | None = None
        self._run_start_time: float | None = None
        self._active_output_dir: Path | None = None
        self._resume_available = False

        self._build_widgets()
        self._reload_everything()
        self._suggest_run_fields()
        self._check_resumable_run()
        self._poll_queue()

    # ── Widget layout ────────────────────────────────────────────────

    def _build_widgets(self) -> None:
        top = tk.Frame(self.root)
        top.pack(fill="both", expand=True, padx=10, pady=(10, 5))

        self.source_listbox, self._sources_selected_box = self._build_column(
            top, "Source texts (test/source/*.md, *.txt — click to multi-select)")
        self.model_listbox, self._models_selected_box = self._build_column(
            top, "S1 models (click to multi-select)")
        self.mindset_listbox, self._mindsets_selected_box = self._build_column(
            top, "Mindset (click to multi-select — General = ohne)")
        self.target_listbox, self._targets_selected_box = self._build_column(
            top, "Target languages (click to multi-select)")

        self.source_listbox.bind("<<ListboxSelect>>", lambda e: self._toggle(
            self.source_listbox, self._sources_all, self._selected_sources,
            self._sources_selected_box, key=lambda p: p.name))
        self.model_listbox.bind("<<ListboxSelect>>", lambda e: self._toggle(
            self.model_listbox, self._models_all, self._selected_models,
            self._models_selected_box, key=lambda m: m))
        self.mindset_listbox.bind("<<ListboxSelect>>", lambda e: self._toggle_mindset())
        self.target_listbox.bind("<<ListboxSelect>>", lambda e: self._toggle(
            self.target_listbox, self._targets_all, self._selected_targets,
            self._targets_selected_box, key=lambda t: f"{t[0]} ({t[1]})"))

        # Options row: source language, S2 model, mindset-detect model
        opts = tk.LabelFrame(self.root, text="Options (apply to every combination)")
        opts.pack(fill="x", padx=10, pady=5)

        tk.Label(opts, text="Source language:").grid(row=0, column=0, sticky="e", padx=(5, 2), pady=5)
        self.source_lang_var = tk.StringVar(value="German")
        self.source_lang_combo = ttk.Combobox(opts, textvariable=self.source_lang_var,
                                               state="readonly", width=15)
        self.source_lang_combo.grid(row=0, column=1, sticky="w", pady=5)
        self.source_lang_var.trace_add("write", lambda *_: self._update_coherence_hint())

        tk.Label(opts, text="S2 model (optional):").grid(row=0, column=2, sticky="e", padx=(15, 2), pady=5)
        self.s2_var = tk.StringVar(value=_NO_S2_LABEL)
        self.s2_combo = ttk.Combobox(opts, textvariable=self.s2_var, state="readonly", width=25)
        self.s2_combo.grid(row=0, column=3, sticky="w", pady=5)

        tk.Label(opts, text="Mindset-detect model (empty = active S1 model):").grid(
            row=0, column=4, sticky="e", padx=(15, 2), pady=5)
        self.mindset_model_var = tk.StringVar(value="")
        self.mindset_model_combo = ttk.Combobox(opts, textvariable=self.mindset_model_var, width=25)
        self.mindset_model_combo.grid(row=0, column=5, sticky="w", padx=(0, 5), pady=5)

        self.reload_button = tk.Button(opts, text="Reload from server", command=self._reload_everything)
        self.reload_button.grid(row=0, column=6, sticky="w", padx=(15, 5), pady=5)

        # Coherence level: always sent along with every /translate/chunk call
        # (see run_batch_session()) — the server only ever *uses* it when a
        # combination's source language equals its target language (app.py's
        # own coherence_mode check), so nothing needs to be enforced here.
        # This picker just needs to exist; the hint label tells you whether
        # it's currently relevant to your selection.
        tk.Label(opts, text="Coherence level (only applies when source = target):").grid(
            row=1, column=0, columnspan=2, sticky="e", padx=(5, 2), pady=5)
        self.coherence_level_var = tk.StringVar(value=COHERENCE_LEVELS[1][0])
        self.coherence_level_combo = ttk.Combobox(
            opts, textvariable=self.coherence_level_var, state="readonly", width=18,
            values=[label for label, _level in COHERENCE_LEVELS])
        self.coherence_level_combo.grid(row=1, column=2, sticky="w", pady=5)

        self.coherence_hint_label = tk.Label(opts, text="", font=("TkDefaultFont", 9, "italic"), fg="#555555")
        self.coherence_hint_label.grid(row=1, column=3, columnspan=4, sticky="w", padx=(10, 5), pady=5)

        self.matrix_label = tk.Label(opts, text="0 combination(s)", font=("TkDefaultFont", 9, "italic"))
        self.matrix_label.grid(row=2, column=0, columnspan=7, sticky="w", padx=5, pady=(0, 5))

        # Run folder fields
        run_frame = tk.LabelFrame(self.root, text="Run folder")
        run_frame.pack(fill="x", padx=10, pady=5)

        tk.Label(run_frame, text="No.:").grid(row=0, column=0, sticky="e", padx=(5, 2), pady=5)
        self.nr_var = tk.StringVar()
        self.nr_entry = tk.Entry(run_frame, textvariable=self.nr_var, width=6)
        self.nr_entry.grid(row=0, column=1, sticky="w", pady=5)
        self.nr_var.trace_add("write", lambda *_: self._update_preview())

        tk.Label(run_frame, text="Date:").grid(row=0, column=2, sticky="e", padx=(10, 2), pady=5)
        self.datum_var = tk.StringVar()
        self.datum_entry = tk.Entry(run_frame, textvariable=self.datum_var, width=10)
        self.datum_entry.grid(row=0, column=3, sticky="w", pady=5)
        self.datum_var.trace_add("write", lambda *_: self._update_preview())

        tk.Label(run_frame, text="Rest:").grid(row=0, column=4, sticky="e", padx=(10, 2), pady=5)
        self.freitext_var = tk.StringVar()
        self.freitext_entry = tk.Entry(run_frame, textvariable=self.freitext_var, width=30)
        self.freitext_entry.grid(row=0, column=5, sticky="w", padx=(0, 5), pady=5)
        self.freitext_var.trace_add("write", lambda *_: self._update_preview())

        self.preview_label = tk.Label(run_frame, text="", font=("TkDefaultFont", 9, "italic"), fg="#555555")
        self.preview_label.grid(row=1, column=0, columnspan=6, sticky="w", padx=5, pady=(0, 5))

        # Controls
        control_frame = tk.Frame(self.root)
        control_frame.pack(fill="x", padx=10, pady=5)
        self.start_button = tk.Button(control_frame, text="Start new run",
                                       command=self._on_start_new, bg="#2e7d32", fg="white")
        self.start_button.pack(side="left", padx=(0, 5))
        self.stop_button = tk.Button(control_frame, text="Stop", command=self._on_stop,
                                      state="disabled", bg="#c62828", fg="white")
        self.stop_button.pack(side="left", padx=5)
        self.resume_button = tk.Button(control_frame, text="Resume run",
                                        command=self._on_resume, state="disabled")
        self.resume_button.pack(side="left", padx=5)

        # Progress
        progress_frame = tk.Frame(self.root)
        progress_frame.pack(fill="x", padx=10, pady=(5, 0))
        self.progress_bar = ttk.Progressbar(progress_frame, mode="determinate", maximum=100)
        self.progress_bar.pack(fill="x")
        self.status_label = tk.Label(progress_frame, text="Ready.", justify="left", anchor="w")
        self.status_label.pack(fill="x", pady=(5, 0))

        # Log
        log_frame = tk.LabelFrame(self.root, text="Console")
        log_frame.pack(fill="both", expand=True, padx=10, pady=(5, 10))
        self.log_text = ScrolledText(log_frame, state="disabled", height=14, wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)
        self.log_text.tag_configure("log_error", foreground="#c62828")
        self.log_text.tag_configure("log_success", foreground="#2e7d32")

    def _build_column(self, parent: tk.Frame, title: str) -> tuple[tk.Listbox, tk.Listbox]:
        frame = tk.LabelFrame(parent, text=title)
        frame.pack(side="left", fill="both", expand=True, padx=5)
        listbox = tk.Listbox(frame, selectmode=tk.MULTIPLE, exportselection=False)
        listbox.pack(fill="both", expand=True, padx=5, pady=5)
        tk.Label(frame, text="Selected:").pack(anchor="w", padx=5)
        selected_box = tk.Listbox(frame, height=5, exportselection=False)
        selected_box.pack(fill="x", padx=5, pady=(0, 5))
        return listbox, selected_box

    # ── Generic toggle for the simple (name-keyed) columns ───────────

    def _toggle(self, listbox, all_items, selected_list, selected_box, key) -> None:
        sel = listbox.curselection()
        if not sel:
            return
        item = all_items[sel[0]]
        if item in selected_list:
            selected_list.remove(item)
        else:
            selected_list.append(item)
        listbox.selection_clear(0, tk.END)
        selected_box.delete(0, tk.END)
        for it in selected_list:
            selected_box.insert(tk.END, key(it))
        self._update_matrix_label()

    def _toggle_mindset(self) -> None:
        sel = self.mindset_listbox.curselection()
        if not sel:
            return
        choice = self._mindsets_all[sel[0]]
        if choice in self._selected_mindsets:
            self._selected_mindsets.remove(choice)
        else:
            self._selected_mindsets.append(choice)
        self.mindset_listbox.selection_clear(0, tk.END)
        self._mindsets_selected_box.delete(0, tk.END)
        for c in self._selected_mindsets:
            self._mindsets_selected_box.insert(tk.END, c.label)
        self._update_matrix_label()

    # ── Populate lists from the server / test/source ─────────────────

    def _reload_everything(self) -> None:
        if not core.check_server():
            self._append_log(f"[ERROR] Server not reachable at {core.SERVER_URL}. "
                              "Start translator.bat/.sh first, then 'Reload from server'.")
            return

        # Source texts
        SOURCE_DIR.mkdir(parents=True, exist_ok=True)
        self._sources_all = sorted(
            p for p in SOURCE_DIR.iterdir()
            if p.is_file() and p.suffix.lower() in (".md", ".txt") and p.name != ".gitkeep"
        )
        self._selected_sources = [p for p in self._selected_sources if p in self._sources_all]
        self.source_listbox.delete(0, tk.END)
        for p in self._sources_all:
            self.source_listbox.insert(tk.END, p.name)
        if not self._sources_all:
            self._append_log(f"No source texts found in {SOURCE_DIR} — drop .md/.txt files there.")

        # Models — translategemma pinned to the top, rest alphabetical
        try:
            self._models_all = _sort_models_pinned(core.fetch_ollama_models())
        except Exception as e:
            self._append_log(f"[ERROR] Could not fetch Ollama models: {e}")
            self._models_all = []
        self._selected_models = [m for m in self._selected_models if m in self._models_all]
        self.model_listbox.delete(0, tk.END)
        for m in self._models_all:
            self.model_listbox.insert(tk.END, m)
        self.s2_combo["values"] = [_NO_S2_LABEL] + self._models_all
        if self.s2_var.get() not in self.s2_combo["values"]:
            self.s2_var.set(_NO_S2_LABEL)
        self.mindset_model_combo["values"] = [""] + self._models_all

        # Mindsets (+ synthetic Auto-detect entry)
        try:
            mindsets = core.fetch_mindsets()
        except Exception as e:
            self._append_log(f"[ERROR] Could not fetch mindsets: {e}")
            mindsets = {}
        self._mindsets_all = [core.MindsetChoice(key=k, label=v, value=k) for k, v in mindsets.items()]
        self._mindsets_all.append(core.MindsetChoice(
            key=core.AUTO_MINDSET, label=core.AUTO_MINDSET_LABEL, value=None))
        known_keys = {c.key for c in self._mindsets_all}
        self._selected_mindsets = [c for c in self._selected_mindsets if c.key in known_keys]
        self.mindset_listbox.delete(0, tk.END)
        for c in self._mindsets_all:
            self.mindset_listbox.insert(tk.END, c.label)

        # Target languages + mindset-detect default — both come straight off
        # /config, the same endpoint the translator's own dropdowns use, so
        # this list is always exactly what the running server currently
        # offers (call "Reload from server" again after editing config.yaml
        # and restarting the server to pick up a change).
        try:
            cfg = core.fetch_config()
        except Exception as e:
            self._append_log(f"[ERROR] Could not fetch /config: {e}")
            cfg = {}
        languages = {LANGUAGE_DISPLAY_EN.get(label, label): code
                     for label, code in cfg.get("languages", {}).items()}
        self._targets_all = list(languages.items())
        self._selected_targets = [t for t in self._selected_targets if t in self._targets_all]
        self.target_listbox.delete(0, tk.END)
        for label, code in self._targets_all:
            self.target_listbox.insert(tk.END, f"{label} ({code})")
        self.source_lang_combo["values"] = [label for label, _code in self._targets_all]
        if self.source_lang_var.get() not in self.source_lang_combo["values"] and self._targets_all:
            self.source_lang_var.set(self._targets_all[0][0])

        # Only pre-fill once — don't clobber a value the user already typed.
        if not self.mindset_model_var.get().strip():
            self.mindset_model_var.set(cfg.get("mindset_model") or _FALLBACK_MINDSET_MODEL)

        self._refresh_selected_boxes()
        self._update_matrix_label()

    def _refresh_selected_boxes(self) -> None:
        self._sources_selected_box.delete(0, tk.END)
        for p in self._selected_sources:
            self._sources_selected_box.insert(tk.END, p.name)
        self._models_selected_box.delete(0, tk.END)
        for m in self._selected_models:
            self._models_selected_box.insert(tk.END, m)
        self._mindsets_selected_box.delete(0, tk.END)
        for c in self._selected_mindsets:
            self._mindsets_selected_box.insert(tk.END, c.label)
        self._targets_selected_box.delete(0, tk.END)
        for label, code in self._selected_targets:
            self._targets_selected_box.insert(tk.END, f"{label} ({code})")

    def _update_matrix_label(self) -> None:
        n = len(self._selected_sources) * len(self._selected_models) * \
            len(self._selected_mindsets) * len(self._selected_targets)
        self.matrix_label.config(text=f"{n} combination(s) "
                                       f"({len(self._selected_sources)} source × "
                                       f"{len(self._selected_models)} model × "
                                       f"{len(self._selected_mindsets)} mindset × "
                                       f"{len(self._selected_targets)} target)")
        self._update_coherence_hint()

    def _update_coherence_hint(self) -> None:
        """Coherence level is always sent along (see the comment at the
        combo's creation) — this just tells the user whether it currently
        does anything, by checking whether the selected source language's
        code is among the selected target codes."""
        src_code = self._current_source_lang_code()
        target_codes = {code for _label, code in self._selected_targets}
        if src_code in target_codes:
            n_sources = max(len(self._selected_sources), 1) if self._selected_sources else 0
            n = n_sources * len(self._selected_models) * len(self._selected_mindsets)
            self.coherence_hint_label.config(
                text=f"active — source language is among the selected targets ({n} combination(s))",
                fg="#2e7d32")
        else:
            self.coherence_hint_label.config(
                text="inactive — source language not among the selected targets", fg="#888888")

    # ── Run folder naming ─────────────────────────────────────────────

    def _suggest_run_fields(self) -> None:
        self.nr_var.set(str(core.next_run_nr(RESULTS_ROOT)))
        self.datum_var.set(datetime.now().strftime("%Y%m%d"))
        self._update_preview()

    def _update_preview(self) -> None:
        name = build_run_folder_name(self.nr_var.get(), self.datum_var.get(), self.freitext_var.get())
        self.preview_label.config(text=f"Folder name: {name}")

    def _check_resumable_run(self) -> None:
        resumable = core.find_resumable_run(RESULTS_ROOT)
        if resumable is None:
            return
        parsed = parse_run_folder_name(resumable.name)
        if parsed is not None:
            nr, datum, rest = parsed
            self.nr_var.set(nr)
            self.datum_var.set(datum)
            self.freitext_var.set(rest)
            self._update_preview()
        self._active_output_dir = resumable
        self._resume_available = True
        self.resume_button.config(state="normal")
        self._append_log(f"Incomplete run found: {resumable.name} — re-select the same "
                          "sources/models/mindsets/targets and click 'Resume run'.")

    # ── Log ────────────────────────────────────────────────────────────

    def _append_log(self, line: str) -> None:
        self.log_text.config(state="normal")
        line_start = self.log_text.index("end-1c")
        self.log_text.insert(tk.END, line + "\n")
        if "[ERROR]" in line:
            self.log_text.tag_add("log_error", f"{line_start}", f"{line_start} lineend")
        elif "->" in line:
            self.log_text.tag_add("log_success", f"{line_start}", f"{line_start} lineend")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    # ── Start / Stop / Resume ────────────────────────────────────────

    def _current_source_lang_code(self) -> str:
        label = self.source_lang_var.get()
        for l, code in self._targets_all:
            if l == label:
                return code
        return "DE"

    def _current_s2_model(self) -> str:
        v = self.s2_var.get()
        return "" if v == _NO_S2_LABEL else v

    def _current_coherence_level(self) -> int:
        label = self.coherence_level_var.get()
        for l, level in COHERENCE_LEVELS:
            if l == label:
                return level
        return 2

    def _on_start_new(self) -> None:
        if self._worker_thread is not None and self._worker_thread.is_alive():
            messagebox.showwarning("Run active", "A run is already active. Stop it first.")
            return
        if not self._selected_sources:
            messagebox.showwarning("No selection", "Please select at least one source text.")
            return
        if not self._selected_models:
            messagebox.showwarning("No selection", "Please select at least one S1 model.")
            return
        if not self._selected_mindsets:
            messagebox.showwarning("No selection", "Please select at least one mindset.")
            return
        if not self._selected_targets:
            messagebox.showwarning("No selection", "Please select at least one target language.")
            return

        folder_name = build_run_folder_name(self.nr_var.get(), self.datum_var.get(), self.freitext_var.get())
        if not folder_name or folder_name == "run":
            messagebox.showwarning("Folder name missing", "Please fill in the Nr./Date/Rest fields.")
            return

        output_dir = RESULTS_ROOT / folder_name
        self._start_run(output_dir, resume=False)

    def _on_resume(self) -> None:
        if not self._resume_available or self._active_output_dir is None:
            return
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        if not (self._selected_sources and self._selected_models
                and self._selected_mindsets and self._selected_targets):
            messagebox.showwarning("Selection missing",
                                    "Re-select the same sources/models/mindsets/targets as the original run.")
            return
        self._start_run(self._active_output_dir, resume=True)

    def _set_locked(self, locked: bool) -> None:
        state = "disabled" if locked else "normal"
        for w in (self.source_listbox, self.model_listbox, self.mindset_listbox, self.target_listbox,
                  self.source_lang_combo, self.s2_combo, self.mindset_model_combo,
                  self.coherence_level_combo, self.reload_button,
                  self.nr_entry, self.datum_entry, self.freitext_entry):
            w.config(state=state)

    def _start_run(self, output_dir: Path, resume: bool) -> None:
        combos = core.build_matrix(self._selected_sources, self._selected_models,
                                    self._selected_targets, self._selected_mindsets)
        self._active_output_dir = output_dir
        self._stop_event = threading.Event()
        self._run_start_time = time.monotonic()
        self._resume_available = False

        self.start_button.config(state="disabled")
        self.resume_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self._set_locked(True)
        self.progress_bar["value"] = 0
        self._append_log(f"=== Run started: {output_dir.name} ({len(combos)} combination(s), resume={resume}) ===")

        source_lang = self._current_source_lang_code()
        s2_model = self._current_s2_model()
        mindset_model = self.mindset_model_var.get().strip()
        coherence_level = self._current_coherence_level()
        stop_event = self._stop_event
        event_queue = self._event_queue

        def log_cb(line: str) -> None:
            event_queue.put({"type": "log", "line": line})

        def progress_cb(info: dict) -> None:
            event_queue.put({"type": "progress", **info})

        def worker() -> None:
            try:
                core.run_batch_session(
                    combos=combos, source_lang=source_lang, s2_model=s2_model,
                    mindset_model=mindset_model, coherence_level=coherence_level,
                    output_dir=output_dir, perf_log=PERF_LOG,
                    log_callback=log_cb, progress_callback=progress_cb,
                    stop_event=stop_event, resume=resume,
                )
                event_queue.put({"type": "done"})
            except Exception as exc:
                event_queue.put({"type": "error", "message": str(exc)})

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()

    def _on_stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
            self.stop_button.config(state="disabled")
            self._append_log("Stop requested — current combination is still being finished ...")

    # ── Queue polling ──────────────────────────────────────────────────

    def _poll_queue(self) -> None:
        try:
            while True:
                event = self._event_queue.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass

        if self._run_start_time is not None and self._worker_thread is not None and self._worker_thread.is_alive():
            elapsed = time.monotonic() - self._run_start_time
            if hasattr(self, "_last_status_prefix"):
                self.status_label.config(text=f"{self._last_status_prefix}  |  Elapsed: {format_elapsed(elapsed)}")

        self.root.after(150, self._poll_queue)

    def _handle_event(self, event: dict) -> None:
        etype = event.get("type")
        if etype == "log":
            self._append_log(event["line"])
        elif etype == "progress":
            pct = (event["combo_idx"] / event["combo_total"]) * 100 if event["combo_total"] else 0
            self.progress_bar["value"] = pct
            self._last_status_prefix = (
                f"[{event['combo_idx']}/{event['combo_total']}] {event['source']} | "
                f"{event['model']} | {event['target']} | {event['mindset']}"
            )
            self.status_label.config(text=self._last_status_prefix)
        elif etype == "done":
            self._on_run_finished()
            self._append_log("=== Run finished ===")
        elif etype == "error":
            self._on_run_finished()
            self._append_log(f"=== Run ended with error: {event['message']} ===")
            messagebox.showerror("Error", event["message"])

    def _on_run_finished(self) -> None:
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")
        self._set_locked(False)
        self._resume_available = self._stop_event is not None and self._stop_event.is_set()
        self.resume_button.config(state="normal" if self._resume_available else "disabled")
        self._worker_thread = None


def main() -> None:
    root = tk.Tk()
    BatchTestGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
