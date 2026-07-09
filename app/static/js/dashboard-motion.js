/* ═══════════════════════════════════════════════════════════════
   DASHBOARD MOTION — v6.2
   Animated count-up for .stat-big numbers on the admin and
   superadmin dashboards.

   Scope, deliberately narrow:
   - Card stagger, fade-in, and hover elevation are pure CSS
     (see "DASHBOARD MOTION LAYER" block in admin.css). This file
     exists only for the one effect that genuinely needs JS: ticking
     a number up from 0 on page load.
   - Only touches elements whose ENTIRE text content is a plain
     integer or an integer followed by "%". Currency values, dates,
     and anything pre-formatted by Jinja (e.g. "₱1,204.50") is left
     untouched on purpose — animating those would either misread
     the locale formatting or just be visual noise.
   - No-ops completely under prefers-reduced-motion: reduce.
   - No dependencies, no bundler, matches the existing admin.js
     coding style (plain functions + DOMContentLoaded).
═══════════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  var COUNT_UP_DURATION_MS = 700;
  var COUNT_UP_SELECTOR = '.stat-big';
  // Matches: "42", "1,204", "87%" — NOT "₱1,204.50", "—", "N/A", "Basic".
  var PLAIN_NUMBER_RE = /^([\d,]+)(%?)$/;

  function prefersReducedMotion() {
    return (
      window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    );
  }

  function easeOutQuad(t) {
    return 1 - (1 - t) * (1 - t);
  }

  function animateCount(el, target, suffix) {
    var start = performance.now();

    function tick(now) {
      var elapsed = now - start;
      var progress = Math.min(elapsed / COUNT_UP_DURATION_MS, 1);
      var eased = easeOutQuad(progress);
      var current = Math.round(target * eased);
      el.textContent = current.toLocaleString() + suffix;

      if (progress < 1) {
        requestAnimationFrame(tick);
      } else {
        el.textContent = target.toLocaleString() + suffix;
        el.setAttribute('data-counted', 'true');
      }
    }

    requestAnimationFrame(tick);
  }

  function initCountUp() {
    var candidates = document.querySelectorAll(COUNT_UP_SELECTOR);

    candidates.forEach(function (el) {
      if (el.hasAttribute('data-counted')) return;

      var raw = el.textContent.trim();
      var match = raw.match(PLAIN_NUMBER_RE);
      if (!match) {
        // Not a plain number (currency, dash, text) — leave as-is,
        // but mark counted so we never re-scan it.
        el.setAttribute('data-counted', 'true');
        return;
      }

      var target = parseInt(match[1].replace(/,/g, ''), 10);
      var suffix = match[2] || '';

      if (!Number.isFinite(target) || target <= 0) {
        el.setAttribute('data-counted', 'true');
        return;
      }

      if (prefersReducedMotion()) {
        el.setAttribute('data-counted', 'true');
        return; // value is already correct in the DOM — nothing to do
      }

      el.textContent = '0' + suffix;
      animateCount(el, target, suffix);
    });
  }

  document.addEventListener('DOMContentLoaded', initCountUp);
})();
