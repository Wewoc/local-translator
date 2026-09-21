// ── theme.js — GLA design switcher ──────────────────────────────────────────
//
// Contains: setTheme(), initTheme()
//
// Reads global state: none
// Writes global state: none (only html[data-theme] + localStorage)
//
// Independent of ui.js/engines.js/translate.js/app.js — no shared state, can
// load in any order relative to them. The theme is already applied before
// this file runs (see the inline script in index.html's <head>, which reads
// localStorage synchronously to avoid a flash of the wrong theme on load);
// this file only populates the dropdown and handles the user changing it.

const THEME_STORAGE_KEY = 'glaTheme';

function setTheme(id) {
  document.documentElement.dataset.theme = id;
  try { localStorage.setItem(THEME_STORAGE_KEY, id); } catch (e) {}
}

async function initTheme() {
  const themes = await fetch('/theme').then(r => r.json());
  const sel = document.getElementById('themeSelect');
  if (!sel) return;
  Object.entries(themes).forEach(([id, theme]) => {
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = theme.name;
    sel.appendChild(opt);
  });
  sel.value = document.documentElement.dataset.theme || '2';
}

document.addEventListener('DOMContentLoaded', initTheme);
