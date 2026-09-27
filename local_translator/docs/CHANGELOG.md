# Changelog — LocalTranslate

## 2026-09-27

### Added
- `version.py` — single-source `APP_VERSION`, manually bumped, no
  auto-derivation. Exposed via `/config` and `FastAPI(version=...)`, shown
  in the UI's status bar (`#appVersion`, right-aligned). Starts at `0.1.0`
  (README still marks the project Beta). See `docs/MAINTENANCE_translator.md`
  ("Version — APP_VERSION") and `compiler/README.md` for when to bump it.
- `local_translator/compiler/` — standalone `--onedir` PyInstaller build
  (`build.py`, `build_manifest.py`, `build_gui.py`), with an optional
  packed terminology engine (`Terminologie-Engine/pack_terminology.py`,
  new) included as `terminology.data` next to the built EXE. See
  `compiler/README.md`.
- `test/test_gui.py` + `test/runner_core.py` — Tkinter GUI batch runner for
  quality tests, alongside the existing CSV-driven `test.py`. Pick source
  texts, S1 models, target languages, and mindsets (individual ones,
  "General", and/or "Auto-detect" via `/mindset/detect`) by clicking,
  builds the full cartesian product, runs it against a locally running
  LocalTranslate server, writes one result `.md` per combination.
  Start/stop/resume (`batch_progress.jsonl` + `batch_done.marker` per run
  folder, same model as GLA-NeedfulThings/mcp-llm-tester's GUI runner).
  Sends the Coherence Mode `coherence_level` on every call (server only
  acts on it when source lang == target lang, nothing to gate
  client-side); pins `translategemma*` to the top of the S1/S2 model
  lists and defaults the mindset-detect model from `/config`. Run via
  `test/test_gui.py` or `test_gui.bat`. UI kept English throughout (was a
  German/English mishmash in earlier iterations).
- `compiler/build.py` builds `test/test_gui.py` as a standalone
  `dist/LocalTranslate/test/LocalTranslate-Tester.exe` alongside the app by
  default (opt out via `--no-tester`, unchecked via `build_gui.py`'s
  matching checkbox), `--onefile` rather than the app's `--onedir` (see
  "Fixed" below and `compiler/README.md`). Lands in the release ZIP
  automatically, since it's built before the ZIP step. Reuses the app's
  build venv — the tester has no dependencies beyond stdlib + Tkinter.
  First shipped as an opt-in `--with-tester` flag — flipped to on-by-default
  after it twice went unnoticed and silently produced a tester-less build.

### Fixed
- `core/config.py`/`app.py`: path resolution (`PROJECT_ROOT`, `INDEX_PATH`,
  `MINDSETS_PATH`, the `/static` mount) used `Path(__file__)`-based
  resolution, which breaks once the app runs as a PyInstaller build —
  frozen modules aren't real files at that path. Now distinguishes
  `PROJECT_ROOT` (`sys.executable`'s folder — user-editable files) from
  `_BUNDLE_ROOT` (`sys._MEIPASS` — `--add-data` assets) when frozen, dev
  mode unchanged.
- `terminology/terminology.py`: added `terminology.data` pack-file support
  (loads it in preference to the loose per-mindset/per-lang JSON tree if
  present), so the terminology engine can be handed over or bundled as one
  file instead of a folder tree.
- `test/test_gui.py`: `SOURCE_DIR`/`RESULTS_ROOT`/`PERF_LOG` resolved via
  raw `Path(__file__).parent`, same class of bug already fixed once this
  session in `core/config.py` — breaks once frozen (PyInstaller), since a
  bundled module's `__file__` doesn't point next to the EXE. Now resolves
  its own directory via `sys.executable` when `sys.frozen`, `__file__`
  otherwise, same pattern as `core/config.py`'s `PROJECT_ROOT`. Needed for
  the new `--with-tester` build (see "Added").
- Legal-mindset test run (`translategemma:12b`, DE→EN) leaked a raw,
  half-mangled term-engine placeholder into the output: `§Ta5d01bf9§`
  ("Höhere Gewalt") came back as `Section [Ta5d01bf9]`. Root cause:
  `translate_ollama()`'s S1 prompt never told the model to leave
  `§Txxxxxxxx§`/`§Lxxxxxxxx§` tokens alone (unlike `run_coherence_pass()`,
  which already has this instruction) — the model read the `§` as a legal
  section mark, plausible in legal source text that itself uses `§ 4 ...`,
  and reformatted the placeholder into a citation-style `Section [...]`,
  which `TermEngine.restore()`'s exact-match `str.replace()` then couldn't
  catch.
- `engines/ollama.py` → `translate_ollama()`: added the same
  placeholder-preservation instruction `run_coherence_pass()` already
  carries, so S1 is told up front not to touch `§...§` tokens.
- `terminology/terminology.py` → `TermEngine._repair()`: post-processing
  safety net broadened beyond whitespace-only damage (`§ T1a2b3c4d §`) to
  also catch delimiters dropped or swapped for brackets/parens
  (`[T1a2b3c4d]`, `(T1a2b3c4d)`) or dropped entirely (`T1a2b3c4d`) —
  matches only ids actually issued for the current call (via `code_map`),
  never arbitrary T-shaped hex strings, so it can't misfire on real prose.
  Falls back to the source-language term if the target-language term is
  missing, so a protected term always ends up back in the output as real
  text instead of a dangling code. `restore()` now passes `code_map`
  through to `_repair()` for this.
- `TermEngine.verify()`: dropped the "Code lost" check — it compared
  `protected` vs. `restored` and flagged every code no longer literally
  present in `restored`, which is also true for every *successfully*
  restored term (the code is gone because it was replaced by real text),
  so it was reporting `[TermEngine] Code lost: ...` as a false positive on
  every clean translation. Kept the "Code not replaced" check (canonical
  `§Txxxxxxxx§` still present verbatim), the only signal that now remains
  meaningful given `_repair()`'s broadened coverage above.
- Verified: reproduced the exact `Section [Ta5d01bf9]` failure from the
  test report, confirmed `restore()` now resolves it to the real target
  term with correct spacing, confirmed whitespace-damaged and bare
  (no-delimiter) variants repair cleanly too, and confirmed a normal clean
  restore no longer trips `verify()`.
- A follow-up batch re-run (same test suite, both `translategemma:12b` and
  `:4b`) confirmed the legal-mindset fix above, but surfaced a related,
  independent failure: `.../mindset_test_marketing_..._auto-marketing.md`
  (12b) ended with a bare `§L12345678§` — a `link_guard` placeholder with
  an id that was never issued (`terms_protected: 0`, no URL in the source
  text at all), i.e. the model hallucinated a well-formed-looking token
  rather than mangling a real one. Neither `link_guard.restore()` (exact-
  match only) nor `verify()` (report-only, no repair, deliberately, per
  the 2026-08-15 entry above) could turn this into real content, so the
  raw token reached the visible output.
- `core/link_guard.py`: added `_repair()`, mirroring `TermEngine._repair()`
  — recovers ids actually issued for the call (from `mapping`) that got
  whitespace-damaged or had their delimiters dropped/swapped for brackets/
  parens; wired into `restore()`. New `strip_unresolved(text, mapping)`
  final safety net: removes any `§L...§`-shaped token still left after
  that — known-but-unrecoverable or entirely unknown/hallucinated — so it
  never reaches the visible output, and returns what it removed instead of
  the token silently vanishing.
- `terminology/terminology.py`: added the equivalent `TermEngine.
  strip_unresolved(text, code_map)` — `_repair()` only ever touched ids
  actually issued for the call, so a hallucinated `§Txxxxxxxx§` with an
  unknown id would previously slip past both `_repair()` and `verify()`
  (which only checks ids present in `code_map`) entirely unnoticed.
- `app.py`: both `/translate` and `/translate/chunk` now call
  `strip_unresolved()` for TermEngine and link_guard right after their
  `restore()` calls, replacing the old report-only `verify()` calls (a
  strict superset: same detection, plus removal). Collected warnings are
  logged server-side (`[TermEngine] ...` / `[LinkGuard] ...`, chunk-
  indexed on `/translate/chunk`) and now also returned to the caller as
  `response["warnings"]` — same text in both places, so a caller and the
  server log can be matched up directly instead of the caller having no
  visibility into a silent strip.
- `test/runner_core.py`: `translate_chunk()` responses' `warnings` are
  collected across all chunks (S1 and, if run, S2) and passed into
  `_build_result_md()`, which now renders a `## Warnings` section right
  under the `## Run` table (plus a one-line count in the table itself)
  when any were stripped — so a batch test report shows what happened and
  where, instead of a silently cleaned-up translation looking identical to
  one that never had a problem. `test/test.py` (the separate CSV-driven
  runner) was not touched — not the runner these reports came from.
- Verified: reproduced the exact `§L12345678§` case (no mapping entry at
  all) — `strip_unresolved()` removes it and reports "Unknown/hallucinated
  link placeholder stripped", and a recoverable case (known id, delimiters
  swapped for brackets, e.g. `[L12345678]`) is resolved back to the real
  URL by the new `_repair()` before `strip_unresolved()` ever sees it.
- Verified end-to-end against the real pipeline (not just unit-level): a
  full batch re-run (`translategemma:12b`/`:4b`, all mindsets, live Ollama)
  reproduced the original `marketing/12b` hallucination one more time and
  confirmed the fix in place — the `## Run` table shows `Warnings | 1 —
  see below, also logged server-side`, the new `## Warnings` section reads
  `[LinkGuard] chunk 0: Unknown/hallucinated link placeholder stripped:
  §L12345678§`, and the translation text itself ends cleanly with no raw
  placeholder. All other mindsets/combos in the same run stayed clean,
  including the legal-mindset `force majeure` case from the first fix
  above, confirming no regression.

## 2026-08-16

### Fixed
- S1/S2/Mindset-AI model dropdowns (and FROM/TO language dropdowns) stayed
  empty on every fresh page load, regardless of which working copy the app
  was started from and regardless of whether Ollama was reachable. Root
  cause: `static/app.js` defines `init()` (loads `/config`, populates
  FROM/TO + mindset + model dropdowns, starts `checkOllama()`) but nothing
  ever called it — no `DOMContentLoaded` listener, no `<body onload>`.
  Confirmed via Network tab: after a hard reload, `ui.js`/`engines.js`/
  `translate.js`/`app.js` all loaded (200), but zero requests to `/config`
  or `/ollama/status` ever fired.
- `static/app.js`: added `document.addEventListener('DOMContentLoaded', init);`
  at the end of the file, after `setupInput()`.
- Verified: hard reload after the fix shows `/config` and `/ollama/status`
  firing in the Network tab, and FROM/TO/S1/S2/Mindset-AI populate
  correctly with the live Ollama model list.

## 2026-08-15

### Fixed (live-testing follow-up)
- Coherence Pass could leak a raw `§Lxxxxxxxx§` link_guard placeholder into
  the visible output. Root cause: `run_coherence_pass()`'s editing prompt
  told the model to smooth transitions but never told it to leave opaque
  placeholder tokens alone — unlike a straight translation prompt, an
  editing prompt is prone to "fixing" what looks like noise, and a model
  that alters even one character of a placeholder (observed: an added
  digit) makes `link_guard.restore()`'s exact-match `str.replace()` miss
  it, so the mangled token stays in the output verbatim.
- `engines/ollama.py` → `run_coherence_pass()`: prompt now explicitly
  instructs the model to copy `§Lxxxxxxxx§`/`§Txxxxxxxx§`-shaped tokens
  character-for-character and never alter them.
- `core/link_guard.py`: new `verify(restored, mapping)` — scans the
  restored text for any leftover `§L...§`-shaped token (exact-but-unreplaced
  or model-mangled) and reports it, mirroring `TermEngine.verify()`. Doesn't
  repair anything (no silent fallback), just makes the failure visible
  instead of leaking silently into the UI. Wired into both `/translate` and
  `/translate/chunk` in `app.py`, right after `link_guard.restore()` —
  issues print server-side as `[LinkGuard] ...`, same pattern as the
  existing `[TermEngine] ...` logging.
- Verified: reproduced the exact failure mode (protect a bare URL, simulate
  a model mangling the placeholder id by one digit, confirm `restore()`
  leaves it in the text and `verify()` flags it) and confirmed the
  clean/unmangled path still restores and verifies clean.

### Fixed
- Coherence Pass (source_lang == target_lang) restored. The feature was
  added in full on 2026-08-14, then almost entirely undone the next day by
  an unrelated "rolback" commit (`210ecbd`) that was meant to revert
  something else and swept this up with it. `core/diff_utils.py` was left
  behind by that rollback — still on disk, no longer imported anywhere —
  which is why the frontend kept showing "Source and target language are
  identical" instead of switching into Coherence Mode.
- Restored: `run_coherence_pass()` in `engines/ollama.py`, wiring in both
  `/translate` and `/translate/chunk` in `app.py`, and the frontend
  (`index.html`, `static/app.js`, `static/style.css`, `static/translate.js`,
  `static/ui.js`) that removes the `src === tgt` block, disables S2/external
  engines while active, and renders the word-diff with a similarity warning.
  Content verified identical (ignoring line-ending noise) to the original
  `a864168` commit via `git diff --ignore-space-at-eol`.
- Verified with a mocked-Ollama FastAPI TestClient run: normal DE→EN path
  unaffected (no `diff`/`similarity` in response, S2 still runs), DE→DE
  triggers the coherence pass and skips S2 even when an `s2_model` is
  requested, and the `source_lang`/`target_lang` comparison is
  case-insensitive (`de` vs `DE` still triggers Coherence Mode).
- Left untouched (not part of this fix, unrelated to the reported bug):
  the same rollback commit also reverted custom-terminology-override
  support (`custom_de.json`/`custom_en.json`) in `terminology/terminology.py`
  and changed `pipeline_mindset_model` in `config.yaml` — both still at
  their post-rollback state.

## 2026-08-06

### Added
- `core/link_guard.py` — protects URLs, markdown links, and file paths from
  translation pipeline mangling (S1 was spelling out protocol prefixes like
  `https://` as plain text). Placeholder namespace `§Lxxxxxxxx§`, independent
  from TermEngine's `§Txxxxxxxx§`.
- Wired into both `/translate/chunk` and `/translate` endpoints in `app.py`,
  wrapping outside TermEngine — protect before S1, restore after S2 (if active).

### Notes
- Grey-zone case (bare prose paths without backticks or markdown syntax,
  e.g. "liegt unter src/docs/") intentionally left unprotected — see
  session notes for rationale.

### Added
- Dedicated model selection for mindset auto-detection (`/mindset/detect`),
  decoupled from the S1 translation model. Previously `detect_mindset()`
  reused `state.active_model` — a translation model repurposed for
  classification, which likely explains the observed unreliability
  (near-constant fallback to `"general"`).
- New config key `pipeline_mindset_model` in `config.yaml` (default `""`,
  falls back to S1 model — no breaking change).
- New UI dropdown "Mindset AI" in the statusbar (`mindsetModelSelect`),
  populated from the same Ollama model list as S1/S2. Follows the S2
  pattern (request-scoped, no server state) rather than the S1 pattern
  (stateful `/ollama/set_model`) — see `REFERENCE_translator.md` for
  rationale.
- New "AI: {mindset}" label next to the mindset dropdown, shows what
  the model actually picked. Persists after translation completes —
  only cleared by `clearAll()` (✕ Clear) or overwritten by the next
  detection run, not reset automatically when translation finishes.
- Fixed: mindset auto-detection was incorrectly coupled to
  `mode === 'debounce'` (pre-existing behavior, not introduced this
  session) — Manual/Sentence modes never triggered it. First fix
  attempt (separate `mindsetDebounceTimer`, decoupled from mode) was
  itself flawed — fired on every keystroke regardless of mode, ahead
  of the actual translation trigger. Final fix: detection moved inside
  `translate()` itself as the first step, so it fires exactly once per
  translation run, uniformly across all three triggers (button, Enter,
  debounce timeout) — no separate timer needed.
- 8 new mindset classification test texts added to `test/source/`
  (`mindset_test_*.md`, one per mindset: general, technical, legal,
  medical, editorial, academic, marketing, political) — no prior test
  data existed for classification quality, only translation-quality
  benchmarks (NTREX-128).

### Changed
- `engines/ollama.py` → `detect_mindset()` signature: new optional
  `model` parameter. `app.py` → `DetectMindsetRequest` gained
  `mindset_model: str = ""`.

### Added (this session, follow-up fix)
- `detect_mindset()` now sends `options: {temperature: 0}` to Ollama.
  Without it, classification was non-deterministic — same input text
  could flip between e.g. "medical" and "general" across runs, since
  no temperature was set and the model default (~0.7–0.8) allows
  sampling variance. Fix scoped to `detect_mindset()` only —
  `translate_ollama()` and `run_s2()` intentionally keep default
  sampling.

### Known issue — NOT fixed, logged for a future session
- S2 language drift check (`run_s2()` in `engines/ollama.py`) only
  catches drift back to the *source* language (non-ASCII ratio
  threshold, calibrated for German umlauts). Observed case: S2 model
  `aya-expanse:latest` translated EN→ES instead of editing EN in
  place — Spanish has too low a non-ASCII ratio to trip the existing
  `output_non_ascii > input_non_ascii + 0.15` check. Result: S2
  silently returns Spanish text instead of falling back to S1.
  Needs a proper fix (e.g. language detection, not just ASCII ratio)
  — out of scope for the mindset-detection session, tracked here for
  follow-up.

### Not part of this session (deliberately out of scope)
- Floating Mindset (per-chunk detection) — separate roadmap item,
  mindset is still detected once for the whole text, before chunking.