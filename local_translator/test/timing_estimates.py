"""
test/timing_estimates.py — per-model timing estimates for the batch runner.

Learns "seconds per 1000 characters" per model from logs/perf.csv (written
by core/logging.py as each chunk is translated) and persists it as
logs/model_timings.json, right next to perf.csv — runtime-generated data,
not a build asset, so it needs no compiler/build_manifest.py entry: it is
created wherever perf.csv already lives, in a checkout or in a built EXE
(core/config.py's PROJECT_ROOT/LOG_DIR resolution, mirrored by test_gui.py's
own frozen-path-aware logs/ location).

Deliberately does not import core.config — the batch-tester only ever talks
to the running server over HTTP, never to app internals (see runner_core.py's
own module docstring) — so perf.csv's column separator is auto-detected from
its header line instead of reading config.yaml's log_csv_separator.
"""

import json
import statistics
from pathlib import Path

TIMINGS_FILENAME = "model_timings.json"

_EXPECTED_HEADER = [
    "timestamp", "chunk_index", "chunk_size", "complexity",
    "time_s1", "time_s2", "model_s1", "model_s2", "terms_protected",
]


def _detect_separator(header_line: str) -> str:
    for sep in (";", ","):
        if len(header_line.split(sep)) == len(_EXPECTED_HEADER):
            return sep
    return ";"


def _read_perf_rows(perf_log: Path) -> list[dict]:
    if not perf_log.exists():
        return []
    try:
        lines = perf_log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    if not lines:
        return []
    sep = _detect_separator(lines[0])
    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split(sep)
        if len(fields) != len(_EXPECTED_HEADER):
            continue
        rows.append(dict(zip(_EXPECTED_HEADER, fields)))
    return rows


def _timings_path(perf_log: Path) -> Path:
    return perf_log.parent / TIMINGS_FILENAME


def recompute_estimates(perf_log: Path) -> dict:
    """Rebuilds logs/model_timings.json from logs/perf.csv, keyed by model
    name (S1 or S2 — both get logged in the same model_s1/model_s2 columns
    depending on which pass a row came from). Returns the new estimates
    dict. Cheap enough to call once at the end of every batch run."""
    by_model: dict[str, list[float]] = {}
    for row in _read_perf_rows(perf_log):
        try:
            chunk_size = float(row["chunk_size"])
            time_s1 = float(row["time_s1"])
        except (KeyError, ValueError):
            continue
        model = row.get("model_s1", "").strip()
        if model and chunk_size > 0 and time_s1 > 0:
            by_model.setdefault(model, []).append(time_s1 / (chunk_size / 1000))

        model_s2 = row.get("model_s2", "").strip()
        try:
            time_s2 = float(row["time_s2"])
        except (KeyError, ValueError):
            time_s2 = 0.0
        if model_s2 and chunk_size > 0 and time_s2 > 0:
            by_model.setdefault(model_s2, []).append(time_s2 / (chunk_size / 1000))

    estimates = {
        model: {
            "sec_per_1k_chars": round(statistics.median(samples), 2),
            "samples": len(samples),
        }
        for model, samples in by_model.items()
    }

    try:
        _timings_path(perf_log).write_text(
            json.dumps(estimates, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass
    return estimates


def load_estimates(perf_log: Path) -> dict:
    """Reads the persisted estimates, or {} if none exist yet (e.g. before
    the first batch run has ever completed)."""
    try:
        return json.loads(_timings_path(perf_log).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def estimate_seconds(estimates: dict, model: str, chars: int) -> float | None:
    """None if no historical data exists yet for this model — callers must
    show "unknown"/skip it rather than fabricate a number."""
    info = estimates.get(model)
    if not info:
        return None
    return info["sec_per_1k_chars"] * (chars / 1000)
