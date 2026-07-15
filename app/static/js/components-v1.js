(function () {
  'use strict';

  var allowedVariants = ['info', 'success', 'warning', 'danger', 'error'];
  var returnFocus = new WeakMap();

  function ready(callback) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', callback, { once: true });
    } else {
      callback();
    }
  }

  function focusables(root) {
    return Array.prototype.slice.call(root.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'
    )).filter(function (element) {
      return !element.hidden && element.getAttribute('aria-hidden') !== 'true';
    });
  }

  function openDialog(dialog, opener) {
    if (!dialog) return;
    returnFocus.set(dialog, opener || document.activeElement);
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
    document.documentElement.classList.add('ui-overlay-open');
    (window.requestAnimationFrame || function (callback) { window.setTimeout(callback, 0); })(function () {
      var target = dialog.querySelector('[autofocus]') || focusables(dialog)[0];
      if (target) target.focus({ preventScroll: true });
    });
  }

  function closeDialog(dialog) {
    if (!dialog) return;
    if (typeof dialog.close === 'function' && dialog.open) dialog.close();
    else dialog.removeAttribute('open');
    if (!document.querySelector('[data-ui-dialog][open]')) {
      document.documentElement.classList.remove('ui-overlay-open');
    }
    var target = returnFocus.get(dialog);
    if (target && document.contains(target) && typeof target.focus === 'function') {
      target.focus({ preventScroll: true });
    }
    returnFocus.delete(dialog);
  }

  function dismiss(element) {
    var dismissible = element && element.closest('.ui-toast, .ui-alert, [data-ui-dismissible]');
    if (!dismissible) return;
    dismissible.setAttribute('data-ui-leaving', 'true');
    window.setTimeout(function () { dismissible.remove(); }, 180);
  }

  function notify(message, variant, duration) {
    var safeVariant = allowedVariants.indexOf(variant) >= 0 ? variant : 'info';
    var region = document.querySelector('.ui-toast-region');
    if (!region) {
      region = document.createElement('div');
      region.className = 'ui-toast-region';
      region.setAttribute('aria-live', 'polite');
      region.setAttribute('aria-relevant', 'additions');
      document.body.appendChild(region);
    }
    var toast = document.createElement('div');
    toast.className = 'ui-toast ui-toast--' + safeVariant;
    toast.setAttribute('role', safeVariant === 'danger' || safeVariant === 'error' ? 'alert' : 'status');
    var text = document.createElement('span');
    text.textContent = String(message == null ? '' : message);
    var close = document.createElement('button');
    close.type = 'button';
    close.setAttribute('data-ui-dismiss', '');
    close.setAttribute('aria-label', 'Dismiss notification');
    close.textContent = '×';
    toast.appendChild(text);
    toast.appendChild(close);
    region.appendChild(toast);
    window.setTimeout(function () { dismiss(toast); }, Number(duration) > 0 ? Number(duration) : 4500);
    return toast;
  }

  function initDialogs() {
    document.addEventListener('click', function (event) {
      var opener = event.target.closest('[data-ui-dialog-open]');
      if (opener) {
        event.preventDefault();
        openDialog(document.getElementById(opener.getAttribute('data-ui-dialog-open')), opener);
        return;
      }
      var closer = event.target.closest('[data-ui-dialog-close]');
      if (closer) {
        event.preventDefault();
        closeDialog(closer.closest('[data-ui-dialog]'));
      }
    });

    document.querySelectorAll('[data-ui-dialog]').forEach(function (dialog) {
      dialog.addEventListener('cancel', function (event) {
        event.preventDefault();
        closeDialog(dialog);
      });
      dialog.addEventListener('click', function (event) {
        if (event.target !== dialog) return;
        var bounds = dialog.querySelector('.ui-dialog__surface, .ui-drawer__surface');
        if (!bounds) closeDialog(dialog);
        else {
          var rect = bounds.getBoundingClientRect();
          var outside = event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
          if (outside) closeDialog(dialog);
        }
      });
      dialog.addEventListener('keydown', function (event) {
        if (event.key !== 'Tab') return;
        var items = focusables(dialog);
        if (!items.length) return;
        var first = items[0];
        var last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      });
    });
  }

  function initDropdowns() {
    document.addEventListener('click', function (event) {
      var toggle = event.target.closest('[data-ui-dropdown-toggle]');
      if (toggle) {
        event.preventDefault();
        var owner = toggle.closest('[data-ui-dropdown]');
        var menu = owner && owner.querySelector('.ui-dropdown__menu');
        if (!menu) return;
        var open = toggle.getAttribute('aria-expanded') !== 'true';
        document.querySelectorAll('[data-ui-dropdown-toggle][aria-expanded="true"]').forEach(function (other) {
          if (other !== toggle) {
            other.setAttribute('aria-expanded', 'false');
            var otherMenu = other.closest('[data-ui-dropdown]').querySelector('.ui-dropdown__menu');
            otherMenu.hidden = true;
          }
        });
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        menu.hidden = !open;
        if (open) {
          var first = focusables(menu)[0];
          if (first) first.focus();
        }
        return;
      }
      if (!event.target.closest('[data-ui-dropdown]')) {
        document.querySelectorAll('[data-ui-dropdown-toggle][aria-expanded="true"]').forEach(function (button) {
          button.setAttribute('aria-expanded', 'false');
          button.closest('[data-ui-dropdown]').querySelector('.ui-dropdown__menu').hidden = true;
        });
      }
    });
    document.addEventListener('keydown', function (event) {
      var menu = event.target.closest('.ui-dropdown__menu');
      if (!menu) return;
      var items = focusables(menu);
      var index = items.indexOf(document.activeElement);
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        var change = event.key === 'ArrowDown' ? 1 : -1;
        items[(index + change + items.length) % items.length].focus();
      } else if (event.key === 'Escape') {
        event.preventDefault();
        var owner = menu.closest('[data-ui-dropdown]');
        var toggle = owner.querySelector('[data-ui-dropdown-toggle]');
        menu.hidden = true;
        toggle.setAttribute('aria-expanded', 'false');
        toggle.focus();
      }
    });
  }

  function selectTab(tab) {
    var group = tab.closest('[data-ui-tabs]');
    group.querySelectorAll('[role="tab"]').forEach(function (candidate) {
      var selected = candidate === tab;
      candidate.setAttribute('aria-selected', selected ? 'true' : 'false');
      candidate.tabIndex = selected ? 0 : -1;
      var panel = document.getElementById(candidate.getAttribute('aria-controls'));
      if (panel) panel.hidden = !selected;
    });
    tab.focus();
  }

  function initTabs() {
    document.addEventListener('click', function (event) {
      var tab = event.target.closest('[data-ui-tabs] [role="tab"]');
      if (tab) selectTab(tab);
    });
    document.addEventListener('keydown', function (event) {
      var tab = event.target.closest('[data-ui-tabs] [role="tab"]');
      if (!tab || ['ArrowLeft', 'ArrowRight', 'Home', 'End'].indexOf(event.key) < 0) return;
      event.preventDefault();
      var tabs = Array.prototype.slice.call(tab.closest('[role="tablist"]').querySelectorAll('[role="tab"]'));
      var index = tabs.indexOf(tab);
      if (event.key === 'Home') index = 0;
      else if (event.key === 'End') index = tabs.length - 1;
      else index = (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      selectTab(tabs[index]);
    });
  }

  function initInputs() {
    document.addEventListener('click', function (event) {
      var passwordToggle = event.target.closest('[data-ui-password-toggle]');
      if (passwordToggle) {
        var input = document.getElementById(passwordToggle.getAttribute('data-ui-password-toggle'));
        if (!input) return;
        var visible = input.type === 'text';
        input.type = visible ? 'password' : 'text';
        passwordToggle.setAttribute('aria-pressed', visible ? 'false' : 'true');
        passwordToggle.setAttribute('aria-label', visible ? 'Show password' : 'Hide password');
        return;
      }
      var switchButton = event.target.closest('[data-ui-switch]');
      if (switchButton) {
        var checked = switchButton.getAttribute('aria-checked') !== 'true';
        switchButton.setAttribute('aria-checked', checked ? 'true' : 'false');
        var hidden = switchButton.parentElement.querySelector('[data-ui-switch-value]');
        if (hidden) hidden.value = checked ? '1' : '0';
      }
    });
    document.addEventListener('change', function (event) {
      if (!event.target.matches('[data-ui-file]')) return;
      var output = event.target.parentElement.querySelector('[data-ui-file-label]');
      if (!output) return;
      var count = event.target.files ? event.target.files.length : 0;
      output.textContent = count ? (count === 1 ? event.target.files[0].name : count + ' files selected') : 'Choose a file.';
    });
  }

  function initDismissibles() {
    document.addEventListener('click', function (event) {
      var close = event.target.closest('[data-ui-dismiss]');
      if (close) {
        event.preventDefault();
        dismiss(close);
      }
    });
    document.querySelectorAll('[data-ui-auto-close]').forEach(function (element) {
      var delay = Number(element.getAttribute('data-ui-auto-close')) || 5000;
      window.setTimeout(function () { dismiss(element); }, delay);
    });
  }

  function initCommandSearch() {
    document.addEventListener('input', function (event) {
      if (!event.target.matches('[data-ui-command-query]')) return;
      var dialog = event.target.closest('[data-ui-dialog]');
      var query = event.target.value.toLocaleLowerCase();
      dialog.querySelectorAll('[data-ui-command-item]').forEach(function (item) {
        item.hidden = query && item.textContent.toLocaleLowerCase().indexOf(query) < 0;
      });
    });
  }

  function initMobileNav() {
    document.addEventListener('click', function (event) {
      var toggle = event.target.closest('[data-ui-mobile-nav]');
      if (!toggle) return;
      var target = document.getElementById(toggle.getAttribute('aria-controls'));
      if (!target) return;
      var expanded = toggle.getAttribute('aria-expanded') !== 'true';
      toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
      target.hidden = !expanded;
    });
  }

  function initSubmitGuards() {
    document.querySelectorAll('form[data-ui-submit-guard]').forEach(function (form) {
      form.addEventListener('submit', function () {
        form.setAttribute('aria-busy', 'true');
        form.querySelectorAll('[type="submit"]').forEach(function (button) {
          button.disabled = true;
          button.classList.add('is-loading');
        });
      });
    });
  }

  window.MPHUI = Object.freeze({
    closeDialog: closeDialog,
    notify: notify,
    openDialog: openDialog
  });

  ready(function () {
    initDialogs();
    initDropdowns();
    initTabs();
    initInputs();
    initDismissibles();
    initCommandSearch();
    initMobileNav();
    initSubmitGuards();
  });
})();
