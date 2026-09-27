"""
test/runner_core.py — core logic for the batch quality-test runner.

No Tkinter here — this module only talks to a running LocalTranslate
server (see ../app.py) and writes result files. test_gui.py builds the
matrix (source texts × models × mindsets × target languages) and drives
run_batch_session() from a worker thread; a future CLI entry point could
call the same function directly.

Progress/resume model mirrors mcp-llm-tester's GUI runner: each finished
combination is appended to progress.jsonl in the run folder immediately,
a done.marker is written only on a full, un-stopped completion, and
resume simply skips combinations already present in progress.jsonl.
"""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SERVER_URL = "http://127.0.0.1:8000"

PROGRESS_FILENAME = "batch_progress.jsonl"
DONE_MARKER = "batch_done.marker"

# Synthetic mindset entry: resolved per source text via /mindset/detect
# instead of a fixed value from /mindsets.
AUTO_MINDSET = "__auto__"
AUTO_MINDSET_LABEL = "Auto-detect"


# ── Server queries ───────────────────────────────────────────────────────────

def check_server() -> bool:
    try:
        urllib.request.urlopen(f"{SERVER_URL}/config", timeout=3)
        return True
    except Exception:
        return False


def fetch_config() -> dict:
    with urllib.request.urlopen(f"{SERVER_URL}/config", timeout=5) as resp:
        return json.loads(resp.read())


def fetch_ollama_models() -> list[str]:
    with urllib.request.urlopen(f"{SERVER_URL}/ollama/status", timeout=5) as resp:
        data = json.loads(resp.read())
    return list(data.get("models", []))


def fetch_mindsets() -> dict[str, str]:
    """Returns {key: label}, e.g. {"general": "General", "technical": "Technical"}."""
    with urllib.request.urlopen(f"{SERVER_URL}/mindsets", timeout=5) as resp:
        data = json.loads(resp.read())
    return {k: v.get("label", k) for k, v in data.items()}


# ── Translation calls — same endpoints the browser UI uses ──────────────────

def prepare_chunks(text: str) -> list[str]:
    payload = json.dumps({"text": text, "engine": "ollama"}).encode()
    req = urllib.request.Request(
        f"{SERVER_URL}/translate/chunks/prepare",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data.get("chunks", [text])


def set_model(model: str) -> None:
    payload = json.dumps({"model": model}).encode()
    req = urllib.request.Request(
        f"{SERVER_URL}/ollama/set_model",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10)


def detect_mindset(text: str, mindset_model: str = "") -> str:
    payload = json.dumps({"text": text, "mindset_model": mindset_model}).encode()
    req = urllib.request.Request(
        f"{SERVER_URL}/mindset/detect",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return data.get("mindset", "general")


def translate_chunk(
    text: str,
    src_lang: str,
    tgt_lang: str,
    mindset: str,
    s2_model: str,
    chunk_index: int = 0,
    context: str = "",
    coherence_level: int = 2,
) -> dict:
    """coherence_level is always sent, whether or not this call is actually
    a Coherence Mode call — app.py's ChunkRequest only *reads* it inside its
    own `if coherence_mode:` branch (src_lang == tgt_lang), so it's simply
    ignored on an ordinary translation call. Nothing to gate client-side."""
    payload = json.dumps({
        "text":             text,
        "source_lang":      src_lang.upper(),
        "target_lang":      tgt_lang.upper(),
        "engine":           "ollama",
        "context":          context,
        "mindset":          mindset,
        "s2_model":         s2_model if s2_model and s2_model != "—" else "",
        "chunk_index":      chunk_index,
        "coherence_level":  coherence_level,
    }).encode()
    req = urllib.request.Request(
        f"{SERVER_URL}/translate/chunk",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read())


def read_perf_rows(since: datetime, until: datetime, perf_log: Path) -> list[str]:
    """Reads logs/perf.csv and returns the header plus rows whose timestamp
    falls within [since, until]."""
    if not perf_log.exists():
        return []

    rows: list[str] = []
    try:
        with open(perf_log, encoding="utf-8") as f:
            lines = f.readlines()
        if not lines:
            return []
        rows.append(lines[0].rstrip())
        for line in lines[1:]:
            line = line.rstrip()
            if not line:
                continue
            ts_str = line.split(line[19])[0] if len(line) > 19 else ""
            try:
                ts = datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S")
                if since <= ts <= until:
                    rows.append(line)
            except ValueError:
                continue
    except Exception as e:
        rows.append(f"[perf.csv read error: {e}]")
    return rows


def safe_filename(s: str) -> str:
    return "".join(c for c in s if c.isalnum() or c in "-_.").rstrip(".") or "x"


# ── Batch matrix ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MindsetChoice:
    """One selectable mindset-axis entry. value is None for auto-detect —
    resolved per source text at run start, see _resolve_auto_mindsets()."""
    key: str
    label: str
    value: str | None  # None => auto-detect


@dataclass(frozen=True)
class Combo:
    source: Path
    model_s1: str
    target_code: str
    target_label: str
    mindset_key: str
    mindset_label: str

    def combo_id(self) -> str:
        return "|".join([self.source.name, self.model_s1, self.target_code, self.mindset_key])


def build_matrix(
    sources: list[Path],
    models_s1: list[str],
    targets: list[tuple[str, str]],  # (label, code)
    mindsets: list[MindsetChoice],
) -> list[Combo]:
    combos = []
    for source in sources:
        for model in models_s1:
            for target_label, target_code in targets:
                for mindset in mindsets:
                    combos.append(Combo(
                        source=source, model_s1=model,
                        target_code=target_code, target_label=target_label,
                        mindset_key=mindset.key, mindset_label=mindset.label,
                    ))
    return combos


def _load_done_ids(progress_path: Path) -> set[str]:
    done: set[str] = set()
    if not progress_path.exists():
        return done
    with progress_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("status") == "done" and record.get("combo_id"):
                done.add(record["combo_id"])
    return done


def _build_result_md(
    combo: Combo,
    source_text: str,
    resolved_mindset: str,
    s1_translation: str,
    s2_model: str,
    s2_translation: str,
    perf_rows: list[str],
    time_s1: float,
    time_s2: float,
    run_ts: str,
    source_lang: str,
    is_coherence: bool = False,
    coherence_level: int = 2,
    similarities: list[float] | None = None,
) -> str:
    has_s2 = bool(s2_model) and s2_model != "—" and s2_translation

    lines = [
        f"# Test — {run_ts}" + (" [Coherence Mode]" if is_coherence else ""),
        "",
        "## Run",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Source | `{combo.source.name}` |",
        f"| S1 model | `{combo.model_s1}` |",
        f"| S2 model | `{'skipped — Coherence Mode' if is_coherence else (s2_model or '—')}` |",
        f"| Source lang | `{source_lang.upper()}` |",
        f"| Target lang | `{combo.target_code.upper()}` |",
        f"| Mindset | `{combo.mindset_label}`" + (f" (resolved: `{resolved_mindset}`)" if combo.mindset_key == AUTO_MINDSET else "") + " |",
        f"| S1 time | {int(round(time_s1))}s |",
        f"| S2 time | {int(round(time_s2))}s |",
    ]
    if is_coherence:
        lines.append(f"| Coherence level | {coherence_level} |")
        if similarities:
            avg = sum(similarities) / len(similarities)
            lines.append(f"| Similarity (avg over {len(similarities)} chunk(s)) | {avg:.3f} |")
    lines.append("")

    if len(perf_rows) > 1:
        lines += ["## Performance Log", "", "```"] + perf_rows + ["```", ""]
    else:
        lines += ["## Performance Log", "", "_No entries logged for this run._", ""]

    lines += ["## Source", "", source_text, ""]
    lines += [f"## S1 — {combo.model_s1}", "", s1_translation, ""]
    if has_s2:
        lines += [f"## S2 — {s2_model}", "", s2_translation, ""]

    return "\n".join(lines)


def run_batch_session(
    combos: list[Combo],
    source_lang: str,
    s2_model: str,
    mindset_model: str,
    coherence_level: int,
    output_dir: Path,
    perf_log: Path,
    log_callback=lambda line: None,
    progress_callback=lambda info: None,
    stop_event=None,
    resume: bool = False,
) -> None:
    """Runs every combo in `combos` against the LocalTranslate server and
    writes one result .md per combo into output_dir/results/. Progress is
    appended to output_dir/batch_progress.jsonl immediately after each combo
    so a stopped run can be resumed (already-done combo_ids are skipped)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    results_dir = output_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir / PROGRESS_FILENAME
    done_marker = output_dir / DONE_MARKER

    done_ids = _load_done_ids(progress_path) if resume else set()
    if resume and done_marker.exists():
        done_marker.unlink()

    # Auto-detect mindset is resolved once per (source text, mindset_model)
    # pair up front — not once per target language/S1 model — since the
    # source text itself decides the mindset, not the translation direction.
    text_cache: dict[Path, str] = {}
    auto_cache: dict[Path, str] = {}

    total = len(combos)
    log_callback(f"{total} combination(s) queued ({len(done_ids)} already done).")

    for idx, combo in enumerate(combos, 1):
        progress_callback({"combo_idx": idx, "combo_total": total, "source": combo.source.name,
                            "model": combo.model_s1, "target": combo.target_code,
                            "mindset": combo.mindset_label})

        if stop_event is not None and stop_event.is_set():
            log_callback("Stop requested — run paused, can be resumed.")
            return

        combo_id = combo.combo_id()
        if combo_id in done_ids:
            log_callback(f"[{idx}/{total}] SKIP (already done): {combo_id}")
            continue

        if combo.source not in text_cache:
            text_cache[combo.source] = combo.source.read_text(encoding="utf-8")
        source_text = text_cache[combo.source]

        resolved_mindset = combo.mindset_key
        if combo.mindset_key == AUTO_MINDSET:
            if combo.source not in auto_cache:
                try:
                    auto_cache[combo.source] = detect_mindset(source_text, mindset_model)
                except Exception as e:
                    log_callback(f"[{idx}/{total}] [ERROR] mindset auto-detect failed for {combo.source.name}: {e}")
                    _append_progress(progress_path, combo_id, "error", str(e))
                    continue
            resolved_mindset = auto_cache[combo.source]

        is_coherence = source_lang.upper() == combo.target_code.upper()
        log_callback(
            f"[{idx}/{total}] {combo.source.name} | S1={combo.model_s1} | "
            f"{source_lang.upper()}→{combo.target_code.upper()}"
            + (" [Coherence]" if is_coherence else "")
            + f" | mindset={combo.mindset_label}"
            + (f" ({resolved_mindset})" if combo.mindset_key == AUTO_MINDSET else "")
        )

        try:
            set_model(combo.model_s1)
            chunks = prepare_chunks(source_text)
            run_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            date_str = datetime.now().strftime("%Y-%m-%d")
            t_start = datetime.now()

            t0 = time.monotonic()
            s1_parts = []
            similarities = []
            context = ""
            for i, chunk in enumerate(chunks):
                data = translate_chunk(chunk, source_lang, combo.target_code, resolved_mindset,
                                        s2_model, i, context, coherence_level)
                part = data.get("translation", "")
                s1_parts.append(part)
                context = part[-300:] if part else ""
                if "similarity" in data:
                    similarities.append(data["similarity"])
            time_s1 = time.monotonic() - t0
            s1_translation = "\n\n".join(s1_parts)

            # The server's coherence_mode branch (src == tgt) always skips
            # S2 regardless of s2_model — see app.py's translate_chunk
            # endpoint — so this call only actually runs an S2 pass outside
            # Coherence Mode, same as the CSV-driven test.py.
            s2_translation = ""
            time_s2 = 0.0
            if s2_model and s2_model != "—" and not is_coherence:
                t1 = time.monotonic()
                data_s2 = translate_chunk(s1_translation, combo.target_code, combo.target_code,
                                           resolved_mindset, "", coherence_level=coherence_level)
                time_s2 = time.monotonic() - t1
                s2_translation = data_s2.get("translation", "")

            t_end = datetime.now()
            perf_rows = read_perf_rows(t_start, t_end, perf_log)

            result_md = _build_result_md(
                combo, source_text, resolved_mindset, s1_translation, s2_model,
                s2_translation, perf_rows, time_s1, time_s2, run_ts, source_lang,
                is_coherence=is_coherence, coherence_level=coherence_level, similarities=similarities,
            )

            mindset_tag = resolved_mindset if combo.mindset_key != AUTO_MINDSET else f"auto-{resolved_mindset}"
            coherence_tag = "_coherence" if is_coherence else ""
            out_name = (
                f"{date_str}_{safe_filename(combo.model_s1.replace(':', '-'))}_"
                f"{safe_filename(combo.source.stem)}_{combo.target_code.lower()}{coherence_tag}_"
                f"{safe_filename(mindset_tag)}.md"
            )
            (results_dir / out_name).write_text(result_md, encoding="utf-8")
            log_callback(f"    -> {out_name} ({int(round(time_s1))}s)")
            _append_progress(progress_path, combo_id, "done", out_name)

        except Exception as e:
            log_callback(f"[{idx}/{total}] [ERROR] {combo_id}: {e}")
            _append_progress(progress_path, combo_id, "error", str(e))
            continue

    if stop_event is None or not stop_event.is_set():
        done_marker.write_text(datetime.now().isoformat(), encoding="utf-8")
        log_callback(f"Done. Results in: {results_dir}")


def _append_progress(progress_path: Path, combo_id: str, status: str, note: str) -> None:
    record = {"combo_id": combo_id, "status": status, "note": note,
              "timestamp": datetime.now().isoformat()}
    with progress_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def find_resumable_run(results_root: Path) -> Path | None:
    """Looks only at the most recently created run_*-folder under
    results_root — same "resumable" definition as mcp-llm-tester's GUI:
    a progress file exists but no done marker."""
    if not results_root.exists():
        return None
    latest_nr = None
    latest_path = None
    for entry in results_root.iterdir():
        if not entry.is_dir():
            continue
        import re
        match = re.match(r"^run_(\d+)_", entry.name)
        if match:
            nr = int(match.group(1))
            if latest_nr is None or nr > latest_nr:
                latest_nr = nr
                latest_path = entry
    if latest_path is None:
        return None
    if not (latest_path / PROGRESS_FILENAME).exists():
        return None
    if (latest_path / DONE_MARKER).exists():
        return None
    return latest_path


def next_run_nr(results_root: Path) -> int:
    if not results_root.exists():
        return 1
    import re
    highest = 0
    for entry in results_root.iterdir():
        if not entry.is_dir():
            continue
        match = re.match(r"^run_(\d+)_", entry.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1
