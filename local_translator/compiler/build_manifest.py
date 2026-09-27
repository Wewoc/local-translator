"""
compiler/build_manifest.py — single source of truth for the PyInstaller build

No logic, no imports beyond stdlib types — pure data. build.py reads this;
nothing else needs to duplicate these lists.
"""

APP_NAME = "LocalTranslate"

ENTRY_POINT = "app.py"

# Read-only assets bundled into the build via PyInstaller --add-data —
# (source, dest-inside-bundle), both relative to local_translator/.
# terminology/ is deliberately NOT here — that's the separate, optional
# terminology.data (see pack_terminology.py), never embedded.
DATA_FILES = [
    ("index.html", "."),
    ("static", "static"),
    ("pipeline/mindsets.json", "pipeline"),
]

# uvicorn/fastapi load several submodules dynamically that PyInstaller's
# static analysis doesn't see from a plain `import uvicorn` — without
# these, the built app either fails to start or fails on the first
# request with a ModuleNotFoundError.
HIDDEN_IMPORTS = [
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "dotenv",
    "yaml",
    "lara_sdk",
]

# User-editable files that must live NEXT TO the built EXE, copied fresh
# on every build (the build's own dist/ output is disposable — the actual
# hand-off unit is the release ZIP, not dist/ itself; nobody edits
# config.yaml inside dist/ directly). .env is never copied — personal API
# keys, never bundled into anything handed to someone else.
EXTERNAL_DEFAULTS = [
    "config.yaml",
]
