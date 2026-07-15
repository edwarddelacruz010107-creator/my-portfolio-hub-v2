/* MyPortfolioHub dashboard shell interactions.
 * CSP-safe mobile drawer, delete modal, toast and notification helpers.
 */
(function () {
  'use strict';

  // Notification centers (including the legacy [data-notification-bell]
  // contract) are owned by notifications-v1.js so this shell never double-polls.

  var MOBILE_QUERY = '(max-width: 900px)';

  function isMobile() {
    return window.matchMedia ? window.matchMedia(MOBILE_QUERY).matches : window.innerWidth <= 900;
  }

  function initSidebarDrawer() {
    var sidebar = document.getElementById('sidebar');
    var overlay = document.getElementById('sidebarOverlay');
    var menuButton = document.getElementById('menuToggle');
    var closeButton = document.getElementById('sidebarClose');

    if (!sidebar || !overlay || !menuButton) return;

    var previouslyFocused = null;

    function setOpenState(open) {
      sidebar.classList.toggle('open', open);
      overlay.classList.toggle('show', open);
      overlay.classList.toggle('active', open);
      document.body.classList.toggle('sidebar-menu-open', open);
      menuButton.classList.toggle('is-active', open);
      menuButton.setAttribute('aria-expanded', open ? 'true' : 'false');
      menuButton.setAttribute('aria-label', open ? 'Close sidebar' : 'Open sidebar');

      if (isMobile()) {
        sidebar.setAttribute('aria-hidden', open ? 'false' : 'true');
        overlay.setAttribute('aria-hidden', open ? 'false' : 'true');
        document.documentElement.classList.toggle('drawer-scroll-locked', open);
      } else {
        sidebar.removeAttribute('aria-hidden');
        overlay.setAttribute('aria-hidden', 'true');
        document.documentElement.classList.remove('drawer-scroll-locked');
      }

      if (open) {
        previouslyFocused = document.activeElement;
        window.requestAnimationFrame(function () {
          var focusTarget = closeButton || sidebar.querySelector('a, button');
          if (focusTarget) focusTarget.focus({ preventScroll: true });
        });
      } else if (previouslyFocused && typeof previouslyFocused.focus === 'function') {
        previouslyFocused.focus({ preventScroll: true });
        previouslyFocused = null;
      }
    }

    function toggleDrawer(event) {
      if (event) {
        event.preventDefault();
        event.stopPropagation();
      }
      setOpenState(!sidebar.classList.contains('open'));
    }

    menuButton.addEventListener('click', toggleDrawer);

    if (closeButton) {
      closeButton.addEventListener('click', function (event) {
        event.preventDefault();
        setOpenState(false);
      });
    }

    overlay.addEventListener('click', function () { setOpenState(false); });

    sidebar.addEventListener('click', function (event) {
      if (!isMobile()) return;
      var link = event.target.closest('a[href]');
      if (link) setOpenState(false);
    });

    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && sidebar.classList.contains('open')) {
        setOpenState(false);
      }
    });

    function syncBreakpoint() {
      if (isMobile()) {
        if (!sidebar.classList.contains('open')) {
          sidebar.setAttribute('aria-hidden', 'true');
          overlay.setAttribute('aria-hidden', 'true');
        }
      } else {
        setOpenState(false);
        sidebar.removeAttribute('aria-hidden');
      }
    }

    window.addEventListener('resize', syncBreakpoint, { passive: true });
    window.addEventListener('orientationchange', function () {
      window.setTimeout(syncBreakpoint, 120);
    });

    setOpenState(false);
    syncBreakpoint();
  }

  function dismissToast(toast) {
    if (!toast) return;
    toast.classList.add('hiding');
    window.setTimeout(function () { toast.remove(); }, 380);
  }

  function initToasts() {
    document.addEventListener('click', function (event) {
      var close = event.target.closest('[data-toast-close], .toast-close, .flash-close');
      if (!close) return;
      var toast = close.closest('.toast, .flash, .toast-flash');
      if (!toast) return;
      event.preventDefault();
      dismissToast(toast);
    });

    document.querySelectorAll('.toast-flash, [data-auto-close]').forEach(function (toast) {
      window.setTimeout(function () { dismissToast(toast); }, 5000);
    });

    window.dismissToast = dismissToast;
  }

  function initDeleteModal() {
    var modal = document.getElementById('deleteModal');
    var form = document.getElementById('deleteModalForm');
    if (!modal || !form) return;

    var message = document.getElementById('deleteModalMsg') || document.getElementById('deleteModalBody');
    var dangerButton = modal.querySelector('.btn-danger');

    function isHidden() {
      if (typeof modal.showModal === 'function') return !modal.open;
      return modal.classList.contains('hidden') || modal.style.display === 'none';
    }

    function openModal(action, text) {
      if (action) form.action = action;
      if (message && text) message.textContent = text;
      if (modal.matches('[data-ui-dialog]') && window.MPHUI) {
        window.MPHUI.openDialog(modal, document.activeElement);
      } else if (typeof modal.showModal === 'function') {
        if (!modal.open) modal.showModal();
      } else {
        modal.classList.remove('hidden');
        modal.style.display = 'flex';
      }
      document.body.classList.add('modal-open');
      if (dangerButton) dangerButton.focus({ preventScroll: true });
    }

    function closeModal() {
      if (modal.matches('[data-ui-dialog]') && window.MPHUI) window.MPHUI.closeDialog(modal);
      else if (typeof modal.close === 'function' && modal.open) modal.close();
      else {
        modal.classList.add('hidden');
        modal.style.display = 'none';
      }
      document.body.classList.remove('modal-open');
    }

    window.confirmDelete = function (action, labelOrMessage) {
      var text = labelOrMessage || 'Delete this item? This action cannot be undone.';
      if (message && message.id === 'deleteModalBody' && labelOrMessage && !/delete|remove|cannot/i.test(labelOrMessage)) {
        text = 'This will permanently remove "' + labelOrMessage + '" and all associated data. This action cannot be undone.';
      }
      openModal(action, text);
    };
    window.closeDeleteModal = closeModal;

    document.addEventListener('click', function (event) {
      var trigger = event.target.closest('[data-confirm-delete]');
      if (trigger) {
        event.preventDefault();
        openModal(trigger.dataset.deleteAction || trigger.getAttribute('href'), trigger.dataset.deleteMessage);
        return;
      }
      if (event.target.closest('[data-close-delete-modal]')) {
        event.preventDefault();
        closeModal();
      }
    });

    modal.addEventListener('click', function (event) {
      if (event.target === modal) closeModal();
    });

    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && !isHidden()) closeModal();
    });
  }

  function init() {
    initSidebarDrawer();
    initToasts();
    initDeleteModal();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
