// ═══════════════════════════════════════════════
//  PORTFOLIO CMS — ADMIN JS
// ═══════════════════════════════════════════════

// ── CSRF token helper ────────────────────────
function getCsrf() {
  return document.querySelector('input[name="csrf_token"]')?.value
    || document.querySelector('meta[name="csrf-token"]')?.content
    || '';
}

// ── Toast helper ─────────────────────────────
function showToast(msg, type = 'success') {
  const icons = { success: '✓', danger: '✕', info: 'ℹ' };
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.innerHTML = `
    <span class="toast-icon">${icons[type] || '•'}</span>
    <span class="toast-msg">${msg}</span>
    <button class="toast-close" onclick="this.parentElement.remove()">✕</button>
  `;
  // Append to flash container or create one
  let container = document.querySelector('.flash-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'flash-container';
    document.body.appendChild(container);
  }
  container.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; }, 3500);
  setTimeout(() => { t.remove(); }, 4000);
}

// ── Simple drag-and-drop reorder ─────────────
function initDragReorder(containerSelector, reorderUrl) {
  const containers = document.querySelectorAll(containerSelector);
  containers.forEach(container => {
    let dragEl = null;

    container.querySelectorAll('[data-id]').forEach(item => {
      item.draggable = true;

      item.addEventListener('dragstart', e => {
        dragEl = item;
        item.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
      });

      item.addEventListener('dragend', () => {
        item.classList.remove('dragging');
        dragEl = null;
        // Collect new order and POST to server
        const order = [];
        container.querySelectorAll('[data-id]').forEach((el, idx) => {
          order.push({ id: parseInt(el.dataset.id), order: idx });
        });
        fetch(reorderUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrf()
          },
          body: JSON.stringify({ order })
        })
        .then(r => r.json())
        .then(() => showToast('Order saved'))
        .catch(() => showToast('Could not save order', 'danger'));
      });

      item.addEventListener('dragover', e => {
        e.preventDefault();
        if (!dragEl || dragEl === item) return;
        const rect = item.getBoundingClientRect();
        const midY = rect.top + rect.height / 2;
        if (e.clientY < midY) {
          container.insertBefore(dragEl, item);
        } else {
          container.insertBefore(dragEl, item.nextSibling);
        }
      });
    });
  });
}

// ── Image preview helper ─────────────────────
function previewImage(inputEl, previewImgId, placeholderId) {
  if (!inputEl.files || !inputEl.files[0]) return;
  const reader = new FileReader();
  reader.onload = e => {
    const img = document.getElementById(previewImgId);
    const ph = placeholderId ? document.getElementById(placeholderId) : null;
    if (img) { img.src = e.target.result; img.style.display = 'block'; }
    if (ph) ph.style.display = 'none';
  };
  reader.readAsDataURL(inputEl.files[0]);
}

// ── Confirm before nav away with unsaved changes ──
function markFormDirty(formSelector) {
  const form = document.querySelector(formSelector);
  if (!form) return;
  let dirty = false;
  form.querySelectorAll('input, textarea, select').forEach(el => {
    el.addEventListener('change', () => { dirty = true; });
  });
  form.addEventListener('submit', () => { dirty = false; });
  window.addEventListener('beforeunload', e => {
    if (dirty) {
      e.preventDefault();
      e.returnValue = '';
    }
  });
}

// ── Skills page init ─────────────────────────
if (document.querySelector('.skill-list')) {
  initDragReorder('.skill-list', '/admin/skills/reorder');
}

// ── Projects page init ───────────────────────
if (document.querySelector('.project-grid')) {
  initDragReorder('.project-grid', '/admin/projects/reorder');
}

// ── Form dirty tracking ──────────────────────
markFormDirty('.admin-form');

// ── Auto-grow textareas ──────────────────────
document.querySelectorAll('textarea.form-textarea').forEach(ta => {
  ta.addEventListener('input', function () {
    this.style.height = 'auto';
    this.style.height = this.scrollHeight + 'px';
  });
});

// ── Proficiency range live update ────────────
const range = document.getElementById('proficiencyRange');
if (range) {
  range.addEventListener('input', function () {
    const val = document.getElementById('profVal');
    if (val) val.textContent = this.value;
  });
}

// ── Tag chip input enhancement ───────────────
const tagInput = document.querySelector('input[name="tags"]');
if (tagInput) {
  tagInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      e.preventDefault(); // don't submit the form
      const val = tagInput.value.trim();
      if (!val.endsWith(',')) tagInput.value = val + ', ';
    }
  });
}

// ── Theme toggle persistence ───────────────────
const themeSwitch = document.getElementById('themeSwitch');
const themeIconSun = document.querySelector('.theme-icon-sun');
const themeIconMoon = document.querySelector('.theme-icon-moon');
const THEME_KEY = 'cms-admin-theme';

function setTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem(THEME_KEY, theme);
  if (theme === 'light') {
    themeIconSun.style.display = 'inline-block';
    themeIconMoon.style.display = 'none';
  } else {
    themeIconSun.style.display = 'none';
    themeIconMoon.style.display = 'inline-block';
  }
}

function initTheme() {
  const saved = localStorage.getItem(THEME_KEY) || 'dark';
  setTheme(saved);
}

if (themeSwitch) {
  themeSwitch.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    setTheme(current === 'dark' ? 'light' : 'dark');
  });
}

initTheme();

/* ═══════════════════════════════════════════════════
   ADMIN JS v2.0 — Design System Extensions
   Submit guards, skeleton loaders, copy helpers
═══════════════════════════════════════════════════ */

// ── Submit guard (prevent double-submit on all forms) ─────────
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('form').forEach(form => {
    // Skip forms with no submit button or already handled
    const submitBtn = form.querySelector('[type="submit"]');
    if (!submitBtn || form.dataset.submitGuarded) return;
    form.dataset.submitGuarded = 'true';
    form.addEventListener('submit', () => {
      submitBtn.setAttribute('data-loading', 'true');
      submitBtn.disabled = true;
      // Re-enable after 8s as fallback
      setTimeout(() => {
        submitBtn.removeAttribute('data-loading');
        submitBtn.disabled = false;
      }, 8000);
    });
  });
});

// ── Skeleton loader helpers ───────────────────────────────────
function showSkeleton(containerId, rows = 3) {
  const el = document.getElementById(containerId);
  if (!el) return;
  let html = '';
  for (let i = 0; i < rows; i++) {
    html += `<div class="skeleton-row">
      <div class="skeleton skeleton-avatar"></div>
      <div style="flex:1">
        <div class="skeleton skeleton-text"></div>
        <div class="skeleton skeleton-text w-3/4"></div>
      </div>
    </div>`;
  }
  el.innerHTML = html;
}

// ── Copy to clipboard helper ──────────────────────────────────
function copyToClipboard(text, btn) {
  navigator.clipboard.writeText(text).then(() => {
    if (btn) {
      const orig = btn.innerHTML;
      btn.innerHTML = '<iconify-icon icon="lucide:check" width="13"></iconify-icon> Copied';
      btn.style.color = 'var(--success)';
      setTimeout(() => { btn.innerHTML = orig; btn.style.color = ''; }, 1800);
    }
  });
}

// ── Toast helper (programmatic) ───────────────────────────────
function showToast(message, type = 'info', duration = 4500) {
  const container = document.querySelector('.flash-container') || (() => {
    const c = document.createElement('div');
    c.className = 'flash-container';
    document.body.appendChild(c);
    return c;
  })();
  const icons = {
    success: 'lucide:check-circle',
    danger: 'lucide:x-circle',
    warning: 'lucide:alert-triangle',
    info: 'lucide:info'
  };
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `
    <span class="toast-icon"><iconify-icon icon="${icons[type] || icons.info}" width="15"></iconify-icon></span>
    <span class="toast-msg">${message}</span>
    <button class="toast-close" onclick="this.parentElement.remove()">
      <iconify-icon icon="lucide:x" width="13"></iconify-icon>
    </button>`;
  container.appendChild(toast);
  setTimeout(() => { toast.style.opacity = '0'; toast.style.transform = 'translateX(100%)'; toast.style.transition = 'all 0.3s ease'; }, duration);
  setTimeout(() => toast.remove(), duration + 350);
}

// ── Upload zone drag-and-drop (global) ────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.upload-zone').forEach(zone => {
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('drag-over'); });
  });
});

// ── Plan card selection ───────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.plan-card-v2 input[type="radio"]').forEach(radio => {
    radio.addEventListener('change', () => {
      document.querySelectorAll('.plan-card-v2').forEach(c => c.classList.remove('selected'));
      radio.closest('.plan-card-v2')?.classList.add('selected');
    });
  });
});

// ─────────────────────────────────────────────────────
// v3.6.1 — Admin UI Polish additions
// ─────────────────────────────────────────────────────

// ── Animate progress bars + completion ring on page load ──
(function animateOnLoad() {
  // Progress mini bars
  document.querySelectorAll('.progress-mini-bar, .progress-fill').forEach(el => {
    const target = el.style.width;
    el.style.width = '0';
    requestAnimationFrame(() => {
      setTimeout(() => { el.style.width = target; }, 100);
    });
  });

  // Skill bars
  document.querySelectorAll('.skill-bar-fill').forEach(el => {
    const target = el.style.width;
    el.style.width = '0';
    requestAnimationFrame(() => {
      setTimeout(() => { el.style.width = target; }, 200);
    });
  });
})();

// ── JS loading state on forms ─────────────────────────
document.querySelectorAll('[data-loading="true"]').forEach(form => {
  form.addEventListener('submit', function() {
    const btn = this.querySelector('button[type="submit"], input[type="submit"]');
    if (btn) {
      btn.classList.add('btn-loading');
      btn.setAttribute('disabled', 'disabled');
    }
  });
});

// ── Plan card selection highlight ─────────────────────
document.querySelectorAll('.plan-card-v2').forEach(card => {
  const radio = card.querySelector('input[type="radio"]');
  if (!radio) return;
  function syncSelected() {
    document.querySelectorAll('.plan-card-v2').forEach(c => c.classList.remove('selected'));
    if (radio.checked) card.classList.add('selected');
  }
  radio.addEventListener('change', syncSelected);
  syncSelected();
  card.addEventListener('click', () => { radio.checked = true; radio.dispatchEvent(new Event('change', {bubbles:true})); });
});

// ── Copy to clipboard helper ──────────────────────────
document.querySelectorAll('.copy-btn[data-copy]').forEach(btn => {
  btn.addEventListener('click', function() {
    const text = this.getAttribute('data-copy');
    navigator.clipboard?.writeText(text).then(() => {
      const orig = this.innerHTML;
      this.innerHTML = '✓ Copied';
      this.style.color = 'var(--success)';
      setTimeout(() => { this.innerHTML = orig; this.style.color = ''; }, 1500);
    });
  });
});

// ── Nav active on child routes (fallback) ─────────────
(function markActiveNav() {
  const path = window.location.pathname;
  document.querySelectorAll('.nav-item').forEach(link => {
    const href = link.getAttribute('href');
    if (!href) return;
    if (href !== '/' && path.startsWith(href) && !link.classList.contains('active')) {
      // Don't override server-set active, only add if not already done
    }
  });
})();
