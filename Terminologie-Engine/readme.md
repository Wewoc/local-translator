# Terminologie-Engine

Build pipeline for domain-specific terminology lists used by LocalTranslate.

Runs locally — source files and build scripts stay in this folder and are never committed to the repo.
The compiled output (`local_translator/terminology/`) is committed.

---

> **⚠️ Use at your own risk.** No guarantee of correctness or completeness of the generated term
> lists — always spot-check before relying on them. Provided as-is, with no warranty of any kind —
> see [LICENSE](../LICENSE).

---

## What it does

Domain-specific terms are protected before translation and restored afterwards.
The engine replaces terms in the source text with stable hash-based codes (`§Txxxxxxxx§`),
sends the protected text to the LLM, then substitutes the codes with the correct target-language terms.

This prevents the LLM from mistranslating, simplifying, or ignoring established terminology.

---

## Folder structure

```
Terminologie-Engine/          ← Local build environment (local only, not in repo)
  build_terminology.py        ← Step 1: Extract & bucket terms from sources
  filter_terminology.py       ← Step 2: Clean, filter and AI-validate lists
  apply_checkpoint.py         ← Recovery: Apply interrupted checkpoints manually
  validate.bat                ← Shortcut: Run Pass 3 validation (aya-expanse)
  
  IATE_download de-en-csv/    ← Raw source data (Manual download)
    IATE_export.csv
  MicrosoftTermCollection/    ← Raw source data (Manual download)
    GERMAN.tbx

local_translator/
  terminology/                ← Compiled output (COMMITTED to repo) — read by TermEngine at runtime
    build_report.txt          ← Overview of extracted and filtered terms
    technical/                ← Main mindset for engineering/IT
      de.json                 ← Filtered codes: German terms
      en.json                 ← Filtered codes: English terms
      custom_de.json          ← Manual overrides
    general/
      de.json
      en.json
    legal/                    ← Other mindsets (same structure as general)
    medical/
    editorial/
    academic/
    marketing/
    political/
```

---

## List format

Each mindset has two files — one per language:

```json
{
  "§T3f2a1b4c§": "Betriebssystem",
  "§T9e1d5f2a§": "Gruppenrichtlinienobjekt"
}
```

```json
{
  "§T3f2a1b4c§": "Operating System",
  "§T9e1d5f2a§": "Group Policy Object"
}
```

Codes are hash-based and build-stable — the same DE term always gets the same code across builds.
Adding a new target language: place a new `fr.json` in each mindset folder. No code changes needed.

---

## Step 1 — Build

Reads TBX and/or CSV source files, classifies entries by mindset domain, writes compiled lists.

```powershell
python build_terminology.py `
  --mtc "MicrosoftTermCollection\GERMAN.tbx" `
  --iate "IATE_download de-en-csv\IATE_export.csv" `
  --out "..\local_translator\terminology"
```

**Arguments:**

| Argument | Description |
|---|---|
| `--mtc` | Path to TBX file (e.g. MicrosoftTermCollection GERMAN.tbx) |
| `--iate` | Path to pipe-separated CSV (E_ID, E_DOMAINS, L_CODE, T_TERM, T_RELIABILITY) |
| `--out` | Output folder — becomes `local_translator/terminology/` in the repo |
| `--filter` | Optional: Ollama pass to pre-filter obvious non-terms during build |
| `--model` | Ollama model for `--filter` (default: mistral) |

**Key settings in `build_terminology.py`:**

| Setting | Default | Effect |
|---|---|---|
| `IATE_MIN_RELIABILITY` | 3 | Minimum IATE reliability: 3=Reliable, 4=Very reliable |
| `GENERAL_MIN_DOMAINS` | 3 | How many mindset domains a term must cover to land in `general/` |

After the build, run Step 2 to clean the lists.

---

## Step 2 — Filter

Three independent passes — run in any combination.
Every write operation creates a backup in `local_translator/terminology/{mindset}/backup/`.

### Pass 1 — Blocklist (always active)

Removes fragments, everyday words, all-caps artefacts from IATE parsing.

```powershell
python filter_terminology.py --dir "..\local_translator\terminology"
```

Use `--dry-run` to preview without writing:

```powershell
python filter_terminology.py --dir "..\local_translator\terminology" --dry-run
```

### Pass 2 — Domain filter (--filter)

Ollama evaluates: *"Is this a genuine domain-specific term?"*
Recommended model: `mistral` or `qwen2.5:7b`

```powershell
python filter_terminology.py --dir "..\local_translator\terminology" --filter --model mistral
```

### Pass 3 — Translation validation (--validate)

`aya-expanse` evaluates: *"Is the DE→EN translation correct and natural?"*
Checks translation quality of pairs — not just whether a term is domain-specific.
Recommended model: `aya-expanse:latest` (multilingual, strong DE/EN coverage)

```powershell
python filter_terminology.py --dir "..\local_translator\terminology" --validate --model aya-expanse:latest
```

### All passes combined

```powershell
python filter_terminology.py --dir "..\local_translator\terminology" --filter --validate --model aya-expanse:latest
```

### Single mindset

```powershell
python filter_terminology.py --dir "..\local_translator\terminology" --mindset technical --validate --model aya-expanse:latest
```

**Note on runtime:** Pass 3 on `technical/` takes 2–3 hours at ~67k entries.
Run overnight using `validate.bat`.

### Resume after interruption

Pass 3 writes a checkpoint file after every batch (`.filter3_checkpoint_{mindset}.json` in the
`Terminologie-Engine/` folder). If the process is interrupted, simply restart — it will continue
from the last completed batch automatically.

If the checkpoint exists but the list size has changed (e.g. after `apply_checkpoint.py` was used),
the checkpoint is considered stale and the pass restarts from the beginning.

### Emergency recovery — apply_checkpoint.py

If the process was interrupted before the checkpoint fix was applied and the checkpoint contains
valid data, use `apply_checkpoint.py` to write the already-validated entries directly to the JSON
files without re-running the batches:

```powershell
python apply_checkpoint.py --mindset technical --dir "..\local_translator\terminology"
```

The script reports how many batches were completed and warns if entries from unvalidated batches
were dropped. After applying, re-run Pass 3 on the now-smaller list to validate the remainder.

---

## Step 3 — Pack (optional)

Once Steps 1–2 are done, `local_translator/terminology/` holds a folder tree —
8 mindsets times N languages, each its own JSON file. To hand that data to someone else,
or to bundle it with a `local_translator/compiler/build.py` build, pack it into a single
portable file instead:

```powershell
python pack_terminology.py --dir "..\local_translator\terminology"
```

Writes `terminology.data` (gzip-compressed JSON, named `.data` rather than `.gz` on
purpose — a `.gz` extension had Windows Explorer navigate INTO the file via an installed
archive tool instead of treating it as one file, misleadingly showing "0 KB") into that
same folder by default, or wherever `--out` points. `local_translator/terminology/
terminology.py` loads this file in preference to the loose tree if it finds one next to
itself (or next to a built EXE) — drop the one file, no folder structure to recreate.
`custom_de.json`/`custom_en.json` are never packed (see `local_translator/docs/CHANGELOG.md`
— that override support was reverted
and terminology.py doesn't read them either currently).

---

## Mindsets

| Mindset | Domain coverage |
|---|---|
| `general` | Terms appearing in 3+ different mindset domains — cross-domain vocabulary |
| `technical` | IT, software, electronics, telecommunications, engineering |
| `legal` | Law, contracts, legislation, judicial, intellectual property |
| `medical` | Medicine, health, pharmacology, biology, clinical |
| `editorial` | Media, journalism, publishing, culture, language |
| `academic` | Education, research, science, statistics, mathematics |
| `marketing` | Trade, finance, economics, advertising, business |
| `political` | Politics, government, international relations, defence, EU |

MTC entries without domain information default to `technical/`.

---

## Adding custom terms

For terms missing from the built lists (e.g. modern AI terminology not covered by IATE or MTC),
place manual entries in `custom_de.json` and `custom_en.json` in the relevant mindset folder.
These are loaded and merged automatically at runtime alongside the main lists.

---

## Rebuilding

When source files are updated or configuration changes, re-run Steps 1 and 2.
Old backups in `local_translator/terminology/{mindset}/backup/` can be deleted manually to save space.
