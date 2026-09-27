#!/usr/bin/env python3
"""
compiler/build.py
Builds LocalTranslate as a --onedir PyInstaller package, optionally
including a packed terminology engine.

--onedir instead of --onefile: the app is launched interactively and
waits for the server to come up before opening the browser (app.py's
_open_when_ready() loop) — --onefile re-extracts itself into a fresh temp
folder on every single launch, making exactly that wait longer every
time. --onedir unpacks once, at build time; every launch after that is as
fast as running the script directly.

dist/LocalTranslate/ is disposable build output, not a persistent
install — the actual hand-off unit is the release ZIP. That's why this
script wipes it clean before every build instead of trying to preserve
anything inside it (e.g. an old terminology.data from a previous
--term-engine-dir run): whoever unzips the release and runs the app long
enough to accumulate real state (edited config.yaml, exports/, logs/)
does so from the unzipped copy, never from dist/ itself.

Three modes:
  App only:               python build.py
  App + terminology:      python build.py --term-engine-dir <path>
  Terminology pack only:  python build.py --only-term-engine
                               --term-engine-dir <path> --out <path>
    (skips the app build entirely — no PyInstaller, no build venv needed;
    useful for refreshing an already-built dist/'s terminology.data,
    or handing someone just an updated pack file, without touching the app)

The --term-engine-dir path is never remembered or defaulted — the
compiled terminology/ folder this points at lives outside this repo
(built locally via Terminologie-Engine/), so a stored/hardcoded path
would only ever be valid on one machine.

Run from local_translator/:
    python compiler/build.py
    python compiler/build.py --term-engine-dir /path/to/terminology
    python compiler/build.py --only-term-engine --term-engine-dir /path/to/terminology --out /path/to/terminology.data
    python compiler/build.py --no-zip
    python compiler/build.py --with-tester
"""

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import build_manifest as manifest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "Terminologie-Engine"))
from pack_terminology import pack_terminology  # noqa: E402


def check_source(root: Path):
    missing = [name for name in (manifest.ENTRY_POINT, "requirements.txt") if not (root / name).exists()]
    if missing:
        print(f"  [build] Not a local_translator checkout (missing {', '.join(missing)}): {root}")
        raise SystemExit(1)


def ensure_build_venv(root: Path) -> Path:
    """
    Creates (if missing) or reuses a project-local build venv at
    local_translator/.venv-build/, installs requirements.txt + PyInstaller
    into it, returns its python executable.

    Deliberately project-local, not a shared system-wide location: this
    keeps whatever else happens to be installed in the machine's global
    Python (or another project's venv) from ever being visible to
    PyInstaller's Analysis step, at the cost of nothing more than one
    `pip install` per dependency change.
    """
    print("\n[1/4] Checking build venv ...")
    venv_dir = root / ".venv-build"
    venv_python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    if not venv_python.exists():
        print(f"  Not found at {venv_dir} — creating ...")
        subprocess.check_call([sys.executable, "-m", "venv", str(venv_dir)])
    else:
        print(f"  Reusing existing venv: {venv_dir}")

    subprocess.check_call([str(venv_python), "-m", "pip", "install", "-q",
                            "-r", str(root / "requirements.txt")])
    subprocess.check_call([str(venv_python), "-m", "pip", "install", "-q", "pyinstaller"])
    print("  Build venv ready.")
    return venv_python


def build_exe(root: Path, venv_python: Path, dist_dir: Path):
    print("\n[2/4] Building EXE (--onedir) ...")

    if dist_dir.exists():
        print(f"  Clearing previous build output: {dist_dir}")
        shutil.rmtree(dist_dir)

    sep = ";" if sys.platform == "win32" else ":"
    add_data_args = []
    for src, dest in manifest.DATA_FILES:
        add_data_args += ["--add-data", f"{root / src}{sep}{dest}"]

    hidden_args = []
    for h in manifest.HIDDEN_IMPORTS:
        hidden_args += ["--hidden-import", h]

    cmd = [
        str(venv_python), "-m", "PyInstaller",
        "--onedir",
        "--name", manifest.APP_NAME,
        "--distpath", str(root / "dist"),
        "--workpath", str(root / "build"),
        "--specpath", str(Path(__file__).parent),
        *add_data_args,
        *hidden_args,
        str(root / manifest.ENTRY_POINT),
    ]
    result = subprocess.run(cmd, cwd=str(root))
    if result.returncode != 0:
        print("\n  [build] PyInstaller failed — see output above.")
        raise SystemExit(1)

    if not dist_dir.exists():
        print(f"  [build] PyInstaller reported success but {dist_dir} doesn't exist.")
        raise SystemExit(1)


def build_tester_exe(root: Path, venv_python: Path, dist_dir: Path):
    """
    Builds test/test_gui.py (the batch quality-test runner) as a second,
    standalone EXE — --onefile, unlike the app's --onedir. The app's
    --onedir choice is about avoiding a re-extract on every launch while
    something waits on it (app.py's server-startup poll); the tester has
    no such wait, and --onefile places the EXE directly at
    dist/<app>/test/LocalTranslate-Tester.exe with no extra PyInstaller
    subfolder — required so test_gui.py's own frozen-path logic (_HERE)
    still finds source/ and results/ next to itself and logs/ one level
    up, mirroring its location in a checkout (local_translator/test/).
    Reuses the same build venv as the app build — the tester has no
    dependencies beyond stdlib + Tkinter, already covered by it.
    """
    print("\n[extra] Building batch-tester EXE (--onefile) ...")
    tester_dist = dist_dir / "test"

    cmd = [
        str(venv_python), "-m", "PyInstaller",
        "--onefile",
        "--name", manifest.TESTER_NAME,
        "--distpath", str(tester_dist),
        "--workpath", str(root / "build" / "tester"),
        "--specpath", str(Path(__file__).parent),
        str(root / manifest.TESTER_ENTRY_POINT),
    ]
    result = subprocess.run(cmd, cwd=str(root))
    if result.returncode != 0:
        print("\n  [build] PyInstaller failed while building the tester — see output above.")
        raise SystemExit(1)

    exe_name = manifest.TESTER_NAME + (".exe" if sys.platform == "win32" else "")
    exe_path = tester_dist / exe_name
    if not exe_path.exists():
        print(f"  [build] PyInstaller reported success but {exe_path} doesn't exist.")
        raise SystemExit(1)
    print(f"  {exe_path}")


def copy_external_defaults(root: Path, dist_dir: Path):
    print("\n[3/4] Copying editable defaults ...")
    for name in manifest.EXTERNAL_DEFAULTS:
        src = root / name
        if not src.exists():
            print(f"  [build] Warning: {name} not found in {root} — skipped.")
            continue
        shutil.copy2(src, dist_dir / name)
        print(f"  {name} -> {dist_dir / name}")


def include_term_engine(term_engine_dir: Path, dist_dir: Path):
    out_path = dist_dir / "terminology.data"
    if not term_engine_dir.is_dir():
        print(f"  [build] --term-engine-dir not found: {term_engine_dir}")
        raise SystemExit(1)
    report = pack_terminology(term_engine_dir, out_path)
    if not report:
        print(f"  [build] No terminology data found under {term_engine_dir} — nothing packed.")
        return
    total = sum(sum(counts.values()) for counts in report.values())
    print(f"  {out_path.name}: {len(report)} mindset(s), {total} terms total")


def build_zip(dist_dir: Path):
    zip_path = dist_dir.parent / f"{manifest.APP_NAME}.zip"
    print(f"\n[4/4] Creating {zip_path.name} ...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in dist_dir.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(dist_dir.parent))
    print(f"  -> {zip_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Build LocalTranslate as a --onedir package, optionally with a packed terminology engine.")
    parser.add_argument("--term-engine-dir", default=None,
                         help="Path to a compiled terminology/ folder (mindset/lang.json tree) to pack and include.")
    parser.add_argument("--only-term-engine", action="store_true",
                         help="Skip the app build entirely — only pack the terminology engine. "
                              "Requires --term-engine-dir and --out.")
    parser.add_argument("--out", default=None,
                         help="Output path for --only-term-engine mode.")
    parser.add_argument("--no-zip", action="store_true", help="Skip creating the release ZIP.")
    parser.add_argument("--with-tester", action="store_true",
                         help="Also build test/test_gui.py (the batch quality-test runner) as "
                              "dist/<app>/test/LocalTranslate-Tester.exe.")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent  # compiler/ -> local_translator/
    check_source(root)

    if args.only_term_engine:
        if args.with_tester:
            parser.error("--with-tester has no effect with --only-term-engine (no app build happens)")
        if not args.term_engine_dir or not args.out:
            parser.error("--only-term-engine requires both --term-engine-dir and --out")
        term_engine_dir = Path(args.term_engine_dir).resolve()
        if not term_engine_dir.is_dir():
            print(f"  [build] --term-engine-dir not found: {term_engine_dir}")
            raise SystemExit(1)
        out_path = Path(args.out).resolve()
        report = pack_terminology(term_engine_dir, out_path)
        if not report:
            print(f"  [build] No terminology data found under {term_engine_dir} — nothing packed.")
            raise SystemExit(1)
        total = sum(sum(counts.values()) for counts in report.values())
        print(f"  Packed -> {out_path} ({len(report)} mindset(s), {total} terms total)")
        return

    venv_python = ensure_build_venv(root)

    dist_dir = root / "dist" / manifest.APP_NAME
    build_exe(root, venv_python, dist_dir)
    copy_external_defaults(root, dist_dir)

    if args.with_tester:
        build_tester_exe(root, venv_python, dist_dir)

    if args.term_engine_dir:
        include_term_engine(Path(args.term_engine_dir).resolve(), dist_dir)
    else:
        print("\nTerminology engine not included (--term-engine-dir not given).")

    if not args.no_zip:
        build_zip(dist_dir)

    print(f"\n  Build finished: {dist_dir}")


if __name__ == "__main__":
    main()
