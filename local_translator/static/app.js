// ── app.js — global state, init, input setup ────────────────────────────────
//
// Contains: global state (6 variables), init(), setupInput()
//
// Reads global state: all
// Writes global state: config (initial fill), debounceTimer, mindsetDetected
//
// Calls: functions from ui.js, engines.js, translate.js
//
// Must be loaded as the last script — init() assumes all others are present.

// ── Global State ──────────────────────────────────────────────────────────────

let config = {};
let debounceTimer = null;
let currentTranslation = '';
let isTranslating = false;
let abortController = null;
let mindsetDetected = false;

// ── Init ─────────────────────────────────────────────────────────────────────

async function init() {
  config = await fetch('/config').then(r => r.json());

  document.getElementById('appVersion').textContent = config.version ? `v${config.version}` : '';

  // Fill language dropdowns
  ['srcLang', 'tgtLang'].forEach(id => {
    const sel = document.getElementById(id);
    Object.entries(config.languages).forEach(([name, code]) => {
      const opt = document.createElement('option');
      opt.value = code;
      opt.textContent = name;
      sel.appendChild(opt);
    });
  });
  document.getElementById('srcLang').value   = config.default_source_lang;
  document.getElementById('tgtLang').value   = config.default_target_lang;
  document.getElementById('modeSelect').value = config.default_mode || 'debounce';

  // Fill mindset dropdown
  const mindsetSel = document.getElementById('mindsetSelect');
  Object.entries(config.mindsets || {}).forEach(([key, label]) => {
    const opt = document.createElement('option');
    opt.value = key;
    opt.textContent = label;
    mindsetSel.appendChild(opt);
  });
  mindsetSel.value = config.default_mindset || 'general';

  // Final-pass buttons
  document.getElementById('deeplBtn').disabled = !config.deepl_available;

  if (config.libretranslate_available) checkLibre();
  else document.getElementById('libreBtn').disabled = true;

  const mmBtn = document.getElementById('mymemoryBtn');
  mmBtn.disabled = !config.mymemory_available;
  if (!config.mymemory_available) mmBtn.dataset.forceDisabled = '1';

  document.getElementById('laraBtn').disabled = !config.lara_available;
  if (config.lara_available) updateLaraUsage();

  // VRAM status
  updateVramStatus();
  setInterval(updateVramStatus, 10000);

  // Language dropdowns → LibreTranslate status + Coherence Mode UI
  document.getElementById('srcLang').addEventListener('change', () => {
    if (config.libretranslate_available) checkLibre();
    checkTerminology();
    updateCoherenceUI();
  });
  document.getElementById('tgtLang').addEventListener('change', () => {
    if (config.libretranslate_available) checkLibre();
    checkTerminology();
    updateCoherenceUI();
  });
  document.getElementById('mindsetSelect').addEventListener('change', () => {
    checkTerminology();
  });
  checkTerminology();
  updateCoherenceUI();

  // Ollama status
  checkOllama();
  setInterval(checkOllama, 30000);

  setupInput();
}

// ── Input Setup ──────────────────────────────────────────────────────────────

function setupInput() {
  const ta  = document.getElementById('srcText');
  const out = document.getElementById('tgtOutput');

  // Synchronized scrolling
  ta.addEventListener('scroll', () => {
    const ratio = ta.scrollTop / (ta.scrollHeight - ta.clientHeight || 1);
    out.scrollTop = ratio * (out.scrollHeight - out.clientHeight);
  });
  out.addEventListener('scroll', () => {
    const ratio = out.scrollTop / (out.scrollHeight - out.clientHeight || 1);
    ta.scrollTop = ratio * (ta.scrollHeight - ta.clientHeight);
  });

  // Translation in debounce mode. Mindset detection no longer happens here
  // (see translate.js) - it should only run once a translation actually
  // starts, regardless of which trigger (button/Enter/debounce),
  // not on every single keystroke regardless of the selected mode.
  ta.addEventListener('input', () => {
    updateCharCount('srcText', 'srcCount');

    const mode = document.getElementById('modeSelect').value;
    if (mode === 'debounce') {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        translateNow();
      }, (config.debounce_seconds || 1.5) * 1000);
    }
  });

  // Sentence mode
  ta.addEventListener('keydown', (e) => {
    const mode = document.getElementById('modeSelect').value;
    if (mode === 'sentence' && e.key === 'Enter' && !e.shiftKey) {
      setTimeout(translateNow, 50);
    }
  });
}

document.addEventListener('DOMContentLoaded', init);
