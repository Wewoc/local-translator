"""
test/run_state.py — on-disk state for the batch runner: run-level settings,
per-combo chunk progress, and the list of resumable runs.

Three kinds of file live under a run's output_dir (results/run_<nr>_<date>_<rest>/):

  run_settings.json        Written once when a run is started fresh. Holds
                            the selected sources/models/targets/mindsets and
                            options (s2 model, source lang, coherence level)
                            so a later Resume can show/restore exactly what
                            this run was configured with.

  batch_progress.jsonl      Unchanged — runner_core.py's own append-only log
                            of finished/errored combo_ids (see _append_progress).

  chunks/<combo_id>.json    One file per combination that is *currently*
                            in progress (deleted once that combo fully
                            finishes). Holds enough to resume mid-combo
                            without re-translating already-done chunks:
                            resolved mindset, the result filename, a table
                            of {index, chars, seconds, similarity} per
                            finished chunk, S1/S2 completion flags, and the
                            exact byte offset in the result .md right after
                            the last confirmed chunk — so a resume can
                            truncate away any dangling, unconfirmed data
                            from an interrupted write before continuing.

Writes here are all-or-nothing (write to a temp file, then os.replace) —
these are read back to decide whether translated work can be reused, so a
half-written file must never be mistaken for a valid one.
"""

import hashlib
import json
import os
import re
from pathlib import Path

RUN_SETTINGS_FILENAME = "run_settings.json"
CHUNKS_DIRNAME = "chunks"

_SAFE_RUN_RE = re.compile(r"^run_(\d+)_")


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ── Run-level settings ───────────────────────────────────────────────────────

def write_run_settings(output_dir: Path, settings: dict) -> None:
    _atomic_write_json(output_dir / RUN_SETTINGS_FILENAME, settings)


def read_run_settings(output_dir: Path) -> dict | None:
    return _read_json(output_dir / RUN_SETTINGS_FILENAME)


# ── Per-combo chunk progress ─────────────────────────────────────────────────

def _safe_combo_filename(combo_id: str) -> str:
    """A readable prefix plus a hash of the full combo_id — the hash alone
    guarantees uniqueness (two different combo_ids never collide, even if
    an unusually long one would otherwise be truncated to the same
    prefix as another); the prefix is just so the filename stays
    recognizable when browsing the chunks/ folder by hand."""
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in combo_id)
    digest = hashlib.sha1(combo_id.encode("utf-8")).hexdigest()[:10]
    return f"{safe[:150]}_{digest}.json"


def combo_progress_path(output_dir: Path, combo_id: str) -> Path:
    return output_dir / CHUNKS_DIRNAME / _safe_combo_filename(combo_id)


def load_combo_progress(output_dir: Path, combo_id: str) -> dict | None:
    return _read_json(combo_progress_path(output_dir, combo_id))


def save_combo_progress(output_dir: Path, combo_id: str, data: dict) -> None:
    _atomic_write_json(combo_progress_path(output_dir, combo_id), data)


def delete_combo_progress(output_dir: Path, combo_id: str) -> None:
    try:
        combo_progress_path(output_dir, combo_id).unlink()
    except FileNotFoundError:
        pass


# ── Listing resumable runs ───────────────────────────────────────────────────

def list_resumable_runs(results_root: Path) -> list[dict]:
    """Every run_*-folder under results_root that has a batch_progress.jsonl
    but no batch_done.marker — i.e. every run that stopped (or crashed)
    before finishing. Newest first (by run number). Each entry:
    {"path": Path, "name": str, "done": int, "total": int | None,
     "mtime": float, "settings": dict | None}. `total` is None when the
     original combo count can't be recovered (no run_settings.json, e.g.
     a run from before this feature existed) — callers show "?" for it."""
    if not results_root.exists():
        return []

    # Local imports to avoid a hard dependency between the two modules at
    # import time — runner_core.py already defines these two constants.
    import runner_core as core

    runs = []
    for entry in results_root.iterdir():
        if not entry.is_dir() or not _SAFE_RUN_RE.match(entry.name):
            continue
        progress_path = entry / core.PROGRESS_FILENAME
        if not progress_path.exists():
            continue
        if (entry / core.DONE_MARKER).exists():
            continue

        done_ids = core._load_done_ids(progress_path)
        settings = read_run_settings(entry)
        total = None
        if settings:
            total = (len(settings.get("sources", [])) * len(settings.get("models", []))
                     * len(settings.get("targets", [])) * len(settings.get("mindsets", [])))
        try:
            mtime = progress_path.stat().st_mtime
        except OSError:
            mtime = 0.0

        match = _SAFE_RUN_RE.match(entry.name)
        runs.append({
            "path": entry,
            "name": entry.name,
            "nr": int(match.group(1)),
            "done": len(done_ids),
            "total": total,
            "mtime": mtime,
            "settings": settings,
        })

    runs.sort(key=lambda r: r["nr"], reverse=True)
    return runs
