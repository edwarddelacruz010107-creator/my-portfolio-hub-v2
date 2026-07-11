(() => {
  'use strict';

  const app = document.getElementById('emailServicesApp');
  if (!app) return;

  const config = {
    testUrl: app.dataset.testUrl || '',
    toggleUrlTemplate: app.dataset.toggleUrlTemplate || '',
    priorityUrl: app.dataset.priorityUrl || '',
    statusUrl: app.dataset.statusUrl || '',
    csrfToken: app.dataset.csrfToken || '',
    defaultEmail: app.dataset.defaultEmail || '',
  };

  const toastContainer = document.getElementById('esToastContainer');
  const priorityList = document.getElementById('priorityList');
  const priorityForm = document.getElementById('providerPriorityForm');
  const priorityOrder = document.getElementById('providerOrder');
  const recipientInput = document.getElementById('emailTestRecipient');

  function toast(message, type = 'info', duration = 5000) {
    if (!toastContainer) return;
    const iconMap = {
      success: 'lucide:check-circle',
      error: 'lucide:x-circle',
      info: 'lucide:info',
    };
    const colorMap = {success: '#22c55e', error: '#ef4444', info: '#60a5fa'};
    const item = document.createElement('div');
    item.className = `es-toast ${type}`;
    const icon = document.createElement('iconify-icon');
    icon.setAttribute('icon', iconMap[type] || iconMap.info);
    icon.setAttribute('width', '18');
    icon.style.flexShrink = '0';
    icon.style.color = colorMap[type] || colorMap.info;
    const text = document.createElement('span');
    text.textContent = String(message || 'Request completed.');
    item.append(icon, text);
    toastContainer.appendChild(item);
    window.setTimeout(() => {
      item.classList.add('hide');
      window.setTimeout(() => item.remove(), 350);
    }, duration);
  }

  async function readJson(response) {
    const contentType = response.headers.get('content-type') || '';
    if (response.redirected || response.url.includes('/login')) {
      throw new Error('Your session expired. Refresh the page and sign in again.');
    }
    if (!contentType.includes('application/json')) {
      const body = await response.text();
      console.error('[EmailServices] Non-JSON response', response.status, body.slice(0, 500));
      throw new Error(response.status >= 500
        ? 'The server returned an error. Check the production logs.'
        : 'The request was rejected. Refresh the page and try again.');
    }
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.message || payload.error || `Request failed (${response.status}).`);
    }
    return payload;
  }

  async function postForm(url, formData) {
    const response = await fetch(url, {
      method: 'POST',
      body: formData,
      credentials: 'same-origin',
      headers: {
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json',
      },
    });
    return readJson(response);
  }

  function testRecipient() {
    const value = (recipientInput?.value || config.defaultEmail || '').trim();
    if (!value || !value.includes('@')) {
      recipientInput?.focus();
      throw new Error('Enter a valid test recipient email address.');
    }
    return value;
  }

  async function testProvider(provider, button) {
    const original = button?.innerHTML || '';
    if (button) {
      button.disabled = true;
      button.innerHTML = '<span class="es-spinner"></span> Sending test…';
    }
    try {
      const formData = new FormData();
      formData.append('provider', provider);
      formData.append('to_email', testRecipient());
      formData.append('csrf_token', config.csrfToken);
      const result = await postForm(config.testUrl, formData);
      if (!result.success) throw new Error(result.message || 'Provider test failed.');

      toast(`${result.message} (${result.latency}s) → ${result.to_email}`, 'success', 6500);
      const badge = document.getElementById(`statusBadge-${provider}`);
      if (badge) {
        badge.className = 'status-badge connected';
        badge.textContent = 'Connected';
      }
    } catch (error) {
      toast(error.message || 'The provider test failed.', 'error', 7500);
    } finally {
      if (button) {
        button.disabled = false;
        button.innerHTML = original;
      }
    }
  }

  async function toggleProvider(provider, checkbox) {
    const previous = !checkbox.checked;
    checkbox.disabled = true;
    try {
      const formData = new FormData();
      formData.append('action', checkbox.checked ? 'activate' : 'deactivate');
      formData.append('csrf_token', config.csrfToken);
      const url = config.toggleUrlTemplate.replace('__provider__', encodeURIComponent(provider));
      const result = await postForm(url, formData);
      if (!result.success) throw new Error(result.error || 'Provider toggle failed.');

      checkbox.checked = Boolean(result.active);
      document.getElementById(`providerCard-${provider}`)?.classList.toggle('active-provider', result.active);
      const label = document.getElementById(`toggleLabel-${provider}`);
      if (label) label.textContent = result.active ? 'Active' : 'Inactive';
      updatePriorityState();
      toast(`${provider.charAt(0).toUpperCase()}${provider.slice(1)} ${result.active ? 'activated' : 'deactivated'}.`, 'success');
    } catch (error) {
      checkbox.checked = previous;
      toast(error.message || 'Provider toggle failed.', 'error');
    } finally {
      checkbox.disabled = false;
    }
  }

  function priorityItems() {
    return priorityList ? Array.from(priorityList.querySelectorAll('.priority-item')) : [];
  }

  function syncPriority() {
    const items = priorityItems();
    items.forEach((item, index) => {
      const number = item.querySelector('.priority-num');
      if (number) number.textContent = String(index + 1);
      const up = item.querySelector('[data-priority-move="up"]');
      const down = item.querySelector('[data-priority-move="down"]');
      if (up) up.disabled = index === 0;
      if (down) down.disabled = index === items.length - 1;
    });
    if (priorityOrder) {
      priorityOrder.value = items.map((item) => item.dataset.provider).join(',');
    }
  }

  function updatePriorityState() {
    priorityItems().forEach((item) => {
      const provider = item.dataset.provider;
      const toggle = document.querySelector(`[data-provider-toggle="${provider}"]`);
      const dot = item.querySelector('.priority-dot');
      dot?.classList.toggle('active', Boolean(toggle?.checked));
      dot?.classList.toggle('inactive', !toggle?.checked);
    });
  }

  function movePriority(item, direction) {
    if (!priorityList || !item) return;
    if (direction === 'up' && item.previousElementSibling) {
      priorityList.insertBefore(item, item.previousElementSibling);
    } else if (direction === 'down' && item.nextElementSibling) {
      priorityList.insertBefore(item.nextElementSibling, item);
    }
    syncPriority();
  }

  async function savePriority(event) {
    event.preventDefault();
    syncPriority();
    const button = document.getElementById('savePriorityBtn');
    const original = button?.innerHTML || '';
    if (button) {
      button.disabled = true;
      button.innerHTML = '<span class="es-spinner"></span> Saving…';
    }
    try {
      const formData = new FormData(priorityForm);
      const result = await postForm(config.priorityUrl, formData);
      if (!result.success) throw new Error(result.error || 'Priority update failed.');
      toast('Provider priority saved.', 'success');
    } catch (error) {
      toast(error.message || 'Priority update failed.', 'error', 6500);
    } finally {
      if (button) {
        button.disabled = false;
        button.innerHTML = original;
      }
    }
  }

  document.addEventListener('click', (event) => {
    const testButton = event.target.closest('[data-test-provider]');
    if (testButton) {
      testProvider(testButton.dataset.testProvider, testButton);
      return;
    }

    const activeButton = event.target.closest('[data-test-active-provider]');
    if (activeButton) {
      const active = priorityItems().find((item) => {
        const toggle = document.querySelector(`[data-provider-toggle="${item.dataset.provider}"]`);
        return toggle?.checked;
      });
      if (!active) {
        toast('Activate and configure at least one provider first.', 'error');
        return;
      }
      testProvider(active.dataset.provider, activeButton);
      return;
    }

    const passwordButton = event.target.closest('[data-password-target]');
    if (passwordButton) {
      const input = document.getElementById(passwordButton.dataset.passwordTarget);
      if (!input) return;
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      passwordButton.querySelector('iconify-icon')?.setAttribute('icon', show ? 'lucide:eye-off' : 'lucide:eye');
      return;
    }

    const guideButton = event.target.closest('[data-guide-key]');
    if (guideButton) {
      const key = guideButton.dataset.guideKey;
      const content = document.getElementById(`guide-${key}`);
      const arrow = document.getElementById(`guideArrow-${key}`);
      const open = content?.classList.toggle('open') || false;
      guideButton.setAttribute('aria-expanded', String(open));
      if (arrow) arrow.style.transform = open ? 'rotate(180deg)' : '';
      return;
    }

    const moveButton = event.target.closest('[data-priority-move]');
    if (moveButton) {
      movePriority(moveButton.closest('.priority-item'), moveButton.dataset.priorityMove);
    }
  });

  document.addEventListener('change', (event) => {
    const toggle = event.target.closest('[data-provider-toggle]');
    if (toggle) toggleProvider(toggle.dataset.providerToggle, toggle);
  });

  priorityForm?.addEventListener('submit', savePriority);

  if (priorityList) {
    let dragged = null;
    priorityList.addEventListener('dragstart', (event) => {
      dragged = event.target.closest('.priority-item');
      if (!dragged) return;
      event.dataTransfer.effectAllowed = 'move';
      window.setTimeout(() => dragged?.classList.add('dragging'), 0);
    });
    priorityList.addEventListener('dragover', (event) => {
      event.preventDefault();
      const target = event.target.closest('.priority-item');
      if (!target || !dragged || target === dragged) return;
      const rect = target.getBoundingClientRect();
      const after = event.clientY > rect.top + rect.height / 2;
      priorityList.insertBefore(dragged, after ? target.nextSibling : target);
      syncPriority();
    });
    priorityList.addEventListener('dragend', () => {
      dragged?.classList.remove('dragging');
      dragged = null;
      syncPriority();
    });
  }

  syncPriority();
  updatePriorityState();
})();
