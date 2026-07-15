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
      const dot = document.createElement('span');
      dot.className = 'dot';
      status.replaceChildren(dot, document.createTextNode(text));
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



  function initHeroImageEditor() {
    const editor = document.querySelector('[data-hero-image-editor]');
    if (!editor) return;

    const imageInput = document.getElementById('hero_image_url');
    const browserTextInput = document.getElementById('hero_preview_url_text');
    const nameInput = document.getElementById('hero_preview_name');
    const roleInput = document.getElementById('hero_preview_role');
    const badgeInput = document.getElementById('hero_stat_badge_text');
    const likesInput = document.getElementById('hero_stat_likes');
    const viewsInput = document.getElementById('hero_stat_views');
    const commentsInput = document.getElementById('hero_stat_comments');
    const widgetsInput = document.getElementById('hero_enable_widgets');
    const animationInput = document.getElementById('hero_enable_animation');

    const fitInput = editor.querySelector('[data-hero-control="fit"]');
    const xInput = editor.querySelector('[data-hero-control="x"]');
    const yInput = editor.querySelector('[data-hero-control="y"]');
    const zoomInput = editor.querySelector('[data-hero-control="zoom"]');
    const resetBtn = editor.querySelector('[data-hero-reset-frame]');
    const centerBtn = editor.querySelector('[data-hero-center-frame]');
    const zoomInBtn = editor.querySelector('[data-hero-zoom-in]');
    const zoomOutBtn = editor.querySelector('[data-hero-zoom-out]');
    const openLink = editor.querySelector('[data-hero-open-image]');
    const previewBox = editor.querySelector('[data-hero-preview-box]');
    const browser = editor.querySelector('[data-hero-live-browser]');
    const browserText = editor.querySelector('[data-hero-browser-text]');
    const caption = editor.querySelector('[data-hero-caption]');
    const captionName = editor.querySelector('[data-hero-caption-name]');
    const captionRole = editor.querySelector('[data-hero-caption-role]');
    const engage = editor.querySelector('[data-hero-engage]');
    const publishedWidget = editor.querySelector('[data-hero-floating-published]');
    const likedWidget = editor.querySelector('[data-hero-floating-liked]');
    const badgeText = editor.querySelector('[data-hero-badge-text]');
    const likeCount = editor.querySelector('[data-hero-like-count]');
    const likedText = editor.querySelector('[data-hero-liked-text]');
    const viewCount = editor.querySelector('[data-hero-view-count]');
    const commentCount = editor.querySelector('[data-hero-comment-count]');
    const statusText = editor.querySelector('[data-hero-editor-status]');

    let dragState = null;
    let wheelCommitTimer = null;

    function clamp(value, min, max, fallback) {
      const n = Number.parseFloat(value);
      if (Number.isNaN(n)) return fallback;
      return Math.max(min, Math.min(max, n));
    }

    function valueOr(input, fallback) {
      if (!input) return fallback;
      const value = String(input.value || '').trim();
      return value || fallback;
    }

    function setStatus(message, state) {
      if (statusText) statusText.textContent = message;
      if (!editor) return;
      editor.dataset.heroEditorState = state || 'ready';
    }

    function dispatchControl(input, eventName) {
      if (!input) return;
      input.dispatchEvent(new Event(eventName || 'input', { bubbles: true }));
    }

    function commitControls(inputs) {
      inputs.filter(Boolean).forEach((input) => dispatchControl(input, 'change'));
    }

    function removeEmptyState() {
      const empty = editor.querySelector('[data-hero-preview-empty]');
      if (empty) empty.remove();
    }

    function showEmpty(message, options) {
      const settings = options || {};
      const img = editor.querySelector('[data-hero-preview-img]');
      if (settings.removeImage !== false && img) img.remove();
      if (!previewBox) return;

      let empty = editor.querySelector('[data-hero-preview-empty]');
      if (!empty) {
        empty = document.createElement('div');
        empty.className = 'hero-image-editor-empty';
        empty.dataset.heroPreviewEmpty = 'true';
        empty.innerHTML = '<iconify-icon icon="lucide:image" width="24"></iconify-icon><span></span>';
        previewBox.appendChild(empty);
      }
      const label = empty.querySelector('span');
      if (label) label.textContent = message || 'Upload or paste a hero image to start the live editor.';
    }

    function bindImageEvents(img) {
      if (!img || img.dataset.heroEventsBound === 'true') return;
      img.dataset.heroEventsBound = 'true';
      img.addEventListener('load', () => {
        if (previewBox) {
          previewBox.classList.remove('is-loading', 'has-error');
          previewBox.classList.add('has-image');
        }
        removeEmptyState();
        setStatus('Image loaded — drag inside the frame to reposition it.', 'ready');
      });
      img.addEventListener('error', () => {
        if (previewBox) {
          previewBox.classList.remove('is-loading', 'has-image');
          previewBox.classList.add('has-error');
        }
        showEmpty('The image URL could not be loaded. Replace it or verify that it is publicly accessible.', { removeImage: false });
        setStatus('Image failed to load. Check the image URL or upload it again.', 'error');
      });
    }

    function ensurePreviewImage() {
      const url = imageInput ? imageInput.value.trim() : '';
      let img = editor.querySelector('[data-hero-preview-img]');

      if (!url) {
        if (previewBox) previewBox.classList.remove('is-loading', 'has-error', 'has-image');
        showEmpty('Upload or paste a hero image to start the live editor.');
        setStatus('Waiting for a hero image.', 'empty');
        return null;
      }

      if (!img && previewBox) {
        removeEmptyState();
        img = document.createElement('img');
        img.alt = 'Landing hero frame preview';
        img.dataset.heroPreviewImg = 'true';
        img.draggable = false;
        previewBox.insertBefore(img, previewBox.firstChild);
      }

      if (img) {
        bindImageEvents(img);
        if (img.getAttribute('src') !== url) {
          if (previewBox) {
            previewBox.classList.add('is-loading');
            previewBox.classList.remove('has-error');
          }
          removeEmptyState();
          img.setAttribute('src', url);
          setStatus('Loading hero image…', 'loading');
        } else if (img.complete) {
          if (img.naturalWidth > 0) {
            if (previewBox) {
              previewBox.classList.remove('is-loading', 'has-error');
              previewBox.classList.add('has-image');
            }
            removeEmptyState();
            setStatus('Image loaded — drag inside the frame to reposition it.', 'ready');
          } else if (img.getAttribute('src')) {
            if (previewBox) previewBox.classList.add('has-error');
            showEmpty('The image URL could not be loaded. Replace it or verify that it is publicly accessible.', { removeImage: false });
            setStatus('Image failed to load. Check the image URL or upload it again.', 'error');
          }
        }
      }

      if (openLink) {
        openLink.href = url;
        openLink.removeAttribute('aria-disabled');
        openLink.removeAttribute('tabindex');
      }
      return img;
    }

    function updateOutputs(x, y, zoom) {
      const outX = editor.querySelector('[data-hero-output="x"]');
      const outY = editor.querySelector('[data-hero-output="y"]');
      const outZoom = editor.querySelector('[data-hero-output="zoom"]');
      if (outX) outX.textContent = Math.round(x);
      if (outY) outY.textContent = Math.round(y);
      if (outZoom) outZoom.textContent = Math.round(zoom);
    }

    function updateLiveContent() {
      const name = valueOr(nameInput, '');
      const role = valueOr(roleInput, '');
      const likes = valueOr(likesInput, '128');
      const views = valueOr(viewsInput, '214');
      const comments = valueOr(commentsInput, '12');
      const badge = valueOr(badgeInput, 'Portfolio published');
      const widgetsEnabled = widgetsInput ? widgetsInput.checked : true;
      const animationEnabled = animationInput ? animationInput.checked : true;

      if (browserText) browserText.textContent = valueOr(browserTextInput, 'myportfoliohub.online/you');
      if (captionName) captionName.textContent = name;
      if (captionRole) captionRole.textContent = role;
      if (caption) caption.hidden = !name && !role;
      if (badgeText) badgeText.textContent = badge;
      if (likeCount) likeCount.textContent = likes;
      if (likedText) likedText.textContent = likes;
      if (viewCount) viewCount.textContent = views;
      if (commentCount) commentCount.textContent = comments;

      [engage, publishedWidget, likedWidget].filter(Boolean).forEach((element) => {
        element.classList.toggle('is-hidden', !widgetsEnabled);
      });
      if (browser) browser.classList.toggle('no-animate', !animationEnabled);
    }

    function applyPreview() {
      const url = imageInput ? imageInput.value.trim() : '';
      if (!url && openLink) {
        openLink.href = '#';
        openLink.setAttribute('aria-disabled', 'true');
        openLink.setAttribute('tabindex', '-1');
      }

      const img = ensurePreviewImage();
      const fit = fitInput && fitInput.value === 'contain' ? 'contain' : 'cover';
      const x = clamp(xInput && xInput.value, 0, 100, 50);
      const y = clamp(yInput && yInput.value, 0, 100, 50);
      const zoom = clamp(zoomInput && zoomInput.value, 100, 180, 100);

      if (img) {
        img.style.objectFit = fit;
        img.style.objectPosition = x + '% ' + y + '%';
        img.style.transformOrigin = x + '% ' + y + '%';
        img.style.transform = 'scale(' + (zoom / 100).toFixed(2) + ')';
      }

      updateOutputs(x, y, zoom);
      updateLiveContent();
    }

    function setPosition(x, y, commit) {
      if (xInput) xInput.value = Math.round(clamp(x, 0, 100, 50));
      if (yInput) yInput.value = Math.round(clamp(y, 0, 100, 50));
      if (xInput) dispatchControl(xInput, 'input');
      if (yInput) dispatchControl(yInput, 'input');
      if (commit) commitControls([xInput, yInput]);
    }

    function setZoom(value, commit) {
      if (!zoomInput) return;
      zoomInput.value = Math.round(clamp(value, 100, 180, 100));
      dispatchControl(zoomInput, 'input');
      if (commit) dispatchControl(zoomInput, 'change');
    }

    [
      imageInput,
      browserTextInput,
      nameInput,
      roleInput,
      badgeInput,
      likesInput,
      viewsInput,
      commentsInput,
      widgetsInput,
      animationInput,
      fitInput,
      xInput,
      yInput,
      zoomInput,
    ].filter(Boolean).forEach((input) => {
      input.addEventListener('input', applyPreview);
      input.addEventListener('change', applyPreview);
    });

    if (previewBox) {
      previewBox.addEventListener('pointerdown', (event) => {
        const img = editor.querySelector('[data-hero-preview-img]');
        if (!img || previewBox.classList.contains('has-error') || event.button !== 0) return;
        event.preventDefault();
        previewBox.focus({ preventScroll: true });
        if (fitInput && fitInput.value !== 'contain' && clamp(zoomInput && zoomInput.value, 100, 180, 100) <= 100) {
          // A 16:9 screenshot at 100% has no overflow to drag. Add a small,
          // predictable zoom as soon as the user starts repositioning it.
          setZoom(105, false);
        }
        previewBox.setPointerCapture(event.pointerId);
        dragState = {
          pointerId: event.pointerId,
          startClientX: event.clientX,
          startClientY: event.clientY,
          startX: clamp(xInput && xInput.value, 0, 100, 50),
          startY: clamp(yInput && yInput.value, 0, 100, 50),
          moved: false,
        };
        previewBox.classList.add('is-dragging', 'has-interacted');
        setStatus('Repositioning image… release to save the new frame.', 'editing');
      });

      previewBox.addEventListener('pointermove', (event) => {
        if (!dragState || event.pointerId !== dragState.pointerId) return;
        const rect = previewBox.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        const zoomFactor = clamp(zoomInput && zoomInput.value, 100, 180, 100) / 100;
        const dx = event.clientX - dragState.startClientX;
        const dy = event.clientY - dragState.startClientY;
        const nextX = dragState.startX - ((dx / rect.width) * 100) / zoomFactor;
        const nextY = dragState.startY - ((dy / rect.height) * 100) / zoomFactor;
        dragState.moved = dragState.moved || Math.abs(dx) > 1 || Math.abs(dy) > 1;
        setPosition(nextX, nextY, false);
      });

      const finishDrag = (event) => {
        if (!dragState || (event && event.pointerId !== dragState.pointerId)) return;
        const moved = dragState.moved;
        dragState = null;
        previewBox.classList.remove('is-dragging');
        if (moved) {
          commitControls([xInput, yInput]);
          setStatus('Frame updated. The saved landing page will use this exact position.', 'ready');
        } else {
          setStatus('Drag the image to reposition it, or scroll to zoom.', 'ready');
        }
      };

      previewBox.addEventListener('pointerup', finishDrag);
      previewBox.addEventListener('pointercancel', finishDrag);
      previewBox.addEventListener('lostpointercapture', finishDrag);

      previewBox.addEventListener('wheel', (event) => {
        const img = editor.querySelector('[data-hero-preview-img]');
        if (!img || previewBox.classList.contains('has-error')) return;
        event.preventDefault();
        previewBox.classList.add('has-interacted');
        const current = clamp(zoomInput && zoomInput.value, 100, 180, 100);
        const step = event.deltaY < 0 ? 4 : -4;
        setZoom(current + step, false);
        setStatus('Zooming image…', 'editing');
        window.clearTimeout(wheelCommitTimer);
        wheelCommitTimer = window.setTimeout(() => {
          commitControls([zoomInput]);
          setStatus('Zoom updated. The public landing preview will match this frame.', 'ready');
        }, 220);
      }, { passive: false });

      previewBox.addEventListener('keydown', (event) => {
        const key = event.key;
        const moveStep = event.shiftKey ? 5 : 1;
        const x = clamp(xInput && xInput.value, 0, 100, 50);
        const y = clamp(yInput && yInput.value, 0, 100, 50);
        const zoom = clamp(zoomInput && zoomInput.value, 100, 180, 100);
        let handled = true;

        if (key === 'ArrowLeft') setPosition(x - moveStep, y, true);
        else if (key === 'ArrowRight') setPosition(x + moveStep, y, true);
        else if (key === 'ArrowUp') setPosition(x, y - moveStep, true);
        else if (key === 'ArrowDown') setPosition(x, y + moveStep, true);
        else if (key === '+' || key === '=') setZoom(zoom + 5, true);
        else if (key === '-' || key === '_') setZoom(zoom - 5, true);
        else if (key === 'Home') setPosition(50, 50, true);
        else handled = false;

        if (handled) {
          event.preventDefault();
          previewBox.classList.add('has-interacted');
          setStatus('Frame fine-tuned with the keyboard.', 'ready');
        }
      });
    }

    if (zoomInBtn) zoomInBtn.addEventListener('click', () => {
      setZoom(clamp(zoomInput && zoomInput.value, 100, 180, 100) + 5, true);
      setStatus('Zoom increased.', 'ready');
    });

    if (zoomOutBtn) zoomOutBtn.addEventListener('click', () => {
      setZoom(clamp(zoomInput && zoomInput.value, 100, 180, 100) - 5, true);
      setStatus('Zoom decreased.', 'ready');
    });

    if (centerBtn) centerBtn.addEventListener('click', () => {
      setPosition(50, 50, true);
      setStatus('Image centered in the landing frame.', 'ready');
    });

    if (resetBtn) {
      resetBtn.addEventListener('click', () => {
        if (fitInput) fitInput.value = 'cover';
        if (xInput) xInput.value = 50;
        if (yInput) yInput.value = 50;
        if (zoomInput) zoomInput.value = 100;
        [fitInput, xInput, yInput, zoomInput].filter(Boolean).forEach((input) => dispatchControl(input, 'input'));
        commitControls([fitInput, xInput, yInput, zoomInput]);
        if (previewBox) previewBox.classList.remove('has-interacted');
        setStatus('Frame reset to the recommended center crop.', 'ready');
      });
    }

    applyPreview();
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
      if (!url) {
        if (img) img.remove();
        if (previewBox && !editor.querySelector('[data-founder-preview-empty]')) {
          const empty = document.createElement('div');
          empty.className = 'founder-photo-editor-empty';
          empty.dataset.founderPreviewEmpty = 'true';
          empty.innerHTML = '<iconify-icon icon="lucide:image" width="22"></iconify-icon><span>Upload or paste a founder photo to preview the crop.</span>';
          previewBox.appendChild(empty);
        }
        return null;
      }
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
    initHeroImageEditor();
    initFounderPhotoEditor();
  });
})();
