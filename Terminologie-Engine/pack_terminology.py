"""
pack_terminology.py — Packs compiled terminology lists into one portable file

Reads local_translator/terminology/<mindset>/<lang>.json (as built by
build_terminology.py + filter_terminology.py) and writes a single
gzip-compressed JSON file, terminology.data. local_translator/terminology/
terminology.py loads that file in preference to the loose per-file tree if
it finds one.

Point of this tool: handing the term lists to someone else (a friend, a
packaged build) means giving them one file instead of a folder tree with
8 mindsets times N languages each. Drop the resulting terminology.data
next to a built EXE (or next to terminology.py for local testing) — no
other setup needed, no folder structure to recreate.

Named .data, not .gz or .pack.gz, on purpose: on at least one real Windows
machine, an installed archive tool intercepted the .gz extension and had
Explorer navigate INTO the file instead of showing it as a single file,
displaying a misleading "0 KB" for its (correctly non-empty) contents.
.data isn't a registered archive extension anywhere, so nothing tries to
browse into it — Explorer just shows it as a normal file with its real
size. The bytes are still plain gzip; gzip.open() doesn't care about the
extension, only the file's own magic bytes.

custom_*.json overrides are intentionally NOT packed — terminology.py does
not currently load them either (that support was reverted, see
local_translator/docs/CHANGELOG.md); packing only what the runtime
actually reads avoids a pack that silently carries terms nobody will ever
see used.

Mindset folder names are checked against ALL_MINDSETS (same list
build_terminology.py/filter_terminology.py already use) — an unrecognized
name still gets packed (no data is dropped on a guess), but is flagged,
since local_translator's TermEngine only ever looks up mindsets it knows
about (pipeline/mindsets.json) — anything else would otherwise sit in the
pack unused with nothing telling you so.

Usage:
  python pack_terminology.py --dir ../local_translator/terminology
  python pack_terminology.py --dir ../local_translator/terminology --out ../local_translator/terminology.data
"""

import argparse
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

ALL_MINDSETS = ["general", "technical", "legal", "medical",
                "editorial", "academic", "marketing", "political"]


def pack_terminology(src_dir: Path, out_path: Path) -> dict:
    """
    Reads <src_dir>/<mindset>/<lang>.json for every mindset folder present
    (custom_*.json excluded), writes a single gzip-compressed JSON file to
    out_path.

    Returns a report: {mindset: {lang: term_count}} — empty if src_dir
    contained no usable mindset data.
    """
    mindsets_payload: dict[str, dict[str, dict]] = {}
    report: dict[str, dict[str, int]] = {}

    for mindset_dir in sorted(src_dir.iterdir()):
        if not mindset_dir.is_dir():
            continue
        mindset = mindset_dir.name
        if mindset not in ALL_MINDSETS:
            print(f"  [pack] Warning: '{mindset}' is not a known mindset "
                  f"({', '.join(ALL_MINDSETS)}) — packed anyway, but "
                  f"local_translator will never look it up.")

        langs: dict[str, dict] = {}
        lang_counts: dict[str, int] = {}
        for json_file in sorted(mindset_dir.glob("*.json")):
            if json_file.stem.startswith("custom_"):
                continue
            lang = json_file.stem
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  [pack] Skipping unreadable {json_file}: {e}")
                continue
            langs[lang] = data
            lang_counts[lang] = len(data)

        if langs:
            mindsets_payload[mindset] = langs
            report[mindset] = lang_counts

    payload = {
        "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mindsets": mindsets_payload,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Pack compiled terminology lists into one portable file.")
    parser.add_argument("--dir", required=True,
                         help="Path to the compiled terminology/ folder (mindset/lang.json tree)")
    parser.add_argument("--out", default=None,
                         help="Output path (default: <dir>/terminology.data)")
    args = parser.parse_args()

    src_dir = Path(args.dir).resolve()
    if not src_dir.is_dir():
        print(f"  [pack] Not found: {src_dir}")
        raise SystemExit(1)

    out_path = Path(args.out).resolve() if args.out else src_dir / "terminology.data"

    report = pack_terminology(src_dir, out_path)

    if not report:
        print(f"  [pack] No mindset data found under {src_dir} — nothing packed.")
        raise SystemExit(1)

    print(f"  Packed -> {out_path}")
    total = 0
    for mindset in sorted(report):
        counts = report[mindset]
        total += sum(counts.values())
        parts = ", ".join(f"{lang}={n}" for lang, n in sorted(counts.items()))
        print(f"    {mindset:<12} {parts}")
    size_kb = out_path.stat().st_size / 1024
    print(f"  {total} terms total, {size_kb:.1f} KB compressed")


if __name__ == "__main__":
    main()
