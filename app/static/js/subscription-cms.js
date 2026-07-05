/**
 * subscription-cms.js — page-scoped UI polish for Subscription Settings
 * (/superadmin/subscriptions). Purely cosmetic; this page has no autosave
 * endpoint, so this only tracks whether the plans form has unsaved edits
 * and reflects that in the sticky action bar note. Form submission, CSRF,
 * and the update_plans/reset_subscription actions are untouched — this
 * never intercepts submit.
 */
(function () {
  'use strict';

  function init() {
    const form = document.getElementById('subscriptionPlansForm');
    const note = document.getElementById('billingActionBarNote');
    if (!form || !note) return;

    const defaultText = note.textContent;

    function markDirty() {
      note.textContent = 'Unsaved changes — click Save to apply them to tenants.';
      note.classList.add('is-dirty');
    }

    form.addEventListener('input', markDirty, { once: true });
    form.addEventListener('change', markDirty, { once: true });
    form.addEventListener('submit', () => {
      note.textContent = defaultText;
      note.classList.remove('is-dirty');
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();
