/**
 * settings-form.js — shared behaviors for CMS-style settings pages
 * (landing CMS, and any future page using .settings-shell / .sticky-actions-bar).
 *
 * 1. Unified save-status state machine driving the sticky-bar pill:
 *    idle -> dirty -> saving -> saved | error. A single controller owns
 *    every write to the pill so autosave and manual-submit tracking can
 *    never race each other over the same DOM node.
 * 2. Per-field debounced autosave (opt-in via [data-autosave-url] on the
 *    form): 1.5s after the last edit to a given field, that field alone
 *    is POSTed to the autosave endpoint. Only ever writes the one field
 *    that changed — never a full-form submit — so a debounce firing
 *    mid-edit on another field can't clobber it.
 * 3. Inline field validation: autosave error responses render a
 *    .field-err message under the field and flag it .field-error,
 *    clearing automatically once a corrected value autosaves clean.
 * 4. Live character counters for every field carrying a maxlength.
 * 5. Scroll-spy: highlights the matching .settings-nav link as sections
 *    with [data-settings-section] scroll through view.
 *
 * Opt-in throughout — every piece does nothing unless its markup is
 * present, so this is safe to load globally from base.html.
 */
(function () {
  'use strict';

  const AUTOSAVE_DEBOUNCE_MS = 1500;

  function initSettingsForm() {
    const form = document.querySelector('[data-settings-form]');
    const status = document.querySelector('[data-role="save-status"]');
    if (!form || !status) return;

    const autosaveUrl = form.dataset.autosaveUrl || '';
    const csrfInput = form.querySelector('input[name="csrf_token"]');
    const csrfToken = csrfInput ? csrfInput.value : '';

    // ---- Save-status state machine -----------------------------------
    // debouncing: fields edited but not yet sent (still in the 1.5s window).
    // inFlight:   fields whose autosave request is currently pending.
    // errors:     fields whose last autosave attempt failed validation.
    const debouncing = new Set();
    const inFlight = new Set();
    const errors = new Set();
    let everSaved = false;
    let submitting = false;

    function render() {
      let text = 'No changes';
      let kind = '';
      if (submitting) {
        text = 'Saving…';
        kind = 'saving';
      } else if (errors.size > 0) {
        text = 'Autosave failed — check highlighted fields';
        kind = 'error';
      } else if (inFlight.size > 0) {
        text = 'Saving…';
        kind = 'saving';
      } else if (debouncing.size > 0) {
        text = 'Unsaved changes';
        kind = 'dirty';
      } else if (everSaved) {
        text = 'All changes saved';
        kind = 'saved';
      }
      status.className = 'sticky-actions-status' + (kind ? ' is-' + kind : '');
      status.innerHTML = '<span class="dot"></span>' + text;
    }

    // ---- Inline field error rendering ---------------------------------
    function fieldWrapper(input) {
      return input.closest('.form-field');
    }

    function setFieldError(input, message) {
      const wrapper = fieldWrapper(input);
      input.classList.add('field-error');
      if (!wrapper) return;
      let err = wrapper.querySelector('.field-err');
      if (!err) {
        err = document.createElement('div');
        err.className = 'field-err';
        wrapper.appendChild(err);
      }
      err.textContent = message;
      err.dataset.autosaveError = 'true';
    }

    function clearFieldError(input) {
      input.classList.remove('field-error');
      const wrapper = fieldWrapper(input);
      if (!wrapper) return;
      const err = wrapper.querySelector('.field-err[data-autosave-error="true"]');
      if (err) err.remove();
    }

    // ---- Character counters -------------------------------------------
    function initCounter(input) {
      const max = parseInt(input.getAttribute('maxlength'), 10);
      if (!max) return;
      const wrapper = fieldWrapper(input);
      if (!wrapper) return;
      let counter = wrapper.querySelector('.field-char-count');
      if (!counter) {
        counter = document.createElement('div');
        counter.className = 'field-char-count';
        wrapper.appendChild(counter);
      }
      const update = () => {
        const len = input.value.length;
        counter.textContent = len + ' / ' + max;
        counter.classList.toggle('is-near-limit', len >= max * 0.9);
      };
      update();
      input.addEventListener('input', update);
    }

    // ---- Autosave -------------------------------------------------------
    const timers = new Map();

    function scheduleAutosave(input) {
      if (!autosaveUrl || !input.name) return;
      const name = input.name;
      debouncing.add(name);
      render();
      if (timers.has(name)) clearTimeout(timers.get(name));
      timers.set(
        name,
        setTimeout(() => {
          timers.delete(name);
          debouncing.delete(name);
          runAutosave(input);
        }, AUTOSAVE_DEBOUNCE_MS)
      );
    }

    function runAutosave(input) {
      const name = input.name;
      const value = input.type === 'checkbox' ? input.checked : input.value;

      inFlight.add(name);
      render();

      fetch(autosaveUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        credentials: 'same-origin',
        body: JSON.stringify({ field: name, value: value, csrf_token: csrfToken }),
      })
        .then((res) => res.json().catch(() => ({})).then((data) => ({ ok: res.ok, data: data })))
        .then(({ ok, data }) => {
          inFlight.delete(name);
          if (ok && data && data.success) {
            errors.delete(name);
            clearFieldError(input);
            everSaved = true;
          } else {
            errors.add(name);
            setFieldError(input, (data && data.error) || 'Could not save this field.');
          }
          render();
        })
        .catch(() => {
          inFlight.delete(name);
          errors.add(name);
          setFieldError(input, 'Network error — this field was not saved.');
          render();
        });
    }

    // ---- Wire up fields --------------------------------------------------
    const fields = Array.from(form.elements).filter(
      (el) =>
        el.name &&
        el.name !== 'csrf_token' &&
        (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') &&
        el.type !== 'submit' &&
        el.type !== 'button' &&
        el.type !== 'file'
    );

    fields.forEach((input) => {
      initCounter(input);
      const handler = () => {
        render();
        scheduleAutosave(input);
      };
      input.addEventListener('input', handler);
      input.addEventListener('change', handler);
    });

    form.addEventListener('submit', () => {
      submitting = true;
      timers.forEach((t) => clearTimeout(t));
      timers.clear();
      render();
    });

    render();
  }

  function initSectionNav() {
    const nav = document.querySelector('.settings-nav');
    if (!nav) return;

    const links = Array.from(nav.querySelectorAll('a[href^="#"]'));
    if (!links.length) return;

    const maybeScrollActiveTab = (link) => {
      // Only horizontally scroll the section nav when it is truly scrollable.
      // On narrow phones the nav is a 2-column grid; calling scrollIntoView
      // there can shift the whole document sideways in DevTools/mobile view.
      if (!link || typeof link.scrollIntoView !== 'function') return;
      const navEl = link.closest('.settings-nav');
      if (!navEl) return;
      if (navEl.scrollWidth > navEl.clientWidth + 8) {
        link.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
      }
    };

    const getTarget = (link) => {
      const href = link.getAttribute('href') || '';
      if (!href.startsWith('#') || href.length <= 1) return null;
      try {
        return document.querySelector(href);
      } catch (_) {
        return null;
      }
    };

    const openTarget = (target) => {
      if (!target) return;
      // Landing/Pricing CMS sections are <details>. A side-nav click used to
      // only jump to the collapsed card, which made Contact look broken.
      // Open the section before scrolling so its editable fields are visible.
      if (target.tagName === 'DETAILS') target.open = true;
    };

    const activate = (targetId) => {
      links.forEach((a) => {
        const active = a.getAttribute('href') === '#' + targetId;
        a.classList.toggle('is-active', active);
        if (active) maybeScrollActiveTab(a);
      });
    };

    links.forEach((link) => {
      link.addEventListener('click', (event) => {
        const target = getTarget(link);
        if (!target) return;
        event.preventDefault();
        openTarget(target);
        activate(target.id);
        if (history && history.pushState) history.pushState(null, '', '#' + target.id);
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });

        const firstFocusable = target.querySelector('summary, input, textarea, select, button, a[href]');
        if (firstFocusable && typeof firstFocusable.focus === 'function') {
          setTimeout(() => firstFocusable.focus({ preventScroll: true }), 250);
        }
      });
    });

    if (window.location.hash) {
      const initial = document.querySelector(window.location.hash);
      if (initial) {
        openTarget(initial);
        activate(initial.id);
        setTimeout(() => initial.scrollIntoView({ behavior: 'auto', block: 'start' }), 0);
      }
    }
  }

  function initScrollSpy() {
    const nav = document.querySelector('.settings-nav');
    if (!nav) return;
    const links = Array.from(nav.querySelectorAll('a[href^="#"]'));
    const sections = links
      .map((a) => document.querySelector(a.getAttribute('href')))
      .filter(Boolean);
    if (!sections.length) return;

    const setActive = (id) => {
      links.forEach((a) => {
        const active = a.getAttribute('href') === '#' + id;
        a.classList.toggle('is-active', active);
        if (active && window.matchMedia('(max-width: 900px)').matches) maybeScrollActiveTab(a);
      });
    };

    const observer = new IntersectionObserver(
      (entries) => {
        // Pick the entry closest to the top of the viewport among visible ones.
        const visible = entries.filter((e) => e.isIntersecting);
        if (visible.length) setActive(visible[0].target.id);
      },
      { rootMargin: '-15% 0px -70% 0px', threshold: 0 }
    );
    sections.forEach((sec) => observer.observe(sec));
    setActive((window.location.hash || '#' + sections[0].id).replace('#', ''));
  }



  function initFounderPhotoEditor() {
    const editor = document.querySelector('[data-founder-photo-editor]');
    if (!editor) return;

    const photoInput = document.getElementById('founder_photo_url');
    const fitInput = editor.querySelector('[data-founder-control="fit"]');
    const xInput = editor.querySelector('[data-founder-control="x"]');
    const yInput = editor.querySelector('[data-founder-control="y"]');
    const zoomInput = editor.querySelector('[data-founder-control="zoom"]');
    const resetBtn = editor.querySelector('[data-founder-reset-crop]');
    const previewBox = editor.querySelector('.founder-photo-editor-preview');

    function clamp(value, min, max, fallback) {
      const n = parseInt(value, 10);
      if (Number.isNaN(n)) return fallback;
      return Math.max(min, Math.min(max, n));
    }

    function ensurePreviewImage() {
      let img = editor.querySelector('[data-founder-preview-img]');
      const url = photoInput ? photoInput.value.trim() : '';
      if (!url) return img;
      if (!img && previewBox) {
        const empty = editor.querySelector('[data-founder-preview-empty]');
        if (empty) empty.remove();
        img = document.createElement('img');
        img.alt = 'Founder portrait crop preview';
        img.dataset.founderPreviewImg = 'true';
        previewBox.appendChild(img);
      }
      if (img && img.getAttribute('src') !== url) img.setAttribute('src', url);
      return img;
    }

    function updateOutputs(x, y, zoom) {
      const outX = editor.querySelector('[data-founder-output="x"]');
      const outY = editor.querySelector('[data-founder-output="y"]');
      const outZoom = editor.querySelector('[data-founder-output="zoom"]');
      if (outX) outX.textContent = x;
      if (outY) outY.textContent = y;
      if (outZoom) outZoom.textContent = zoom;
    }

    function applyPreview() {
      const img = ensurePreviewImage();
      if (!img) return;
      const fit = fitInput && fitInput.value === 'contain' ? 'contain' : 'cover';
      const x = clamp(xInput && xInput.value, 0, 100, 50);
      const y = clamp(yInput && yInput.value, 0, 100, 50);
      const zoom = clamp(zoomInput && zoomInput.value, 100, 180, 100);
      img.style.objectFit = fit;
      img.style.objectPosition = x + '% ' + y + '%';
      img.style.transformOrigin = x + '% ' + y + '%';
      img.style.transform = 'scale(' + (zoom / 100).toFixed(2) + ')';
      updateOutputs(x, y, zoom);
    }

    [photoInput, fitInput, xInput, yInput, zoomInput].filter(Boolean).forEach((input) => {
      input.addEventListener('input', applyPreview);
      input.addEventListener('change', applyPreview);
    });

    if (resetBtn) {
      resetBtn.addEventListener('click', () => {
        if (fitInput) fitInput.value = 'cover';
        if (xInput) xInput.value = 50;
        if (yInput) yInput.value = 50;
        if (zoomInput) zoomInput.value = 100;
        [fitInput, xInput, yInput, zoomInput].filter(Boolean).forEach((input) => {
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.dispatchEvent(new Event('change', { bubbles: true }));
        });
        applyPreview();
      });
    }

    applyPreview();
  }

  document.addEventListener('DOMContentLoaded', () => {
    initSettingsForm();
    initSectionNav();
    initScrollSpy();
    initFounderPhotoEditor();
  });
})();
