/**
 * ╔══════════════════════════════════════════════════════════╗
 * ║  GLOBAL THEME ENGINE  ·  v1.0                           ║
 * ║  One source of truth for dark/light mode across all     ║
 * ║  admin, superadmin, and auth pages.                     ║
 * ╚══════════════════════════════════════════════════════════╝
 *
 * DESIGN:
 *  · Single localStorage key  →  'cms_theme'  ('dark' | 'light')
 *  · Pre-hydration snippet in <head> prevents FOCT
 *  · Dispatches 'themechange' custom event on every switch
 *  · Exposes window.ThemeEngine for external consumption
 */

(function (root) {
  'use strict';

  const STORAGE_KEY   = 'cms_theme';
  const DEFAULT_THEME = 'dark';
  const ATTR          = 'data-theme';
  const TRANSITION_MS = 320;
  const EASE          = 'cubic-bezier(0.4, 0, 0.2, 1)';

  /* ── Resolve current theme ─────────────────────────────── */
  function resolve() {
    return localStorage.getItem(STORAGE_KEY) || DEFAULT_THEME;
  }

  /* ── Apply theme to <html> without flash ──────────────── */
  function applyTheme(theme, animate) {
    const html = document.documentElement;

    if (animate) {
      html.classList.add('theme-transitioning');
    }

    html.setAttribute(ATTR, theme);
    localStorage.setItem(STORAGE_KEY, theme);

    // Sync legacy keys so old code still works
    localStorage.setItem('adminTheme',      theme);
    localStorage.setItem('superadmin-theme', theme);

    // Update all registered toggles
    _syncToggles(theme);

    // Fire custom event
    root.dispatchEvent(new CustomEvent('themechange', { detail: { theme } }));

    if (animate) {
      setTimeout(() => html.classList.remove('theme-transitioning'), TRANSITION_MS + 40);
    }
  }

  /* ── Toggle between dark ↔ light ──────────────────────── */
  function toggle() {
    const current = document.documentElement.getAttribute(ATTR) || DEFAULT_THEME;
    applyTheme(current === 'dark' ? 'light' : 'dark', true);
  }

  /* ── Sync all theme-switch buttons on the page ────────── */
  function _syncToggles(theme) {
    document.querySelectorAll('[data-theme-toggle]').forEach(btn => {
      // Sun / moon icon swap
      btn.querySelectorAll('[data-icon-dark]').forEach(el => {
        el.style.display = theme === 'dark' ? '' : 'none';
      });
      btn.querySelectorAll('[data-icon-light]').forEach(el => {
        el.style.display = theme === 'light' ? '' : 'none';
      });
      // Legacy class-based icons (moon-icon / sun-icon / theme-icon-moon / theme-icon-sun)
      btn.querySelectorAll('.moon-icon, .theme-icon-moon').forEach(el => {
        el.style.display = theme === 'dark' ? '' : 'none';
      });
      btn.querySelectorAll('.sun-icon, .theme-icon-sun').forEach(el => {
        el.style.display = theme === 'light' ? '' : 'none';
      });
      btn.setAttribute('aria-label', theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
    });

    // Legacy #themeSwitch without data-theme-toggle
    document.querySelectorAll('#themeSwitch:not([data-theme-toggle])').forEach(btn => {
      btn.querySelectorAll('.moon-icon, .theme-icon-moon').forEach(el => el.style.display = theme === 'dark' ? '' : 'none');
      btn.querySelectorAll('.sun-icon, .theme-icon-sun').forEach(el => el.style.display = theme === 'light' ? '' : 'none');
    });
  }

  /* ── Wire up all toggle buttons ───────────────────────── */
  function _bindToggles() {
    // New data-theme-toggle attribute
    document.querySelectorAll('[data-theme-toggle]').forEach(btn => {
      btn.addEventListener('click', toggle);
    });
    // Legacy #themeSwitch buttons
    document.querySelectorAll('#themeSwitch').forEach(btn => {
      if (!btn.dataset.themeToggleBound) {
        btn.dataset.themeToggleBound = '1';
        btn.addEventListener('click', toggle);
      }
    });
  }

  /* ── Init ──────────────────────────────────────────────── */
  function init() {
    const theme = resolve();
    applyTheme(theme, false);   // no animation on first load

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => {
        _bindToggles();
        _syncToggles(theme);
      });
    } else {
      _bindToggles();
      _syncToggles(theme);
    }
  }

  /* ── Public API ────────────────────────────────────────── */
  root.ThemeEngine = { resolve, applyTheme, toggle, init };

  // Auto-init
  init();

})(window);
