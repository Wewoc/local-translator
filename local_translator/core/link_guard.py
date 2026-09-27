"""
link_guard.py

Protects URLs, file paths, and markdown links from being mangled by the
translation pipeline (S1/S2). Uses the same placeholder-protect/restore
pattern as TermEngine, but with an independent namespace (§Lxxxxxxxx§)
to avoid collisions with §Txxxxxxxx§.

Pipeline position (mindset-independent, sits OUTSIDE TermEngine):

    raw_text
        -> link_guard.protect()
        -> TermEngine.protect()
        -> S1 (translate)
        -> TermEngine.restore()
        -> link_guard.restore()
        -> output

Rationale: link text (if plain prose, e.g. "Firmenwebseite") should still
be translatable, so only the URL/path itself is wrapped, not the whole
markdown link construct (except where the anchor-text heuristic decides
the link text itself is non-translatable, e.g. a filename).

Detection order matters — earlier rules consume matches before later,
broader rules could misfire on them:

    1. Markdown links   [text](url)
    2. Bare URLs        https://...
    3. Windows paths    C:\\... 
    4. UNC paths        \\\\server\\share...
    5. Backtick codespans containing a path separator, e.g. `src/docs/x.md`

Anything else (bare prose paths without backticks, e.g. "liegt unter
src/docs/") is intentionally left untouched — see project notes on the
open grey-zone decision (possible future LLM batch-call candidate).
"""

from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass, field


PLACEHOLDER_PREFIX = "§L"
PLACEHOLDER_SUFFIX = "§"
PLACEHOLDER_ID_LEN = 8
PLACEHOLDER_ALPHABET = string.digits  # keep numeric, mirrors TermEngine §Txxxxxxxx§ style

FILE_EXTENSIONS = {
    ".md", ".pdf", ".docx", ".json", ".txt", ".csv",
    ".yaml", ".yml", ".py", ".xlsx", ".zip",
}

# --- Regex patterns -----------------------------------------------------

# [text](url) — capture text and url separately so we can decide per-part
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")

# bare URLs (http/https), stops at whitespace or a closing paren/bracket
_BARE_URL_RE = re.compile(r"https?://[^\s)\]}>]+")

# Windows drive paths: C:\... (stops at whitespace)
_WIN_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s]+")

# UNC paths: \\server\share\...
_UNC_PATH_RE = re.compile(r"\\\\[^\s]+")

# backtick codespans, evaluated for path-likeness after matching
_CODESPAN_RE = re.compile(r"`([^`]+)`")

# any §L...§-shaped token left in restored text — catches both an exact
# placeholder that somehow wasn't replaced and a model-mangled variant
# (e.g. a digit added/dropped) that restore()'s exact-match replace() can't
# catch either way
_PLACEHOLDER_SHAPE_RE = re.compile(
    re.escape(PLACEHOLDER_PREFIX) + r"[^\s§]{1,20}" + re.escape(PLACEHOLDER_SUFFIX)
)


@dataclass
class LinkGuardResult:
    """Result of protect(): the placeholder-substituted text plus the
    mapping needed to restore the originals."""
    protected_text: str
    mapping: dict[str, str] = field(default_factory=dict)


def _new_placeholder(used_ids: set[str]) -> str:
    while True:
        pid = "".join(secrets.choice(PLACEHOLDER_ALPHABET) for _ in range(PLACEHOLDER_ID_LEN))
        if pid not in used_ids:
            used_ids.add(pid)
            return f"{PLACEHOLDER_PREFIX}{pid}{PLACEHOLDER_SUFFIX}"


def _should_protect_linktext(text: str) -> bool:
    """Anchor-text heuristic for markdown links: True = treat as
    non-translatable (e.g. filename), False = leave translatable prose."""
    stripped = text.strip()
    if any(stripped.lower().endswith(ext) for ext in FILE_EXTENSIONS):
        return True
    if " " in stripped:
        return False  # looks like prose ("Firmenwebseite", "Company Website")
    return True  # grey zone (e.g. "Docs", "Link") — default to protect


def _is_pathlike(codespan_content: str) -> bool:
    return "/" in codespan_content or "\\" in codespan_content


def protect(text: str) -> LinkGuardResult:
    """Replace URLs/paths/links in `text` with placeholders.
    Returns the substituted text plus a mapping for restore()."""
    mapping: dict[str, str] = {}
    used_ids: set[str] = set()
    result = text

    # 1. Markdown links
    def _md_link_sub(m: re.Match) -> str:
        link_text, url = m.group(1), m.group(2)
        url_ph = _new_placeholder(used_ids)
        mapping[url_ph] = url
        if _should_protect_linktext(link_text):
            text_ph = _new_placeholder(used_ids)
            mapping[text_ph] = link_text
            return f"[{text_ph}]({url_ph})"
        return f"[{link_text}]({url_ph})"

    result = _MD_LINK_RE.sub(_md_link_sub, result)

    # 2. Bare URLs (that weren't already consumed by rule 1)
    def _bare_url_sub(m: re.Match) -> str:
        ph = _new_placeholder(used_ids)
        mapping[ph] = m.group(0)
        return ph

    result = _BARE_URL_RE.sub(_bare_url_sub, result)

    # 3. Windows paths
    def _win_path_sub(m: re.Match) -> str:
        ph = _new_placeholder(used_ids)
        mapping[ph] = m.group(0)
        return ph

    result = _WIN_PATH_RE.sub(_win_path_sub, result)

    # 4. UNC paths
    def _unc_path_sub(m: re.Match) -> str:
        ph = _new_placeholder(used_ids)
        mapping[ph] = m.group(0)
        return ph

    result = _UNC_PATH_RE.sub(_unc_path_sub, result)

    # 5. Backtick codespans containing a path separator
    def _codespan_sub(m: re.Match) -> str:
        content = m.group(1)
        if not _is_pathlike(content):
            return m.group(0)  # leave non-path codespans untouched
        ph = _new_placeholder(used_ids)
        mapping[ph] = m.group(0)  # store WITH backticks, restore verbatim
        return ph

    result = _CODESPAN_RE.sub(_codespan_sub, result)

    return LinkGuardResult(protected_text=result, mapping=mapping)


def _repair(text: str, mapping: dict[str, str]) -> str:
    """
    Recovers placeholders damaged by the LLM — whitespace inside intact
    delimiters (§ L12345678 §), or delimiters dropped or swapped for
    brackets/parens ([L12345678], (L12345678)) — the same class of damage
    TermEngine._repair() handles for §Txxxxxxxx§. Only repairs ids actually
    issued for this call (from `mapping`); never guesses at an id that
    wasn't.
    """
    ids = set()
    prefix_re = re.compile(re.escape(PLACEHOLDER_PREFIX) + r"(\d+)" + re.escape(PLACEHOLDER_SUFFIX))
    for placeholder in mapping:
        m = prefix_re.fullmatch(placeholder)
        if m:
            ids.add(m.group(1))
    if not ids:
        return text

    id_alt = "|".join(sorted(ids))
    loose = re.compile(
        r"(?:[§\[({<]{1,2}\s*)?L(" + id_alt + r")(?:\s*[§\])}>]{1,2})?"
    )

    def normalize(m):
        canonical = f"{PLACEHOLDER_PREFIX}{m.group(1)}{PLACEHOLDER_SUFFIX}"
        return mapping.get(canonical, m.group(0))

    return loose.sub(normalize, text)


def restore(text: str, mapping: dict[str, str]) -> str:
    """Reinsert originals for every placeholder in `mapping`."""
    result = text
    for placeholder, original in mapping.items():
        result = result.replace(placeholder, original)
    result = _repair(result, mapping)
    return result


def strip_unresolved(text: str, mapping: dict[str, str]) -> tuple[str, list[str]]:
    """
    Final safety net, run after restore(): removes any §L...§-shaped token
    still present — a known id restore()/_repair() couldn't match, or one
    that was never issued for this call at all (the model hallucinating a
    well-formed-looking placeholder) — so a raw internal token never
    reaches the visible output.

    Returns (cleaned_text, warnings). Each warning names the exact token
    removed and, when known, the original it should have carried — the
    same text this gets logged as server-side, so the two can be matched
    up.
    """
    warnings: list[str] = []

    def _strip(m):
        token = m.group(0)
        original = mapping.get(token)
        if original:
            warnings.append(f"Link/path placeholder left unresolved and stripped: {token} (was: '{original}')")
        else:
            warnings.append(f"Unknown/hallucinated link placeholder stripped: {token}")
        return ""

    cleaned = _PLACEHOLDER_SHAPE_RE.sub(_strip, text)
    if warnings:
        # collapse the double space a removed token typically leaves behind
        # between two surrounding words — never touches newlines/paragraphs
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned, warnings


def verify(restored: str, mapping: dict[str, str]) -> list[str]:
    """Returns warning messages — empty if everything is ok.

    restore() only replaces exact matches from `mapping`. A token slightly
    altered by the model (e.g. an added/missing digit, as observed with the
    Coherence Pass) then no longer matches and otherwise stays in the output
    unnoticed — this scan makes that visible without repairing it (no silent
    fallback, per project convention)."""
    issues = []
    for match in _PLACEHOLDER_SHAPE_RE.findall(restored):
        original = mapping.get(match)
        if original:
            issues.append(f"Code not replaced: {match} -> '{original}'")
        else:
            issues.append(f"Altered/unknown placeholder left hanging in output: {match}")
    return issues


if __name__ == "__main__":
    # quick manual smoke test — not a substitute for real pipeline testing
    sample = (
        "Snapshot from repo documentation.\n"
        "[CHANGELOG.md](https://github.com/Wewoc/Garmin_Local_Archive/blob/main/src/docs/CHANGELOG.md) · "
        "[Firmenwebseite](https://example.com)\n"
        "See also `src/docs/REFERENCE_GARMIN.md` and `ppr/core/` for details.\n"
        "Path on disk: C:\\Users\\Timo\\Documents\\notes.txt\n"
        "and/or should NOT be protected here."
    )

    guarded = protect(sample)
    print("--- protected ---")
    print(guarded.protected_text)
    print("--- restored ---")
    print(restore(guarded.protected_text, guarded.mapping))
