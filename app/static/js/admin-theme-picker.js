/* MyPortfolioHub — CSP-safe admin theme picker */
(() => {
  'use strict';

  function initThemePicker() {
    const page = document.querySelector('[data-theme-picker]');
    if (!page) return;

    const filterButtons = Array.from(page.querySelectorAll('.filter-tab'));
    const cards = Array.from(page.querySelectorAll('.theme-card'));
    const searchInput = page.querySelector('#themeSearchInput');
    const categorySelect = page.querySelector('#themeCategorySelect');
    const grid = page.querySelector('#themesGrid');

    page.querySelectorAll('[data-theme-preview-image]').forEach((image) => {
      image.addEventListener('error', () => {
        image.hidden = true;
        const fallback = image.nextElementSibling;
        if (fallback) fallback.hidden = false;
      }, { once: true });
    });
    const overlay = document.getElementById('applyOverlay');
    const overlayName = document.getElementById('applyOverlayName');
    const toast = document.getElementById('themeToast');
    const toastMessage = document.getElementById('themeToastMsg');
    const toastIcon = document.getElementById('toastIcon');
    let activeTier = 'all';
    let toastTimer = null;

    function setOverlay(visible, themeName = '') {
      if (!overlay) return;
      if (overlayName) overlayName.textContent = themeName;
      overlay.classList.toggle('show', visible);
      overlay.setAttribute('aria-hidden', visible ? 'false' : 'true');
    }

    function dismissThemeToast() {
      window.clearTimeout(toastTimer);
      if (!toast) return;
      toast.classList.remove('show');
    }

    function showThemeToast(message, type = 'success') {
      if (!toast || !toastMessage) return;
      window.clearTimeout(toastTimer);
      toast.className = `show t-${type}`;
      toastMessage.textContent = message;
      if (toastIcon) toastIcon.textContent = type === 'success' ? '✓' : '!';
      toastTimer = window.setTimeout(dismissThemeToast, 5000);
    }

    page.querySelectorAll('[data-theme-toast-close]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        dismissThemeToast();
      });
    });

    function applyFilters() {
      const query = (searchInput?.value || '').trim().toLowerCase();
      const category = categorySelect?.value || 'all';
      let visibleCount = 0;

      cards.forEach((card) => {
        const tierMatches = activeTier === 'all' || card.dataset.tier === activeTier;
        const categoryMatches = category === 'all' || (card.dataset.category || '') === category;
        const queryMatches = !query || (card.dataset.search || '').includes(query);
        const visible = tierMatches && categoryMatches && queryMatches;
        card.classList.toggle('tc-hidden', !visible);
        card.setAttribute('aria-hidden', visible ? 'false' : 'true');
        if (visible) visibleCount += 1;
      });

      let empty = document.getElementById('themesNoResults');
      if (visibleCount === 0 && grid) {
        if (!empty) {
          empty = document.createElement('div');
          empty.id = 'themesNoResults';
          empty.className = 'themes-empty-state';
          empty.innerHTML = '<span aria-hidden="true" style="font-size:2rem;display:block;margin-bottom:.75rem;opacity:.35">⌕</span>No themes match your filters.';
          grid.appendChild(empty);
        }
      } else if (empty) {
        empty.remove();
      }
    }

    filterButtons.forEach((button) => {
      button.addEventListener('click', () => {
        activeTier = button.dataset.filter || 'all';
        filterButtons.forEach((item) => {
          const active = item === button;
          item.classList.toggle('active', active);
          item.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
        applyFilters();
      });
    });
    searchInput?.addEventListener('input', applyFilters);
    categorySelect?.addEventListener('change', applyFilters);

    cards.forEach((card) => {
      card.addEventListener('pointermove', (event) => {
        if (event.pointerType === 'touch') return;
        const rect = card.getBoundingClientRect();
        card.style.setProperty('--glow-x', `${event.clientX - rect.left}px`);
        card.style.setProperty('--glow-y', `${event.clientY - rect.top}px`);
      });

      card.addEventListener('click', (event) => {
        if (event.target.closest('.tc-actions, button, a, input, select')) return;
        card.querySelector('.btn-preview-icon')?.click();
      });
    });

    function restoreApplyButton(button) {
      button.disabled = false;
      button.className = 'btn-apply st-available';
      button.innerHTML = '<span aria-hidden="true">↻</span> Apply Theme';
    }

    page.querySelectorAll('.theme-apply-form').forEach((form) => {
      form.addEventListener('submit', async (event) => {
        // Retain a normal form-submit fallback for older browsers.
        if (!window.fetch || !window.FormData) return;
        event.preventDefault();

        const button = form.querySelector('button[type="submit"]');
        if (!button || button.disabled) return;
        const themeName = form.dataset.themeName || button.dataset.themeName || 'Theme';

        setOverlay(true, themeName);
        button.disabled = true;
        button.className = 'btn-apply st-loading';
        button.innerHTML = '<span class="spin" aria-hidden="true">◌</span> Applying…';

        try {
          const response = await fetch(form.action, {
            method: 'POST',
            body: new FormData(form),
            headers: {
              'X-Requested-With': 'XMLHttpRequest',
              'Accept': 'application/json'
            },
            credentials: 'same-origin'
          });
          let data = {};
          try { data = await response.json(); } catch (_) { /* non-JSON fallback */ }

          if (response.ok && data.success === true) {
            showThemeToast(`“${themeName}” applied. Refreshing…`, 'success');
            window.setTimeout(() => window.location.reload(), 900);
            return;
          }

          setOverlay(false);
          restoreApplyButton(button);
          showThemeToast(data.error || `Could not apply the theme (HTTP ${response.status}).`, 'error');
        } catch (_) {
          setOverlay(false);
          restoreApplyButton(button);
          showThemeToast('Network error. Please try again.', 'error');
        }
      });
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        dismissThemeToast();
        setOverlay(false);
      }
    });

    applyFilters();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initThemePicker, { once: true });
  } else {
    initThemePicker();
  }
})();
