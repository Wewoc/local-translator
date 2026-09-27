"""
engines/ollama.py — Ollama engine

Contains:
  - translate_ollama()
  - run_coherence_pass()
  - run_s2()
  - detect_mindset()

Imports:
  - httpx, re, fastapi.HTTPException
  - core.config: OLLAMA_URL, MINDSETS, state
  - core.chunking: lang_name
  - core.logging: _write_perf_log
  - terminology.terminology: term_engine

Imported by: app.py
"""

import re

import httpx
from fastapi import HTTPException

from core.config import MINDSET_MODEL, MINDSETS, OLLAMA_URL, state
from core.chunking import lang_name
from core.logging import _write_perf_log
from terminology.terminology import term_engine

# ── S1 — Primary Translation ──────────────────────────────────────────────────

async def translate_ollama(
    text: str,
    source_lang: str,
    target_lang: str,
    context: str = "",
    mindset: str = "general",
) -> str:
    src = lang_name(source_lang)
    tgt = lang_name(target_lang)
    ms  = MINDSETS.get(mindset, MINDSETS.get("general", {}))

    anchor = ms.get("anchor", "")
    mam    = ms.get("mam", "")
    veto   = ms.get("veto", [])
    p0     = ms.get("p0", (
        "Output only the translation. No introductory sentences. "
        "No explanations. Do not repeat the source text. "
        "Do not comment on the translation."
    ))

    veto_hint = (
        f"Keep these terms untranslated: {', '.join(veto)}.\n"
        if veto else ""
    )
    context_hint = (
        f"For continuity, the previous passage ended with:\n{context}\n\n"
        if context else ""
    )
    placeholder_hint = (
        "The text may contain opaque placeholder tokens of the form "
        "§Lxxxxxxxx§ or §Txxxxxxxx§ (letters/digits between § marks). These "
        "are not legal section marks — copy them character-for-character "
        "exactly as given, including the § marks on both sides. Never "
        "translate, alter, split, merge, drop, or reformat them (e.g. do "
        "not turn §T1a2b3c4d§ into \"Section [T1a2b3c4d]\" or similar).\n"
    )
    prompt = (
        f"{anchor}\n\n"
        f"{mam}\n"
        f"{veto_hint}"
        f"{placeholder_hint}"
        f"{context_hint}"
        f"Translate the following text from {src} to {tgt}.\n\n"
        f"{text}\n\n"
        f"{p0}"
    )
    payload = {
        "model": state.active_model,
        "prompt": prompt,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=180.0)) as client:
        try:
            r = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
            r.raise_for_status()
            return r.json().get("response", "").strip()
        except httpx.ConnectError:
            raise HTTPException(
                status_code=503,
                detail="Ollama unreachable. Is 'ollama serve' running?",
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ollama error: {e}")

# ── Coherence Pass — Prompt B (source_lang == target_lang) ───────────────────

DEFAULT_COHERENCE_LEVEL = 2

# Eingriffstiefe-Regler: each level tightens/loosens how far the editor may
# depart from the input. "scope" is the only part of the prompt that varies
# per level — the base rules (never touch placeholders, never add/remove
# ideas, never touch Markdown/code) stay the same at every level. Values are
# initial estimates (see konzept_kohaerenz_pass.md) — not yet tuned against
# real test texts.
COHERENCE_LEVELS = {
    1: {
        "label": "Soft",
        "temperature": 0.15,
        "threshold": 0.75,
        "scope": (
            "Your task is strictly limited to: fixing connectors and transition "
            "words between sentences where the logical flow is unclear. Do not "
            "restructure sentences. Do not reorder anything."
        ),
    },
    2: {
        "label": "Standard",
        "temperature": 0.3,
        "threshold": 0.60,
        "scope": (
            "Your task is strictly limited to: smoothing abrupt transitions "
            "between sentences and paragraphs, fixing connectors where the "
            "logical flow is unclear. Do not restructure sentences beyond what "
            "is necessary for a smooth transition."
        ),
    },
    3: {
        "label": "Strong",
        "temperature": 0.45,
        "threshold": 0.45,
        "scope": (
            "Your task is strictly limited to: smoothing transitions between "
            "sentences and paragraphs, fixing connectors where the logical flow "
            "is unclear, and reordering sentences within a paragraph where it "
            "improves the flow."
        ),
    },
    4: {
        "label": "Rewrite Light",
        "temperature": 0.55,
        "threshold": 0.30,
        "scope": (
            "You may rephrase entire sentences and change word choice freely "
            "where it improves coherence, as long as every idea and fact from "
            "the input is preserved."
        ),
    },
    5: {
        "label": "Rewrite Medium",
        "temperature": 0.65,
        "threshold": 0.20,
        "scope": (
            "You may rephrase sentences freely, reorder content within a "
            "paragraph, and shorten redundant passages, as long as every idea "
            "and fact from the input is preserved."
        ),
    },
    6: {
        "label": "Rewrite Heavy",
        "temperature": 0.75,
        "threshold": 0.20,
        "scope": (
            "You may freely restructure and rewrite across paragraphs, using "
            "the input as source material rather than a fixed skeleton. Every "
            "idea and fact from the input must still appear in the output, and "
            "no new information may be added."
        ),
    },
}

async def run_coherence_pass(text: str, lang: str, level: int = DEFAULT_COHERENCE_LEVEL) -> str:
    """Monolingual editing pass: smooths transitions between sentences/paragraphs,
    or rewrites more freely at higher levels (see COHERENCE_LEVELS).
    Runs over state.active_model (S1 model selection) — no translation,
    no S2. Language-agnostic — the language name is inserted at runtime
    via lang_name(), no hardcoding to German."""
    lang_display = lang_name(lang)
    level_cfg = COHERENCE_LEVELS.get(level, COHERENCE_LEVELS[DEFAULT_COHERENCE_LEVEL])

    prompt = (
        f"You are an editor reviewing your own {lang_display} text for coherence "
        f"between sentences and paragraphs. {level_cfg['scope']} "
        "Do not change meaning, tone, or register. Do not add new information "
        "or remove existing content — every idea in the input must remain in "
        "the output. Do not expand abbreviations. Do not alter Markdown "
        "formatting, code blocks, or structural elements. If a passage already "
        "reads smoothly, leave it exactly as is. "
        "The text may contain opaque placeholder tokens of the form §Lxxxxxxxx§ or "
        "§Txxxxxxxx§ (letters/digits between § marks) — copy these character-for-"
        "character exactly as given. Never alter, split, merge, shorten, or "
        "'correct' them, even if they look like noise or a typo. "
        "Output only the corrected text. No explanations. No comments.\n\n"
        f"{text}"
    )
    payload = {
        "model": state.active_model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": level_cfg["temperature"]},
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=180.0)) as client:
        try:
            r = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
            r.raise_for_status()
            result = r.json().get("response", "").strip()
            return result if result else text
        except httpx.ConnectError:
            raise HTTPException(
                status_code=503,
                detail="Ollama unreachable. Is 'ollama serve' running?",
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ollama error: {e}")

# ── S2 — Quality/Terminology Pass ─────────────────────────────────────────────

def _strip_code_blocks(text: str) -> str:
    return re.sub(r'```.*?```', '', text, flags=re.DOTALL)

async def run_s2(text: str, s2_model: str, mindset: str = "general") -> str:
    """Edits, does not translate. Silently falls back to S1 on error or drift."""
    ms    = MINDSETS.get(mindset, MINDSETS.get("general", {}))
    anchor = ms.get("anchor", "")
    veto   = ms.get("veto", [])
    veto_hint = f"Keep these terms untranslated: {', '.join(veto)}.\n" if veto else ""

    prompt = (
        f"{anchor}\n\n"
        "You are a post-editor reviewing a machine translation. "
        "Your scope is strictly limited to: fixing mistranslations, improving awkward phrasing, "
        "and correcting terminology against the domain anchor above. "
        "Do not change register, tone, or style beyond what is strictly necessary. "
        "Do not restructure sentences or paragraphs. "
        "Do not expand abbreviations or version numbers (e.g. V0.1 stays V0.1, GUI stays GUI, EXE stays EXE). "
        "Do not add, remove, or alter any Markdown formatting, italics, code blocks, "
        "or structural elements not already present in the input. "
        "Do not wrap the entire output in a code block or markdown fence. "
        "Do not translate — the text is already in the target language. "
        "If a passage is already correct, leave it exactly as is. "
        "Never delete sentences or paragraphs — every part of the input must appear in the output.\n"
        f"{veto_hint}"
        "Output only the corrected text. No explanations. No comments.\n"
        "IMPORTANT: Do not translate. The text is already in the target language. "
        "Output it in the same language as the input.\n\n"
        f"{text}"
    )
    payload = {"model": s2_model, "prompt": prompt, "stream": False}

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=180.0)) as client:
        try:
            # Unload S1 from VRAM before S2 starts
            await client.post(f"{OLLAMA_URL}/api/generate", json={
                "model": state.active_model, "keep_alive": 0,
                "prompt": "", "stream": False,
            })
            r = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
            r.raise_for_status()
            result = r.json().get("response", "").strip()
            if not result:
                return text

            # Drift check: S2 translated back if the non-ASCII ratio rose sharply
            input_non_ascii  = sum(1 for c in _strip_code_blocks(text)   if ord(c) > 127) / max(len(_strip_code_blocks(text)),   1)
            output_non_ascii = sum(1 for c in _strip_code_blocks(result) if ord(c) > 127) / max(len(_strip_code_blocks(result)), 1)
            if output_non_ascii > input_non_ascii + 0.15:
                return text  # Fallback to S1

            return result
        except Exception:
            return text  # S1 result as fallback

# ── Mindset Detection ─────────────────────────────────────────────────────────

async def detect_mindset(text: str, model: str = "") -> str:
    """Classifies text via an Ollama call. Returns the mindset key, falls back to 'general'.

    Model priority: explicitly passed (frontend dropdown) → config.yaml
    pipeline_mindset_model → state.active_model (S1, the old default approach).
    """
    if not text:
        return "general"

    detect_model = model or MINDSET_MODEL or state.active_model

    length = len(text)
    if length < 6000:
        positions, snippet_len = [0], 500
    elif length < 12000:
        positions, snippet_len = [0, length // 2], 400
    elif length < 60000:
        positions, snippet_len = [0, length // 3, (length // 3) * 2], 300
    elif length < 100000:
        positions, snippet_len = [0, length // 4, length // 2, (length // 4) * 3], 250
    else:
        positions, snippet_len = [
            0, length // 5, (length // 5) * 2,
            (length // 5) * 3, (length // 5) * 4
        ], 200

    snippets = [text[pos:pos + snippet_len].strip() for pos in positions]
    valid_mindsets = list(MINDSETS.keys())
    excerpts = "\n\n---\n\n".join(
        f"Excerpt {i+1}:\n{s}" for i, s in enumerate(snippets)
    )
    prompt = (
        f"Classify the following text excerpts into exactly one of these categories: "
        f"{', '.join(valid_mindsets)}.\n"
        "Focus on the overall register and purpose of the text, not just vocabulary. "
        "A text about technical topics written in an academic, reflective, or analytical "
        "style is 'academic', not 'technical'. "
        "Use 'technical' only for documentation, specifications, manuals, API references, or code. "
        "Reply with only the category name, nothing else.\n\n"
        f"{excerpts}"
    )
    payload = {
        "model": detect_model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0)) as client:
        try:
            r = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
            r.raise_for_status()
            detected = r.json().get("response", "").strip().lower()
            return detected if detected in valid_mindsets else "general"
        except Exception:
            return "general"

# ── Perf-Log Wrapper ──────────────────────────────────────────────────────────

def write_chunk_perf_log(
    chunk_index: int,
    chunk_size: int,
    time_s1: float,
    time_s2: float,
    s2_model: str,
    terms_protected: int = 0,
) -> None:
    """Passes state.active_model explicitly to core.logging — no state import there."""
    try:
        _write_perf_log(
            chunk_index=chunk_index,
            chunk_size=chunk_size,
            time_s1=time_s1,
            time_s2=time_s2,
            model_s1=state.active_model,
            model_s2=s2_model,
            terms_protected=terms_protected,
        )
    except Exception:
        pass
