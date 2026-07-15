(function () {
  'use strict';

  var MIN_DELAY = 30000;
  var MAX_DELAY = 300000;

  function safeActionUrl(value) {
    if (!value) return null;
    try {
      var candidate = new URL(String(value), window.location.origin);
      if (candidate.origin !== window.location.origin || candidate.protocol !== window.location.protocol) return null;
      return candidate.pathname + candidate.search + candidate.hash;
    } catch (error) {
      return null;
    }
  }

  function renderEmpty(container, message) {
    container.replaceChildren();
    var paragraph = document.createElement('p');
    paragraph.className = 'ui-notification-preview__empty';
    paragraph.textContent = message;
    container.appendChild(paragraph);
  }

  function renderItems(container, notifications) {
    container.replaceChildren();
    if (!Array.isArray(notifications) || notifications.length === 0) {
      renderEmpty(container, 'No notifications yet.');
      return;
    }
    notifications.slice(0, 5).forEach(function (notification) {
      var action = safeActionUrl(notification.action_url);
      var item = document.createElement(action ? 'a' : 'div');
      item.className = 'ui-notification-preview__item';
      if (action) item.href = action;
      if (!notification.is_read) item.classList.add('is-unread');

      var title = document.createElement('strong');
      title.textContent = String(notification.title || 'Notification');
      var message = document.createElement('span');
      message.textContent = String(notification.message || '');
      var time = document.createElement('small');
      var created = new Date(notification.created_at);
      time.textContent = Number.isNaN(created.getTime()) ? '' : created.toLocaleString();
      item.appendChild(title);
      item.appendChild(message);
      item.appendChild(time);
      container.appendChild(item);
    });
  }

  function updateCount(center, count) {
    var normalized = Math.max(0, Number(count) || 0);
    var badge = center.querySelector('[data-notification-badge]');
    var toggle = center.querySelector('[data-notification-toggle]');
    if (badge) {
      badge.textContent = normalized > 99 ? '99+' : String(normalized);
      badge.classList.toggle('is-hidden', normalized === 0);
    }
    if (toggle) {
      toggle.setAttribute('aria-label', normalized ? 'Notifications, ' + normalized + ' unread' : 'Notifications');
    }
  }

  function initCenter(center) {
    var url = center.dataset.feedUrl;
    var container = center.querySelector('[data-notification-items]');
    if (!url || !container) return;
    var etag = null;
    var lastPayload = null;
    var delay = MIN_DELAY;
    var timer = null;
    var stopped = false;

    function schedule() {
      window.clearTimeout(timer);
      if (!stopped) timer = window.setTimeout(refresh, delay);
    }

    function refresh() {
      if (document.visibilityState === 'hidden') {
        schedule();
        return Promise.resolve();
      }
      var headers = { Accept: 'application/json' };
      if (etag) headers['If-None-Match'] = etag;
      return window.fetch(url, { credentials: 'same-origin', headers: headers })
        .then(function (response) {
          if (response.status === 304) return { notModified: true };
          if (!response.ok) throw new Error('Notification feed request failed');
          etag = response.headers.get('ETag') || etag;
          return response.json();
        })
        .then(function (payload) {
          delay = MIN_DELAY;
          center.removeAttribute('data-notification-error');
          if (payload && payload.notModified) {
            if (lastPayload) {
              updateCount(center, lastPayload.unread_count);
              renderItems(container, lastPayload.notifications);
            }
          } else if (payload) {
            lastPayload = payload;
            updateCount(center, payload.unread_count);
            renderItems(container, payload.notifications);
          }
        })
        .catch(function () {
          center.setAttribute('data-notification-error', 'true');
          renderEmpty(container, 'Notifications are temporarily unavailable. Open the full page to retry.');
          delay = Math.min(MAX_DELAY, delay * 2);
        })
        .then(schedule);
    }

    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible') {
        delay = MIN_DELAY;
        refresh();
      }
    });
    window.addEventListener('pagehide', function () {
      stopped = true;
      window.clearTimeout(timer);
    }, { once: true });
    refresh();
  }

  function init() {
    document.querySelectorAll('[data-notification-center]').forEach(initCenter);
  }

  window.MPHNotifications = Object.freeze({ safeActionUrl: safeActionUrl });
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
