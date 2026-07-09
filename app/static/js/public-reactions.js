/**
 * app/static/js/public-reactions.js
 *
 * Shared like/react wiring for project cards rendered inside the
 * app/public/templates/public/_base.html design system (currently
 * /explore and /feed). Deliberately factored OUT of landing.js instead
 * of copy-pasted into two more templates -- same backend contract
 * (POST /api/projects/<id>/like|unlike, see app/public/routes.py),
 * same markup contract (.ph-project-card[data-project-id] > .ph-like-btn),
 * one implementation.
 *
 * Requires: <meta name="csrf-token" content="..."> in <head> (already
 * present in _base.html) and the .ph-like-btn / .ph-like-count markup
 * from templates/public/{explore,feed}.html.
 */
(function () {
  'use strict';

  function csrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
  }

  function showToast(message) {
    let toast = document.getElementById('ph-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'ph-toast';
      toast.className = 'ph-toast';
      document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add('is-visible');
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => toast.classList.remove('is-visible'), 3200);
  }

  function paint(btn, liked, count) {
    btn.dataset.liked = liked ? '1' : '0';
    btn.setAttribute('aria-pressed', String(liked));
    btn.classList.toggle('is-liked', liked);
    const countEl = btn.querySelector('.ph-like-count');
    if (countEl) countEl.textContent = count;
  }

  async function toggleLike(btn) {
    const projectId = btn.dataset.projectId;
    if (!projectId) return;

    if (btn.dataset.authRequired === '1') {
      const next = window.location.pathname + window.location.search;
      window.location.href = '/auth?tab=signin&next=' + encodeURIComponent(next);
      return;
    }

    const liked = btn.dataset.liked === '1';
    const countEl = btn.querySelector('.ph-like-count');
    const current = parseInt(countEl ? countEl.textContent : '0', 10) || 0;
    const optimisticCount = liked ? Math.max(current - 1, 0) : current + 1;

    paint(btn, !liked, optimisticCount);
    btn.disabled = true;

    const url = `/api/projects/${projectId}/${liked ? 'unlike' : 'like'}`;
    try {
      const resp = await fetch(url, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Accept': 'application/json',
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken(),
        },
      });
      const json = await resp.json().catch(() => ({}));
      if (!resp.ok || json.success !== true) {
        throw new Error(json.message || 'Unable to update reaction.');
      }
      paint(btn, json.liked === true, json.like_count ?? optimisticCount);
    } catch (err) {
      paint(btn, liked, current);
      showToast(err?.message || 'Unable to update like. Please try again.');
    } finally {
      btn.disabled = false;
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.ph-like-btn').forEach((btn) => {
      btn.addEventListener('click', () => toggleLike(btn));
    });
  });
})();
