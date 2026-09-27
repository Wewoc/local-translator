"""
terminology/terminology.py — Runtime Terminology Engine

Structure:
  terminology/
    technical/de.json   {"§Txxxxxxxx§": "Betriebssystem"}
    technical/en.json   {"§Txxxxxxxx§": "Operating System"}
    legal/de.json + en.json
    ... (general, medical, editorial, academic, marketing, political)

New target language: drop a new fr.json into the mindset folder — done.

Portable alternative: a single terminology.data next to this file (or,
when frozen, next to the EXE) is tried first and takes priority over the
loose per-file tree above — see _find_pack_path(). Built by
Terminologie-Engine/pack_terminology.py, which packs that same
per-mindset/per-lang JSON tree into one file so it can be handed to
someone else, or dropped next to a built EXE, without touching the
folder structure at all. No pack file present: falls back to the loose
tree exactly as before.

API:
  engine = TermEngine()
  ok = engine.check(src_lang="DE", tgt_lang="EN", mindset="technical")
  protected, code_map = engine.protect(text, src_lang="DE", mindset="technical")
  result = engine.restore(translation, tgt_lang="EN", code_map=code_map)
  issues = engine.verify(protected, result, code_map)
  info   = engine.status(src_lang, tgt_lang, mindset)
"""

import gzip
import json
import re
import sys
from pathlib import Path

_TERMINOLOGY_DIR = Path(__file__).resolve().parent
_PACK_FILENAME   = "terminology.data"
_CODE_PATTERN    = re.compile(r"§T[0-9a-f]{8}§")

ALL_MINDSETS = ["general", "technical", "legal", "medical",
                "editorial", "academic", "marketing", "political"]


def _find_pack_path() -> Path | None:
    """
    Looks for a packed terminology.data — next to the EXE when frozen,
    otherwise next to this file. Checked once; None if neither exists.
    """
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / _PACK_FILENAME)
    candidates.append(_TERMINOLOGY_DIR / _PACK_FILENAME)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


class TermEngine:

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # Cache: (mindset, lang) -> {code: term}
            cls._instance._cache: dict[tuple, dict] = {}
            # Pack file, loaded once on first _load() call — None means
            # "checked, none found", not "not checked yet" (see _load()).
            cls._instance._pack = None
            cls._instance._pack_checked = False
        return cls._instance

    # ── Loading ───────────────────────────────────────────────────────────────

    def _load_pack(self) -> dict | None:
        """Loads terminology.data once. Returns {mindset: {lang: {code: term}}},
        or None if no pack file is present or it fails to load."""
        pack_path = _find_pack_path()
        if pack_path is None:
            return None
        try:
            with gzip.open(pack_path, "rt", encoding="utf-8") as f:
                payload = json.load(f)
            return payload.get("mindsets", {})
        except Exception as e:
            print(f"  [TermEngine] Pack load error {pack_path}: {e}")
            return None

    def _load(self, mindset: str, lang: str) -> dict:
        """Loads mindset/lang — only once, then cached. Returns an empty dict on error.
        Prefers terminology.data if present, else the loose per-file tree."""
        key = (mindset, lang)
        if key in self._cache:
            return self._cache[key]

        if not self._pack_checked:
            self._pack = self._load_pack()
            self._pack_checked = True

        if self._pack is not None:
            data = self._pack.get(mindset, {}).get(lang, {})
            self._cache[key] = data
            return data

        json_path = _TERMINOLOGY_DIR / mindset / f"{lang}.json"
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            self._cache[key] = data
            return data
        except FileNotFoundError:
            self._cache[key] = {}
            return {}
        except Exception as e:
            print(f"  [TermEngine] Load error {json_path}: {e}")
            self._cache[key] = {}
            return {}

    # ── check ─────────────────────────────────────────────────────────────────

    def check(self, src_lang: str, tgt_lang: str, mindset: str) -> bool:
        """
        Checks whether the source and target language are available for the mindset.
        Returns True if the engine is active, False if falling back (engine disabled).
        """
        src = self._load(mindset, src_lang.lower())
        tgt = self._load(mindset, tgt_lang.lower())
        return bool(src) and bool(tgt)

    # ── status ────────────────────────────────────────────────────────────────

    def status(self, src_lang: str, tgt_lang: str, mindset: str) -> dict:
        """
        For the /terminology/status endpoint and the UI indicator.
        Returns:
          {
            "active": bool,
            "src_available": bool,
            "tgt_available": bool,
            "mindset": str,
            "src_lang": str,
            "tgt_lang": str,
            "src_terms": int,
            "tgt_terms": int,
          }
        """
        sl = src_lang.lower()
        tl = tgt_lang.lower()
        src_data = self._load(mindset, sl)
        tgt_data = self._load(mindset, tl)
        src_ok = bool(src_data)
        tgt_ok = bool(tgt_data)
        return {
            "active":        src_ok and tgt_ok,
            "src_available": src_ok,
            "tgt_available": tgt_ok,
            "mindset":       mindset,
            "src_lang":      src_lang.upper(),
            "tgt_lang":      tgt_lang.upper(),
            "src_terms":     len(src_data),
            "tgt_terms":     len(tgt_data),
        }

    # ── protect ───────────────────────────────────────────────────────────────

    def protect(
        self,
        text: str,
        src_lang: str = "de",
        mindset: str  = "general",
    ) -> tuple[str, dict]:
        """
        Replaces source-language terms with codes.

        Returns:
            (protected_text, code_map)
            code_map: {"§Txxxxxxxx§": {"src": "Betriebssystem", "matched": "Betriebssystems"}}
        """
        sl = src_lang.lower()
        src_data = self._load(mindset, sl)
        if not src_data:
            return text, {}

        # Invert: term_lower -> code  (for regex matching)
        term_to_code: dict[str, str] = {
            term.lower(): code for code, term in src_data.items()
        }
        # Longest terms first -> no partial match
        sorted_terms = sorted(term_to_code.keys(), key=len, reverse=True)

        code_map: dict[str, dict] = {}
        result = text

        for term_lower in sorted_terms:
            code = term_to_code[term_lower]
            original_term = src_data[code]  # original spelling

            base = re.escape(original_term)
            # \b before the term prevents matches in the middle of words
            # (e.g. "REST" in "underestimated", "NAL" in "external")
            # Negative lookahead instead of \b after the term — inflection
            # endings (-e, -s, -es, -en, -n) are appended directly
            flexions = [base, base+r"e", base+r"s", base+r"es", base+r"en", base+r"n"]
            pattern = re.compile(
                r"\b(?:" + "|".join(flexions) + r")(?![a-zA-ZäöüÄÖÜ])",
                re.IGNORECASE,
            )

            def replacer(m, _code=code, _term=original_term):
                if _CODE_PATTERN.search(m.group(0)):
                    return m.group(0)
                code_map[_code] = {"src": _term, "matched": m.group(0)}
                return _code

            result = pattern.sub(replacer, result)

        return result, code_map

    # ── restore ───────────────────────────────────────────────────────────────

    def restore(
        self,
        text: str,
        tgt_lang: str = "en",
        code_map: dict = None,
        mindset: str   = "general",
    ) -> str:
        """
        Replaces codes with target-language terms.
        Looks up the target term in tgt_lang.json at runtime.
        """
        if not code_map:
            return text

        tl = tgt_lang.lower()
        tgt_data = self._load(mindset, tl)

        result = text
        for code in code_map:
            tgt_term = tgt_data.get(code)
            if tgt_term:
                result = result.replace(code, tgt_term)

        # Repair damaged codes (whitespace, dropped/swapped delimiters, ...)
        # so no §Txxxxxxxx§-shaped token is ever left dangling in the output.
        result = _repair(result, tgt_data, code_map)
        return result

    # ── verify ────────────────────────────────────────────────────────────────

    def verify(self, protected: str, restored: str, code_map: dict) -> list[str]:
        """Returns warning messages — empty if everything is ok.

        Only checks for a canonical §Txxxxxxxx§ code still sitting unresolved
        in `restored` — on a normal or repaired restore, the code is gone
        because it was replaced by real text, so "code no longer present"
        does not by itself mean the term was lost (a naive protected-vs-
        restored diff would flag every successful restoration as a false
        positive)."""
        issues = []
        for code in _CODE_PATTERN.findall(restored):
            if code in code_map:
                issues.append(f"Code not replaced: {code} (src: '{code_map[code]['src']}')")
        return issues

    # ── strip_unresolved ─────────────────────────────────────────────────────

    def strip_unresolved(self, text: str, code_map: dict = None) -> tuple[str, list[str]]:
        """
        Final safety net, run after restore(): removes any canonical
        §Txxxxxxxx§-shaped token still present — a known id restore()/
        _repair() couldn't resolve (e.g. no target-language entry and no
        code_map fallback), or one that was never issued for this call at
        all (the model hallucinating a well-formed-looking code) — so a raw
        internal placeholder never reaches the visible output.

        Returns (cleaned_text, warnings). Each warning names the exact code
        removed and, when known, the source term — the same text this gets
        logged as server-side, so the two can be matched up.
        """
        code_map = code_map or {}
        warnings: list[str] = []

        def _strip(m):
            code = m.group(0)
            info = code_map.get(code)
            if info:
                warnings.append(f"Code left unresolved and stripped: {code} (src: '{info['src']}')")
            else:
                warnings.append(f"Unknown/hallucinated term code stripped: {code}")
            return ""

        cleaned = _CODE_PATTERN.sub(_strip, text)
        if warnings:
            # collapse the double space a removed code typically leaves
            # behind between two surrounding words — never touches
            # newlines/paragraphs
            cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        return cleaned, warnings


# ── Helper Functions ────────────────────────────────────────────────────────────

def _repair(text: str, tgt_data: dict, code_map: dict | None = None) -> str:
    """
    Post-processing safety net: makes sure every term the engine protected
    actually ends up back in the output as real text, even if the LLM
    damaged the placeholder in ways restore()'s exact-match replace() can't
    catch. Observed damage patterns:

      - whitespace inside intact delimiters:      §  T1a2b3c4d  §
      - delimiters dropped or swapped for
        brackets/parens (model reads "§" as a
        legal section mark, e.g. in legal text
        that itself uses "§ 4 ..."):              [T1a2b3c4d]  (T1a2b3c4d)
      - delimiters dropped entirely:               T1a2b3c4d

    Only repairs codes actually issued for this call (from code_map) — never
    guesses at arbitrary T-shaped hex strings that might occur in real prose.
    """
    if not code_map:
        return text

    ids = {code[2:-1] for code in code_map if _CODE_PATTERN.fullmatch(code)}
    if not ids:
        return text

    id_alt = "|".join(sorted(ids))
    # Surrounding whitespace is only consumed together with an actual
    # delimiter char — otherwise a bare, undelimited code (no brackets at
    # all) would eat unrelated spacing from the words around it.
    loose = re.compile(
        r"(?:[§\[({<]{1,2}\s*)?T(" + id_alt + r")(?:\s*[§\])}>]{1,2})?",
        re.IGNORECASE,
    )

    def normalize(m):
        canonical = f"§T{m.group(1).lower()}§"
        return tgt_data.get(canonical, code_map.get(canonical, {}).get("src", m.group(0)))

    return loose.sub(normalize, text)


# ── Singleton ─────────────────────────────────────────────────────────────────

term_engine = TermEngine()
