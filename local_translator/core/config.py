"""
core/config.py — configuration, constants, runtime state

Contains:
  - load_config(), load_mindsets()
  - All constants from config.yaml and .env
  - RuntimeState with active_model
  - Path management (PROJECT_ROOT-based)

Imported by: all other modules.
Does not import anything else from this project.
"""

import json
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

# ── Paths ─────────────────────────────────────────────────────────────────────
#
# Two roots once this runs frozen (PyInstaller --onedir):
#
#   PROJECT_ROOT — sys.executable's folder. The stable install directory
#     (sibling of the EXE's _internal/ in --onedir). User-editable/
#     persistent files belong here: config.yaml, lara_usage.json,
#     exports/, logs/.
#
#   _BUNDLE_ROOT — sys._MEIPASS. Read-only assets added via PyInstaller's
#     --add-data land here: index.html, pipeline/mindsets.json.
#
# Dev (not frozen): both are PROJECT_ROOT, __file__-based as before.
# Never resolve paths via Path(__file__).parent in other modules instead
# of importing from here — breaks the moment this runs frozen.

if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
    _BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    _BUNDLE_ROOT = PROJECT_ROOT

CONFIG_PATH     = PROJECT_ROOT / "config.yaml"
MINDSETS_PATH   = _BUNDLE_ROOT / "pipeline" / "mindsets.json"
LARA_USAGE_FILE = PROJECT_ROOT / "lara_usage.json"
INDEX_PATH      = _BUNDLE_ROOT / "index.html"
STATIC_DIR      = _BUNDLE_ROOT / "static"

# ── Load config ───────────────────────────────────────────────────────────────

load_dotenv(PROJECT_ROOT / ".env")

def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_mindsets() -> dict:
    try:
        return json.loads(MINDSETS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

cfg      = load_config()
MINDSETS = load_mindsets()

# ── Constants ─────────────────────────────────────────────────────────────────

OLLAMA_URL   = cfg.get("ollama_url", "http://localhost:11434")
OLLAMA_MODEL = cfg.get("ollama_model", "mistral")

DEEPL_KEY  = os.getenv("DEEPL_API_KEY", "")
DEEPL_FREE = cfg.get("deepl_free_tier", True)

LIBRE_URL = cfg.get("libretranslate_url", "http://localhost:5000")
LIBRE_KEY = cfg.get("libretranslate_api_key", "")
LIBRE_ON  = cfg.get("libretranslate_enabled", False)

MYMEMORY_ON   = cfg.get("mymemory_enabled", True)
MYMEMORY_MAIL = cfg.get("mymemory_email", "")

LARA_ID          = os.getenv("LARA_ACCESS_KEY_ID", "")
LARA_SECRET      = os.getenv("LARA_ACCESS_KEY_SECRET", "")
LARA_ON          = cfg.get("lara_enabled", False)
LARA_DAILY_LIMIT = cfg.get("lara_daily_limit", 5000)

EXPORT_DIR   = PROJECT_ROOT / cfg.get("export_dir", "exports").lstrip("./")
FILENAME_PFX = cfg.get("filename_prefix", "translation")

LANGUAGES      = cfg.get("languages", {"Deutsch": "DE", "Englisch": "EN"})
DEFAULT_SRC    = cfg.get("default_source_lang", "DE")
DEFAULT_TGT    = cfg.get("default_target_lang", "EN")
DEBOUNCE_SEC   = cfg.get("debounce_seconds", 1.5)
DEFAULT_MODE   = cfg.get("default_mode", "debounce")
DEFAULT_MINDSET = cfg.get("default_mindset", "general")

OLLAMA_CHUNK_SIZE   = cfg.get("ollama_chunk_size", 6000)
DEEPL_CHUNK_SIZE    = 4900   # fixed — DeepL API limit
MYMEMORY_CHUNK_SIZE = 480    # fixed — MyMemory API limit

LOG_DIR = PROJECT_ROOT / cfg.get("log_dir", "logs").lstrip("./")
PERF_LOG = LOG_DIR / "perf.csv"
CSV_SEP  = cfg.get("log_csv_separator", ";")

HOST             = cfg.get("host", "127.0.0.1")
PORT             = cfg.get("port", 8000)
AUTO_OPEN_BROWSER = cfg.get("auto_open_browser", True)

# Unused — retained for config.yaml compatibility
S2_MODEL = cfg.get("pipeline_s2_model", "")
S3_MODEL = cfg.get("pipeline_s3_model", "")  # removed feature, kept for compat

MINDSET_MODEL = cfg.get("pipeline_mindset_model", "")   # empty = fallback to state.active_model

# ── Create directories ────────────────────────────────────────────────────────

EXPORT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Runtime State ─────────────────────────────────────────────────────────────

class RuntimeState:
    """Runtime variables — values that can change after startup."""
    active_model: str = OLLAMA_MODEL

state = RuntimeState()
