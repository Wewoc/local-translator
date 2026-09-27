#!/usr/bin/env python3
"""
compiler/build_gui.py — LocalTranslate Builder (Tkinter)

Small window around compiler/build.py's three modes:
  - App bauen (ohne Term Engine)
  - App + Term Engine bauen
  - Nur Term Engine packen

Radio buttons, not a checkbox — "nur Term Engine" skips the app build
entirely, so it isn't "App build + optional extra", it's a third,
mutually exclusive thing to do.

Path fields are never pre-filled or remembered across runs: the
compiled terminology/ folder they'd point at lives outside this repo,
on whichever machine is building, so a stored default would only ever
be valid on one of them and would go silently stale on any other.

Run with:
    python compiler/build_gui.py
"""

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

ROOT = Path(__file__).resolve().parent.parent  # compiler/ -> local_translator/
BUILD_SCRIPT = Path(__file__).resolve().parent / "build.py"


def build_command(build_script: Path, mode: str, term_path: str, out_path: str,
                   make_zip: bool, with_tester: bool) -> list[str]:
    """
    Pure — no Tkinter, no I/O beyond reading the given strings. Builds the
    compiler/build.py argv for a given GUI selection, or raises ValueError
    with a user-facing message if the selection is incomplete.
    """
    cmd = [sys.executable, str(build_script)]

    if mode == "app":
        pass
    elif mode == "app_term":
        if not term_path.strip():
            raise ValueError("Bitte einen Pfad zur Terminologie-Engine wählen.")
        cmd += ["--term-engine-dir", term_path.strip()]
    elif mode == "only_term":
        if not term_path.strip():
            raise ValueError("Bitte einen Pfad zur Terminologie-Engine wählen.")
        if not out_path.strip():
            raise ValueError("Bitte einen Ausgabepfad für die Pack-Datei wählen.")
        cmd += ["--only-term-engine", "--term-engine-dir", term_path.strip(), "--out", out_path.strip()]
    else:
        raise ValueError(f"Unbekannter Modus: {mode}")

    if mode in ("app", "app_term") and not make_zip:
        cmd.append("--no-zip")

    if mode in ("app", "app_term") and not with_tester:
        cmd.append("--no-tester")

    return cmd


def run_gui() -> None:
    root = tk.Tk()
    root.title("LocalTranslate Builder")
    root.geometry("760x600")

    ttk.Label(root, text="LOCALTRANSLATE BUILDER", font=("Segoe UI", 13, "bold")).pack(
        anchor="w", padx=10, pady=(10, 0))
    ttk.Label(root, text=f"Projektordner: {ROOT}").pack(anchor="w", padx=10, pady=(4, 8))

    opts = ttk.Frame(root, padding=(10, 0))
    opts.pack(fill="x")

    mode_var = tk.StringVar(value="app")
    term_path_var = tk.StringVar(value="")
    out_path_var = tk.StringVar(value="")
    zip_var = tk.BooleanVar(value=True)

    ttk.Radiobutton(opts, text="App bauen (ohne Term Engine)",
                     variable=mode_var, value="app").grid(row=0, column=0, sticky="w", columnspan=3)
    ttk.Radiobutton(opts, text="App + Term Engine bauen",
                     variable=mode_var, value="app_term").grid(row=1, column=0, sticky="w", columnspan=3, pady=(4, 0))
    ttk.Radiobutton(opts, text="Nur Term Engine packen (kein App-Build)",
                     variable=mode_var, value="only_term").grid(row=2, column=0, sticky="w", columnspan=3, pady=(4, 0))

    ttk.Label(opts, text="Pfad zu den kompilierten Termlisten (terminology/-Ordner):").grid(
        row=3, column=0, sticky="w", pady=(10, 0), columnspan=3)
    term_entry = ttk.Entry(opts, textvariable=term_path_var, width=60, state="disabled")
    term_entry.grid(row=4, column=0, sticky="we", padx=(0, 6), pady=(2, 0))

    def _browse_term_path():
        chosen = filedialog.askdirectory()
        if chosen:
            term_path_var.set(chosen)

    term_browse = ttk.Button(opts, text="…", width=3, command=_browse_term_path, state="disabled")
    term_browse.grid(row=4, column=1, pady=(2, 0))

    ttk.Label(opts, text="Ausgabe-Datei (nur bei 'Nur Term Engine packen'):").grid(
        row=5, column=0, sticky="w", pady=(10, 0), columnspan=3)
    out_entry = ttk.Entry(opts, textvariable=out_path_var, width=60, state="disabled")
    out_entry.grid(row=6, column=0, sticky="we", padx=(0, 6), pady=(2, 0))

    def _browse_out_path():
        chosen = filedialog.asksaveasfilename(
            defaultextension=".data",
            initialfile="terminology.data",
            filetypes=[("Terminology data", "*.data")])
        if chosen:
            out_path_var.set(chosen)

    out_browse = ttk.Button(opts, text="…", width=3, command=_browse_out_path, state="disabled")
    out_browse.grid(row=6, column=1, pady=(2, 0))
    opts.columnconfigure(0, weight=1)

    zip_check = ttk.Checkbutton(opts, text="Release-ZIP erstellen", variable=zip_var)
    zip_check.grid(row=7, column=0, sticky="w", pady=(10, 0), columnspan=3)

    tester_var = tk.BooleanVar(value=True)
    tester_check = ttk.Checkbutton(
        opts, text="Batch-Tester mitbauen (test/LocalTranslate-Tester.exe)", variable=tester_var)
    tester_check.grid(row=8, column=0, sticky="w", pady=(4, 0), columnspan=3)

    def _update_field_states(*_):
        mode = mode_var.get()
        term_state = "normal" if mode in ("app_term", "only_term") else "disabled"
        term_entry.configure(state=term_state)
        term_browse.configure(state=term_state)
        out_state = "normal" if mode == "only_term" else "disabled"
        out_entry.configure(state=out_state)
        out_browse.configure(state=out_state)
        zip_check.configure(state="normal" if mode in ("app", "app_term") else "disabled")
        tester_check.configure(state="normal" if mode in ("app", "app_term") else "disabled")

    mode_var.trace_add("write", _update_field_states)
    _update_field_states()

    btn_frame = ttk.Frame(root, padding=10)
    btn_frame.pack(fill="x")
    start_btn = ttk.Button(btn_frame, text="Build starten")
    start_btn.pack(side="left")
    cancel_btn = ttk.Button(btn_frame, text="Abbrechen", state="disabled")
    cancel_btn.pack(side="left", padx=(6, 0))

    status_var = tk.StringVar(value="")
    ttk.Label(root, textvariable=status_var).pack(anchor="w", padx=10)

    log_widget = scrolledtext.ScrolledText(
        root, height=20, state="disabled", font=("Consolas", 9),
        background="#0a0a1a", foreground="#33ff66")
    log_widget.pack(fill="both", expand=True, padx=10, pady=(6, 10))

    def _log(line: str):
        log_widget.configure(state="normal")
        log_widget.insert(tk.END, line + "\n")
        log_widget.see(tk.END)
        log_widget.configure(state="disabled")

    proc_state = {"proc": None}

    def _reader_thread(proc, q):
        for line in proc.stdout:
            q.put(line.rstrip("\n"))
        returncode = proc.wait()
        q.put(("__DONE__", returncode))

    def _poll_output(q, on_done):
        try:
            while True:
                item = q.get_nowait()
                if isinstance(item, tuple) and item[0] == "__DONE__":
                    on_done(item[1])
                    return
                _log(item)
        except queue.Empty:
            pass
        root.after(100, lambda: _poll_output(q, on_done))

    def _finish(returncode: int):
        proc_state["proc"] = None
        start_btn.configure(state="normal")
        cancel_btn.configure(state="disabled")
        if returncode == 0:
            status_var.set("Build fertig.")
            _log("=== Build erfolgreich beendet ===")
        else:
            status_var.set(f"Fehlgeschlagen (Exit {returncode}).")
            _log(f"=== Beendet — Exit-Code {returncode} ===")

    def _on_start():
        try:
            cmd = build_command(BUILD_SCRIPT, mode_var.get(), term_path_var.get(), out_path_var.get(),
                                 zip_var.get(), tester_var.get())
        except ValueError as exc:
            messagebox.showwarning("LocalTranslate Builder", str(exc))
            return

        log_widget.configure(state="normal")
        log_widget.delete("1.0", tk.END)
        log_widget.configure(state="disabled")
        status_var.set("Build läuft ...")
        start_btn.configure(state="disabled")

        try:
            proc = subprocess.Popen(
                cmd, cwd=str(ROOT),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
        except OSError as exc:
            _log(f"Konnte nicht starten: {exc}")
            start_btn.configure(state="normal")
            return

        proc_state["proc"] = proc
        cancel_btn.configure(state="normal")
        q: queue.Queue = queue.Queue()
        threading.Thread(target=_reader_thread, args=(proc, q), daemon=True).start()
        _poll_output(q, _finish)

    def _on_cancel():
        proc = proc_state["proc"]
        if proc is None:
            return
        if not messagebox.askyesno("LocalTranslate Builder", "Build wirklich abbrechen?"):
            return
        _log("Abbruch angefordert ...")
        proc.terminate()

    start_btn.configure(command=_on_start)
    cancel_btn.configure(command=_on_cancel)

    root.mainloop()


if __name__ == "__main__":
    run_gui()
