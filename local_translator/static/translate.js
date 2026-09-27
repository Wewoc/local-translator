// ── translate.js — translation logic ────────────────────────────────────────
//
// Contains: translate, translateNow, stopTranslation, handleTranslateBtn,
//          deeplFinal, libreFinal, mymemoryFinal, laraFinal
//
// Reads global state: config, isTranslating, abortController
// Writes global state: isTranslating, abortController,
//                          currentTranslation, mindsetDetected
//
// Calls: updateLaraUsage, updateVramStatus (engines.js)
//           showToast, renderDiffHTML (ui.js)
//
// Coherence Mode (source_lang === target_lang): no longer blocked — the
// backend then runs Prompt B instead of translation (see app.py, engines/
// ollama.py). The response may additionally contain "diff" + "similarity"
// (core/diff_utils.py) — rendered instead of plain text, with a warning
// at low similarity (see COHERENCE_WARNING_THRESHOLD below).
//
// HTML onclick dependencies (must be globally available):
//   handleTranslateBtn, deeplFinal, libreFinal, mymemoryFinal, laraFinal

const CHUNK_LIMITS = { ollama: 6000, deepl: 4900, mymemory: 480 };
const COHERENCE_WARNING_THRESHOLD = 0.6;   // difflib ratio — below this: visible warning

async function translate(engine = 'ollama') {
  const text = document.getElementById('srcText').value.trim();
  if (!text || isTranslating) return;

  const src = document.getElementById('srcLang').value;
  const tgt = document.getElementById('tgtLang').value;
  // Coherence Mode: src === tgt is allowed (Prompt B in the backend then
  // takes over as a monolingual editing pass instead of translation) — no
  // longer blocked.
  const coherenceMode = src === tgt;

  // Mindset detection — first step as soon as a translation actually
  // starts (regardless of whether triggered by button, Enter, or debounce
  // timeout). Runs only once per translation run (mindsetDetected flag),
  // reset happens in the finally block below once it's done.
  if (!mindsetDetected) {
    try {
      const mindsetModel = document.getElementById('mindsetModelSelect')?.value || '';
      const res  = await fetch('/mindset/detect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, mindset_model: mindsetModel })
      });
      const data = await res.json();
      const sel  = document.getElementById('mindsetSelect');
      if (data.mindset && sel) {
        sel.value = data.mindset;
        const aiLabel = document.getElementById('mindsetAiLabel');
        if (aiLabel) aiLabel.textContent = `AI: ${data.mindset}`;
      }
    } catch {
      // Mindset detection is a "nice-to-have" - if it fails, the
      // translation still proceeds with the currently selected mindset.
    }
    mindsetDetected = true;
  }

  const limit        = CHUNK_LIMITS[engine];
  const needsChunking = limit && text.length > limit;

  isTranslating    = true;
  abortController  = new AbortController();
  const results    = [];
  let minSimilarity = null;
  let warnThreshold = COHERENCE_WARNING_THRESHOLD;
  const coherenceLevel = parseInt(document.getElementById('coherenceLevelSelect')?.value || '2', 10);
  const out        = document.getElementById('tgtOutput');
  out.textContent  = 'Translating …';
  out.className    = 'output-area loading';
  const warnEl = document.getElementById('coherenceWarning');
  if (warnEl) warnEl.style.display = 'none';

  const translateBtn       = document.getElementById('translateBtn');
  translateBtn.textContent = '■ Stop';
  translateBtn.onclick     = stopTranslation;
  document.getElementById('mindsetSelect').disabled = true;

  try {
    if (needsChunking) {
      // ── Prepare chunks ───────────────────────────────────────────────────
      const prepRes  = await fetch('/translate/chunks/prepare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, engine })
      });
      const prepData = await prepRes.json();
      if (!prepRes.ok) throw new Error(prepData.detail || 'Error');

      const { chunks, total } = prepData;
      let context = '';

      // Unload models before starting
      await fetch('/ollama/unload', { method: 'POST' }).catch(() => {});

      // Build the chunk table
      out.className = 'output-area';
      out.innerHTML = '';
      const table   = document.createElement('table');
      table.className = 'chunk-table';
      chunks.forEach((chunk, i) => {
        const tr     = document.createElement('tr');
        const tdRight = document.createElement('td');
        tdRight.className = 'chunk-right';
        tdRight.id        = `chunk-right-${i}`;
        tdRight.innerHTML = '<span class="chunk-placeholder">…</span>';
        tr.appendChild(tdRight);
        table.appendChild(tr);
      });
      out.appendChild(table);

      // ── Chunk loop ────────────────────────────────────────────────────────
      for (let i = 0; i < chunks.length; i++) {
        const s2sel = document.getElementById('s2ModelSelect').value;
        document.getElementById('engineBadge').innerHTML =
          `translating… (${i + 1} / ${total}) <span class="badge-pulse"></span>`;

        const res = await fetch('/translate/chunk', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal: abortController.signal,
          body: JSON.stringify({
            text:        chunks[i],
            source_lang: src,
            target_lang: tgt,
            engine,
            context,
            mindset:     document.getElementById('mindsetSelect').value,
            s2_model:    s2sel,
            chunk_index: i,
            coherence_level: coherenceLevel,
          })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Error');

        results.push(data.translation);
        // last paragraph as context for the next chunk
        const paras = data.translation.split('\n\n');
        context = paras[paras.length - 1].slice(-300);

        const cell = document.getElementById(`chunk-right-${i}`);
        if (cell) {
          if (data.diff) {
            cell.innerHTML = renderDiffHTML(data.diff);
            if (typeof data.coherence_threshold === 'number') warnThreshold = data.coherence_threshold;
            if (typeof data.similarity === 'number') {
              minSimilarity = minSimilarity === null
                ? data.similarity
                : Math.min(minSimilarity, data.similarity);
            }
          } else {
            cell.textContent = data.translation;
          }
        }
        document.getElementById('engineBadge').innerHTML =
          `translating… (${i + 1} / ${total} ✓) <span class="badge-pulse"></span>`;
      }

      currentTranslation = results.join('\n\n');

    } else {
      // ── Normal path — no chunking ───────────────────────────────────────
      await fetch('/ollama/unload', { method: 'POST' }).catch(() => {});
      const res = await fetch('/translate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, source_lang: src, target_lang: tgt, engine,
                             s2_model: document.getElementById('s2ModelSelect').value,
                             coherence_level: coherenceLevel })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Error');
      currentTranslation = data.translation;

      // The chunk table must not be overwritten — this branch only runs
      // when needsChunking was false from the start (see MAINTENANCE).
      if (data.diff) {
        out.innerHTML = renderDiffHTML(data.diff);
        out.className = 'output-area';
        if (typeof data.coherence_threshold === 'number') warnThreshold = data.coherence_threshold;
        if (typeof data.similarity === 'number') minSimilarity = data.similarity;
      } else {
        out.textContent = currentTranslation;
        out.className   = 'output-area';
      }
    }

    if (warnEl && coherenceMode && minSimilarity !== null && minSimilarity < warnThreshold) {
      warnEl.textContent =
        `⚠ Noticeably large deviation from the original detected (similarity ${(minSimilarity * 100).toFixed(0)}%) — please review carefully.`;
      warnEl.style.display = '';
    }

    document.getElementById('tgtCount').textContent =
      `${currentTranslation.length.toLocaleString('en')} chars`;
    if (engine === 'lara') updateLaraUsage();
    document.getElementById('engineBadge').textContent = coherenceMode ? 'coherence pass' : `via ${engine}`;

  } catch (err) {
    if (err.name === 'AbortError') {
      out.textContent = results.length
        ? results.join('\n\n') + '\n\n[aborted]'
        : '[aborted]';
      out.className = 'output-area';
    } else {
      out.textContent = `⚠ ${err.message}`;
      out.className   = 'output-area';
      showToast(err.message, 'err');
    }
  } finally {
    isTranslating   = false;
    abortController = null;
    mindsetDetected = false;

    const btn       = document.getElementById('translateBtn');
    btn.textContent = '▶ Translate';
    btn.onclick     = translateNow;

    const mindsetSel     = document.getElementById('mindsetSelect');
    mindsetSel.disabled  = false;
    mindsetSel.value     = config.default_mindset || 'general';
    // The AI label stays in place — only cleared by clearAll() or overwritten
    // by the next successful detection (translate(), start).

    if (engine === 'ollama') {
      fetch('/ollama/unload', { method: 'POST' })
        .then(() => updateVramStatus())
        .catch(() => {});
    }
  }
}

function translateNow()  { translate('ollama'); }
function deeplFinal()    { translate('deepl'); }
function libreFinal()    { translate('libretranslate'); }
function mymemoryFinal() { translate('mymemory'); }
function laraFinal()     { translate('lara'); }

function stopTranslation() {
  if (abortController) abortController.abort();
}

function handleTranslateBtn() {
  if (isTranslating) stopTranslation();
  else translateNow();
}
