/**
 * pricing-cms.js — page-scoped UI polish for the Superadmin Pricing CMS
 * (/superadmin/settings/pricing). Purely cosmetic/UX; never touches form
 * submission, autosave, or CSRF handling — those stay owned by
 * settings-form.js and the server.
 *
 * 1. Mirrors the existing sticky-bar save-status pill (owned by
 *    settings-form.js) into a small badge next to the page title, so the
 *    "Draft Mode / Unsaved changes / All changes saved" state is visible
 *    without scrolling down. Read-only observer — never writes back.
 * 2. Drives the "Quick checklist" card in the helper panel from the
 *    current values of a few key fields already on the page.
 *
 * Both pieces are opt-in via data attributes / element presence, so this
 * is safe to load only on this one page.
 */
(function () {
  'use strict';

  function initHeaderStatusMirror() {
    const source = document.querySelector('[data-role="save-status"]');
    const badge = document.getElementById('pricingStatusBadge');
    const label = document.getElementById('pricingStatusBadgeLabel');
    if (!source || !badge || !label) return;

    function sync() {
      const kind = ['dirty', 'saving', 'saved', 'error'].find((k) =>
        source.classList.contains('is-' + k)
      );
      badge.classList.remove('is-dirty', 'is-saving', 'is-saved', 'is-error');
      if (kind) badge.classList.add('is-' + kind);

      if (kind === 'dirty' || kind === 'saving') {
        label.textContent = 'Unsaved changes';
      } else if (kind === 'saved') {
        label.textContent = 'All changes saved';
      } else if (kind === 'error') {
        label.textContent = 'Save failed';
      } else {
        label.textContent = 'Draft mode';
      }
    }

    sync();
    new MutationObserver(sync).observe(source, { attributes: true, attributeFilter: ['class'] });
  }

  function initChecklist() {
    const list = document.getElementById('pricingChecklist');
    if (!list) return;
    const items = Array.from(list.querySelectorAll('[data-check-field]'));
    if (!items.length) return;

    function fieldHasValue(name) {
      const el = document.querySelector('[name="' + name + '"]');
      if (!el) return false;
      return (el.value || '').trim().length > 0;
    }

    function anyFieldHasValue(names) {
      return names.some(fieldHasValue);
    }

    function refresh() {
      items.forEach((item) => {
        const names = item.getAttribute('data-check-field').split(',').map((s) => s.trim());
        const done = anyFieldHasValue(names);
        item.classList.toggle('is-done', done);
        const dot = item.querySelector('.check-dot');
        if (dot) dot.innerHTML = done ? '<iconify-icon icon="lucide:check" width="11"></iconify-icon>' : '';
      });
    }

    refresh();
    document.addEventListener('input', (e) => {
      if (e.target && e.target.name) refresh();
    });
    document.addEventListener('change', (e) => {
      if (e.target && e.target.name) refresh();
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    initHeaderStatusMirror();
    initChecklist();
  });
})();
