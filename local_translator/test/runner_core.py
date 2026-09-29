"""
test/runner_core.py — core logic for the batch quality-test runner.

No Tkinter here — this module only talks to a running LocalTranslate
server (see ../app.py) and writes result files. test_gui.py builds the
matrix (source texts × models × mindsets × target languages) and drives
run_batch_session() from a worker thread; a future CLI entry point could
call the same function directly.

Progress/resume model: each finished combination is appended to
batch_progress.jsonl in the run folder immediately, a done.marker is
written only on a full, un-stopped completion, and resume skips
combinations already present in batch_progress.jsonl — same as before.

New: resume is now chunk-accurate, not just combo-accurate (see
run_state.py). Each result .md is written incrementally — a fixed header
+ fully chunked source text once at combo start, then one marker+block
appended per finished chunk, never rewritten — and a small sidecar JSON
under output_dir/chunks/ tracks exactly which chunks are confirmed done
(chars/time/similarity, no text) plus the exact byte offset in the .md
right after the last confirmed chunk. On resume, the .md is truncated
back to that offset (discarding any dangling, unconfirmed write from an
interrupted process) and the confirmed chunks' translated text is read
back from it via their marker comments — so a 100-page document doesn't
need to be re-translated from scratch after a crash or a deliberate Stop.
"""

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import run_state
import timing_estimates

SERVER_URL = "http://127.0.0.1:8000"

PROGRESS_FILENAME = "batch_progress.jsonl"
DONE_MARKER = "batch_done.marker"

# Synthetic mindset entry: resolved per source text via /mindset/detect
# instead of a fixed value from /mindsets.
AUTO_MINDSET = "__auto__"
AUTO_MINDSET_LABEL = "Auto-detect"


# ── Server queries ───────────────────────────────────────────────────────────

def _urlopen(req, timeout: float):
    """Thin wrapper around urllib.request.urlopen() that doesn't let a
    non-2xx response's actual error detail get lost. app.py's endpoints
    raise HTTPException(status_code=..., detail="...") on failure — that
    detail is the one substantive clue for *why* a chunk failed (e.g.
    "Ollama error: <reason>"), but it lives in the error response's body,
    which urlopen()'s raised HTTPError does not surface on its own (str(e)
    is just "HTTP Error 500: Internal Server Error"). Reading e.read() here
    and folding it into the raised message means a batch run's own log
    shows the real reason directly — no need to go find the server console,
    which for a handled HTTPException like this doesn't print a traceback
    anyway (see MAINTENANCE_translator.md)."""
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raw = b""
        try:
            raw = e.read()
        except Exception:
            pass
        detail = raw.decode("utf-8", errors="replace").strip()
        try:
            detail = json.loads(detail).get("detail", detail)
        except Exception:
            pass
        url = req.full_url if isinstance(req, urllib.request.Request) else req
        message = f"HTTP {e.code} from {url}"
        if detail:
            message += f": {detail}"
        raise RuntimeError(message) from e


def check_server() -> bool:
    try:
        urllib.request.urlopen(f"{SERVER_URL}/config", timeout=3)
        return True
    except Exception:
        return False


def fetch_config() -> dict:
    with _urlopen(f"{SERVER_URL}/config", 5) as resp:
        return json.loads(resp.read())


def fetch_ollama_models() -> list[str]:
    with _urlopen(f"{SERVER_URL}/ollama/status", 5) as resp:
        data = json.loads(resp.read())
    return list(data.get("models", []))


def fetch_mindsets() -> dict[str, str]:
    """Returns {key: label}, e.g. {"general": "General", "technical": "Technical"}."""
    with _urlopen(f"{SERVER_URL}/mindsets", 5) as resp:
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
    with _urlopen(req, 30) as resp:
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
    _urlopen(req, 10)


def detect_mindset(text: str, mindset_model: str = "") -> str:
    payload = json.dumps({"text": text, "mindset_model": mindset_model}).encode()
    req = urllib.request.Request(
        f"{SERVER_URL}/mindset/detect",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _urlopen(req, 60) as resp:
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
    with _urlopen(req, 300) as resp:
        return json.loads(resp.read())


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


def _append_progress(progress_path: Path, combo_id: str, status: str, note: str) -> None:
    record = {"combo_id": combo_id, "status": status, "note": note,
              "timestamp": datetime.now().isoformat()}
    with progress_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def next_run_nr(results_root: Path) -> int:
    if not results_root.exists():
        return 1
    highest = 0
    for entry in results_root.iterdir():
        if not entry.is_dir():
            continue
        match = re.match(r"^run_(\d+)_", entry.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


# ── Incremental result .md: header/source once, one block per chunk ─────────

_CHUNK_MARKER_RE = re.compile(
    r"<!-- lt-chunk (?P<idx>\d+)/(?P<total>\d+) \| (?P<chars>\d+) chars \| "
    r"(?P<secs>[\d.]+)s(?: \| sim (?P<sim>[\d.]+))? -->\n"
    r"(?P<text>.*?)(?=\n<!-- lt-chunk |\n## |\Z)",
    re.DOTALL,
)


def _format_source_block(index: int, total: int, text: str) -> str:
    return f"<!-- lt-source-chunk {index}/{total} | {len(text)} chars -->\n{text}\n\n"


def _format_chunk_block(index: int, total: int, text: str, chars: int,
                         seconds: float, similarity: float | None = None) -> str:
    sim_part = f" | sim {similarity:.3f}" if similarity is not None else ""
    marker = f"<!-- lt-chunk {index}/{total} | {chars} chars | {seconds:.1f}s{sim_part} -->"
    return f"{marker}\n{text}\n\n"


def _parse_chunk_blocks(md_text: str) -> list[tuple[int, str]]:
    """Recovers (index, translated_text) for every lt-chunk marker block —
    used on resume to rebuild already-translated chunks without re-running
    them through the model."""
    blocks = []
    for m in _CHUNK_MARKER_RE.finditer(md_text):
        text = m.group("text")
        if text.endswith("\n\n"):
            text = text[:-2]
        elif text.endswith("\n"):
            text = text[:-1]
        blocks.append((int(m.group("idx")), text))
    blocks.sort(key=lambda b: b[0])
    return blocks


def _write_combo_header(combo: Combo, chunks: list[str], source_lang: str, s2_model: str,
                         is_coherence: bool, coherence_level: int, resolved_mindset: str) -> str:
    total = len(chunks)
    parts = [
        f"# Test — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}" + (" [Coherence Mode]" if is_coherence else ""),
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
        f"| Mindset | `{combo.mindset_label}`"
        + (f" (resolved: `{resolved_mindset}`)" if combo.mindset_key == AUTO_MINDSET else "") + " |",
    ]
    if is_coherence:
        parts.append(f"| Coherence level | {coherence_level} |")
    parts += ["| Status | `IN PROGRESS` |", "", "## Source", ""]

    header = "\n".join(parts) + "\n\n"
    for i, chunk in enumerate(chunks, 1):
        header += _format_source_block(i, total, chunk)
    header += f"## S1 — {combo.model_s1}\n\n"
    return header


def _write_combo_footer(md_path: Path, chunks_table: list[dict], warnings: list[str],
                         time_s1: float, time_s2: float, s2_ran: bool) -> None:
    has_sim = any(c.get("similarity") is not None for c in chunks_table)
    lines = ["## Performance Log", "",
             "| Chunk | Chars | Time (s) |" + (" Similarity |" if has_sim else ""),
             "|---|---|---|" + ("---|" if has_sim else "")]
    for c in chunks_table:
        row = f"| {c['index']} | {c['chars']} | {c['seconds']:.1f} |"
        if has_sim:
            sim = c.get("similarity")
            row += f" {sim:.3f} |" if sim is not None else " — |"
        lines.append(row)
    lines.append("")
    if has_sim:
        sims = [c["similarity"] for c in chunks_table if c.get("similarity") is not None]
        if sims:
            lines += [f"_Average similarity: {sum(sims) / len(sims):.3f}_", ""]

    if warnings:
        lines += ["## Warnings", ""] + [f"- {w}" for w in warnings] + [""]

    lines += ["## Done", "", "| Field | Value |", "|---|---|",
              "| Status | `COMPLETE` |", f"| S1 time | {time_s1:.0f}s |"]
    if s2_ran:
        lines.append(f"| S2 time | {time_s2:.0f}s |")
    lines.append("")

    with md_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ── Batch run ─────────────────────────────────────────────────────────────────

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
    expected_combo_total: int | None = None,
) -> None:
    """Runs every combo in `combos` against the LocalTranslate server and
    writes one result .md per combo into output_dir/results/. Progress is
    appended to output_dir/batch_progress.jsonl immediately after each combo
    so a stopped run can be resumed (already-done combo_ids are skipped) —
    and within a combo, chunk-by-chunk via output_dir/chunks/<combo_id>.json
    (see run_state.py and the module docstring above).

    expected_combo_total: the combo count the run was *originally*
    configured for (from run_settings.json), passed by the GUI on resume.
    If `combos` is smaller than this — e.g. an Ollama model used by the
    original run was since deleted, and the GUI could only restore part
    of the selection — finishing every combo actually given here must
    NOT write done.marker: the run would otherwise be reported as
    complete while part of its original scope silently never ran.
    None (a fresh, non-resumed run) skips this check entirely."""
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

    # Chunking only depends on the source text, not on model/target/mindset —
    # split each unique source once up front instead of once per combo. This
    # also gives exact chunk counts/sizes before any translation call, which
    # is what the ETA estimate below needs.
    chunks_by_source: dict[Path, list[str]] = {}
    for combo in combos:
        if combo.source not in text_cache:
            text_cache[combo.source] = combo.source.read_text(encoding="utf-8")
        if combo.source not in chunks_by_source:
            chunks_by_source[combo.source] = prepare_chunks(text_cache[combo.source])

    # Remaining-time estimate, built from logs/model_timings.json (see
    # timing_estimates.py). Counts down as chunks/combos finish below;
    # None as long as no historical data covers any pending combo's model.
    # Already-completed chunks of a partially-resumed combo (from a prior
    # stop/crash) are excluded up front, same as a fully-done combo.
    estimates = timing_estimates.load_estimates(perf_log)
    combo_s2_estimate: dict[str, float | None] = {}
    remaining_estimate = 0.0
    have_estimate = False
    for combo in combos:
        combo_id = combo.combo_id()
        if combo_id in done_ids:
            continue
        chunks = chunks_by_source[combo.source]
        existing = run_state.load_combo_progress(output_dir, combo_id)
        already_done = 0
        s2_already_done = False
        if existing and existing.get("chunk_total") == len(chunks):
            already_done = len(existing.get("chunks", []))
            s2_already_done = bool(existing.get("s2_done"))
        for chunk in chunks[already_done:]:
            est = timing_estimates.estimate_seconds(estimates, combo.model_s1, len(chunk))
            if est is not None:
                remaining_estimate += est
                have_estimate = True
        is_coherence = source_lang.upper() == combo.target_code.upper()
        s2_est = None
        if s2_model and s2_model != "—" and not is_coherence and not s2_already_done:
            s2_est = timing_estimates.estimate_seconds(
                estimates, s2_model, sum(len(c) for c in chunks))
            if s2_est is not None:
                remaining_estimate += s2_est
                have_estimate = True
        combo_s2_estimate[combo_id] = s2_est

    try:
        for idx, combo in enumerate(combos, 1):
            eta = round(remaining_estimate) if have_estimate else None
            progress_callback({"combo_idx": idx, "combo_total": total, "source": combo.source.name,
                                "model": combo.model_s1, "target": combo.target_code,
                                "mindset": combo.mindset_label, "eta_seconds": eta})

            if stop_event is not None and stop_event.is_set():
                log_callback("Stop requested — run paused, can be resumed.")
                return

            combo_id = combo.combo_id()
            if combo_id in done_ids:
                log_callback(f"[{idx}/{total}] SKIP (already done): {combo_id}")
                continue

            source_text = text_cache[combo.source]
            chunks = chunks_by_source[combo.source]
            chunk_total = len(chunks)

            progress = run_state.load_combo_progress(output_dir, combo_id)
            if progress is not None and progress.get("chunk_total") != chunk_total:
                log_callback(f"[{idx}/{total}] Stale progress for {combo_id} "
                             "(source chunking changed) — restarting combo.")
                run_state.delete_combo_progress(output_dir, combo_id)
                progress = None

            resolved_mindset = progress.get("resolved_mindset") if progress else None
            if resolved_mindset is None:
                resolved_mindset = combo.mindset_key
                if combo.mindset_key == AUTO_MINDSET:
                    if combo.source not in auto_cache:
                        try:
                            auto_cache[combo.source] = detect_mindset(source_text, mindset_model)
                        except Exception as e:
                            log_callback(f"[{idx}/{total}] [ERROR] mindset auto-detect failed "
                                         f"for {combo.source.name}: {e}")
                            _append_progress(progress_path, combo_id, "error", str(e))
                            continue
                    resolved_mindset = auto_cache[combo.source]

            is_coherence = source_lang.upper() == combo.target_code.upper()

            if progress and progress.get("out_name"):
                out_name = progress["out_name"]
            else:
                mindset_tag = resolved_mindset if combo.mindset_key != AUTO_MINDSET else f"auto-{resolved_mindset}"
                coherence_tag = "_coherence" if is_coherence else ""
                out_name = (
                    f"{datetime.now().strftime('%Y-%m-%d')}_{safe_filename(combo.model_s1.replace(':', '-'))}_"
                    f"{safe_filename(combo.source.stem)}_{combo.target_code.lower()}{coherence_tag}_"
                    f"{safe_filename(mindset_tag)}.md"
                )
            md_path = results_dir / out_name

            log_callback(
                f"[{idx}/{total}] {combo.source.name} | S1={combo.model_s1} | "
                f"{source_lang.upper()}→{combo.target_code.upper()}"
                + (" [Coherence]" if is_coherence else "")
                + f" | mindset={combo.mindset_label}"
                + (f" ({resolved_mindset})" if combo.mindset_key == AUTO_MINDSET else "")
            )

            try:
                set_model(combo.model_s1)

                resuming_combo = bool(progress) and md_path.exists()
                if resuming_combo:
                    # Cut away anything past the last confirmed chunk — an
                    # interrupted write may have left a dangling, unconfirmed
                    # block at the tail — before trusting the file to recover
                    # already-done chunks from.
                    with md_path.open("r+b") as f:
                        f.truncate(progress.get("md_byte_offset", 0))
                    md_text = md_path.read_text(encoding="utf-8")
                    recovered = _parse_chunk_blocks(md_text)
                    if len(recovered) != len(progress.get("chunks", [])):
                        log_callback(f"[{idx}/{total}] Progress for {combo_id} doesn't match "
                                     "its result file — restarting combo.")
                        resuming_combo = False

                if resuming_combo:
                    s1_parts = [text for _, text in recovered]
                    chunks_table = list(progress.get("chunks", []))
                    warnings = list(progress.get("warnings", []))
                    s1_done = bool(progress.get("s1_done"))
                    s2_done = bool(progress.get("s2_done"))
                    time_s2 = float(progress.get("time_s2", 0.0))
                    start_index = len(chunks_table)
                    elapsed_s1 = sum(c["seconds"] for c in chunks_table)
                    if start_index or s1_done:
                        log_callback(f"[{idx}/{total}] Resuming {combo_id}: "
                                     f"{start_index}/{chunk_total} chunks already done.")
                else:
                    run_state.delete_combo_progress(output_dir, combo_id)
                    header = _write_combo_header(combo, chunks, source_lang, s2_model,
                                                  is_coherence, coherence_level, resolved_mindset)
                    md_path.write_text(header, encoding="utf-8")
                    s1_parts, chunks_table, warnings = [], [], []
                    s1_done = s2_done = False
                    time_s2 = 0.0
                    start_index = 0
                    elapsed_s1 = 0.0
                    progress = {
                        "combo_id": combo_id, "resolved_mindset": resolved_mindset, "out_name": out_name,
                        "chunk_total": chunk_total, "s1_done": False, "s2_done": False, "time_s2": 0.0,
                        "md_byte_offset": md_path.stat().st_size, "chunks": [], "warnings": [],
                    }
                    run_state.save_combo_progress(output_dir, combo_id, progress)

                context = s1_parts[-1][-300:] if s1_parts else ""

                if not s1_done:
                    for i in range(start_index, chunk_total):
                        if stop_event is not None and stop_event.is_set():
                            log_callback(f"Stop requested — {combo_id}: {i}/{chunk_total} "
                                         "chunks done, can be resumed.")
                            return
                        chunk = chunks[i]
                        eta = round(remaining_estimate) if have_estimate else None
                        progress_callback({"combo_idx": idx, "combo_total": total, "source": combo.source.name,
                                            "model": combo.model_s1, "target": combo.target_code,
                                            "mindset": combo.mindset_label, "chunk_idx": i + 1,
                                            "chunk_total": chunk_total, "eta_seconds": eta})

                        t_chunk = time.monotonic()
                        data = translate_chunk(chunk, source_lang, combo.target_code, resolved_mindset,
                                                s2_model, i, context, coherence_level)
                        chunk_seconds = time.monotonic() - t_chunk
                        part = data.get("translation", "")
                        similarity = data.get("similarity")
                        s1_parts.append(part)
                        context = part[-300:] if part else ""
                        warnings += data.get("warnings", [])
                        elapsed_s1 += chunk_seconds

                        with md_path.open("a", encoding="utf-8") as f:
                            f.write(_format_chunk_block(i + 1, chunk_total, part, len(chunk),
                                                         chunk_seconds, similarity))
                        chunks_table.append({"index": i + 1, "chars": len(chunk),
                                              "seconds": round(chunk_seconds, 2), "similarity": similarity})
                        progress.update({"chunks": chunks_table, "warnings": warnings,
                                          "md_byte_offset": md_path.stat().st_size})
                        run_state.save_combo_progress(output_dir, combo_id, progress)

                        chunk_est = timing_estimates.estimate_seconds(estimates, combo.model_s1, len(chunk))
                        if chunk_est is not None:
                            remaining_estimate -= chunk_est

                    s1_done = True
                    progress["s1_done"] = True
                    run_state.save_combo_progress(output_dir, combo_id, progress)

                s1_translation = "\n\n".join(s1_parts)
                time_s1 = elapsed_s1

                # The server's coherence_mode branch (src == tgt) always skips
                # S2 regardless of s2_model — see app.py's translate_chunk
                # endpoint — so this only actually runs a pass outside
                # Coherence Mode, same as the CSV-driven test.py.
                s2_ran = bool(s2_model) and s2_model != "—" and not is_coherence
                if s2_ran and not s2_done:
                    if stop_event is not None and stop_event.is_set():
                        log_callback(f"Stop requested — {combo_id}: S1 done, S2 pending, can be resumed.")
                        return
                    t1 = time.monotonic()
                    data_s2 = translate_chunk(s1_translation, combo.target_code, combo.target_code,
                                               resolved_mindset, "", coherence_level=coherence_level)
                    time_s2 = time.monotonic() - t1
                    s2_translation = data_s2.get("translation", "")
                    warnings += data_s2.get("warnings", [])
                    with md_path.open("a", encoding="utf-8") as f:
                        f.write(f"## S2 — {s2_model}\n\n{s2_translation}\n\n")
                    s2_done = True
                    progress.update({"s2_done": True, "time_s2": time_s2, "warnings": warnings,
                                      "md_byte_offset": md_path.stat().st_size})
                    run_state.save_combo_progress(output_dir, combo_id, progress)
                    s2_est = combo_s2_estimate.get(combo_id)
                    if s2_est is not None:
                        remaining_estimate -= s2_est

                _write_combo_footer(md_path, chunks_table, warnings, time_s1, time_s2, s2_ran)
                log_callback(f"    -> {out_name} ({int(round(time_s1))}s)")
                _append_progress(progress_path, combo_id, "done", out_name)
                run_state.delete_combo_progress(output_dir, combo_id)

            except Exception as e:
                log_callback(f"[{idx}/{total}] [ERROR] {combo_id}: {e}")
                _append_progress(progress_path, combo_id, "error", str(e))
                continue

        if stop_event is None or not stop_event.is_set():
            # A combo whose except-branch above logged an "error" record
            # must not let the run be reported as complete just because the
            # for-loop otherwise ran to its end — re-check what's actually
            # confirmed done in batch_progress.jsonl rather than assuming it.
            combo_ids = {c.combo_id() for c in combos}
            finished_ids = _load_done_ids(progress_path)
            failed_ids = sorted(combo_ids - finished_ids)

            if expected_combo_total is not None and len(combos) < expected_combo_total:
                log_callback(
                    f"All {len(combos)} selected combination(s) finished, but this run was "
                    f"originally configured for {expected_combo_total} — NOT marking as complete. "
                    "Some source(s)/model(s)/target(s)/mindset(s) from the original selection "
                    "weren't available this time; restore the full original selection and "
                    "resume again to actually finish this run."
                )
            elif failed_ids:
                log_callback(
                    f"{len(combo_ids) - len(failed_ids)}/{len(combo_ids)} combination(s) finished, "
                    f"but NOT marking as complete — {len(failed_ids)} permanently failed: "
                    f"{', '.join(failed_ids)}. Fix the underlying error and resume again to retry them."
                )
            else:
                done_marker.write_text(datetime.now().isoformat(), encoding="utf-8")
                log_callback(f"Done. Results in: {results_dir}")
    finally:
        # Folds this run's freshly logged perf.csv rows into the persisted
        # timing estimates for next time — even on an early stop, whatever
        # ran still adds usable samples.
        timing_estimates.recompute_estimates(perf_log)
