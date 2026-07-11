(() => {
  'use strict';

  const root = document.getElementById('emailSettingsApp');
  if (!root) return;

  const statusUrl = root.dataset.statusUrl;
  const diagnosticsUrl = root.dataset.diagnosticsUrl;
  const postUrl = root.dataset.postUrl || window.location.href;

  function getCsrf() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) return meta.content;
    const hidden = root.querySelector('input[name="csrf_token"]');
    return hidden ? hidden.value : '';
  }

  function toast(message, ok = true, duration = 4500) {
    const container = document.getElementById('es-toast');
    if (!container) return;
    const item = document.createElement('div');
    item.className = `toast-msg ${ok ? 'ok' : 'err'}`;
    item.setAttribute('role', 'status');
    item.textContent = `${ok ? '✓' : '✗'} ${message}`;
    container.appendChild(item);
    window.setTimeout(() => {
      item.style.opacity = '0';
      item.style.transition = 'opacity .3s';
      window.setTimeout(() => item.remove(), 320);
    }, duration);
  }

  function setButtonLoading(button, loading, loadingText = 'Working…') {
    if (!button) return;
    if (loading) {
      button.dataset.originalHtml = button.innerHTML;
      button.disabled = true;
      button.innerHTML = `<span class="spin" aria-hidden="true"></span> ${loadingText}`;
    } else {
      button.disabled = false;
      button.innerHTML = button.dataset.originalHtml || button.innerHTML;
      delete button.dataset.originalHtml;
    }
  }

  async function readJsonResponse(response) {
    if (response.redirected && response.url && !response.url.includes('/settings/email')) {
      window.location.assign(response.url);
      throw new Error('Your session expired. Redirecting to sign in.');
    }
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
      const text = await response.text();
      console.error('Unexpected email settings response:', text.slice(0, 1000));
      throw new Error(`Server returned an unexpected response (HTTP ${response.status}). Refresh and sign in again.`);
    }
    const payload = await response.json();
    if (!response.ok && !payload.message) payload.message = `Request failed (HTTP ${response.status}).`;
    return payload;
  }

  async function ajaxPost(formData, timeoutMs = 25000) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(postUrl, {
        method: 'POST',
        body: formData,
        credentials: 'same-origin',
        headers: {
          'Accept': 'application/json',
          'X-Requested-With': 'XMLHttpRequest'
        },
        signal: controller.signal
      });
      return await readJsonResponse(response);
    } catch (error) {
      if (error.name === 'AbortError') {
        throw new Error('Request timed out. SMTP connections may take a few seconds; please retry.');
      }
      throw error;
    } finally {
      window.clearTimeout(timer);
    }
  }

  function normalizeFormData(form, action) {
    const data = new FormData(form);
    data.set('action', action);
    data.set('csrf_token', getCsrf());
    return data;
  }

  function providerLabel(name) {
    return { mailersend: 'MailerSend', smtp: 'SMTP', resend: 'Resend' }[name] || name;
  }

  function ensureProviderPill(name) {
    let pill = root.querySelector(`[data-pill="${name}"]`);
    if (pill) return pill;
    const refresh = document.getElementById('refreshBtn');
    pill = document.createElement('span');
    pill.dataset.pill = name;
    pill.className = 'pill gray';
    pill.hidden = true;
    refresh?.parentElement?.insertBefore(pill, refresh);
    return pill;
  }

  function updatePillBar(name, info) {
    const pill = ensureProviderPill(name);
    if (info.configured === false) {
      pill.hidden = true;
      return;
    }
    if (info.configured === null && pill.hidden) return;
    pill.hidden = false;
    pill.className = `pill ${info.active ? 'green' : 'gray'}`;
    pill.textContent = info.active
      ? `✓ ${providerLabel(name)} active`
      : `◎ ${providerLabel(name)} configured, inactive`;
  }

  function updateProviderUI(name, info) {
    if (info.configured !== null && typeof info.configured !== 'undefined') {
      root.querySelectorAll(`[data-provider-badge="${name}"]`).forEach((badge) => {
        if (info.configured) {
          badge.className = `status-badge ${info.active ? 'ok' : 'off'}`;
          badge.textContent = info.active ? '✓ Configured & Active' : '✓ Configured (inactive)';
        } else {
          badge.className = 'status-badge warn';
          badge.textContent = 'Not configured';
        }
      });
    }

    const item = root.querySelector(`.priority-item[data-provider="${name}"]`);
    if (item) {
      const dot = item.querySelector('.priority-dot');
      const badge = item.querySelector('[data-priority-badge]');
      if (dot) dot.className = `priority-dot ${info.active ? 'active' : 'inactive'}`;
      if (badge && info.configured !== null && typeof info.configured !== 'undefined') {
        if (info.configured) {
          badge.className = `status-badge ${info.active ? 'ok' : 'off'}`;
          badge.textContent = info.active ? '✓ Active' : '◎ Inactive';
        } else {
          badge.className = 'status-badge warn';
          badge.textContent = 'Not configured';
        }
      }
    }
    updatePillBar(name, info);
  }

  async function refreshProviderStatus(showToast = false) {
    const icon = document.getElementById('refreshIcon');
    if (icon) icon.style.animation = 'spin .6s linear infinite';
    try {
      const response = await fetch(statusUrl, {
        cache: 'no-store', credentials: 'same-origin', headers: { 'Accept': 'application/json' }
      });
      const data = await readJsonResponse(response);
      if (!data.ok) throw new Error(data.message || 'Status refresh failed.');
      ['mailersend', 'smtp', 'resend'].forEach((name) => {
        if (data.providers?.[name]) updateProviderUI(name, data.providers[name]);
        const checkbox = root.querySelector(`[data-provider-toggle="${name}"]`);
        if (checkbox && data.providers?.[name]) checkbox.checked = Boolean(data.providers[name].active);
      });
      if (showToast) toast('Provider status refreshed.');
    } catch (error) {
      if (showToast) toast(error.message || 'Could not refresh provider status.', false);
    } finally {
      if (icon) icon.style.animation = '';
    }
  }

  function renumberPriority() {
    root.querySelectorAll('.priority-item').forEach((item, index) => {
      const number = item.querySelector('.priority-num');
      if (number) number.textContent = String(index + 1);
    });
  }

  function initPriorityDrag() {
    let dragged = null;
    root.querySelectorAll('.priority-item').forEach((item) => {
      item.addEventListener('dragstart', () => {
        dragged = item;
        window.setTimeout(() => item.classList.add('dragging'), 0);
      });
      item.addEventListener('dragend', () => {
        item.classList.remove('dragging');
        root.querySelectorAll('.priority-item').forEach((node) => node.classList.remove('drag-over'));
        renumberPriority();
      });
      item.addEventListener('dragover', (event) => {
        event.preventDefault();
        root.querySelectorAll('.priority-item').forEach((node) => node.classList.remove('drag-over'));
        if (item !== dragged) item.classList.add('drag-over');
      });
      item.addEventListener('drop', (event) => {
        event.preventDefault();
        if (!dragged || item === dragged) return;
        const list = document.getElementById('priorityList');
        const items = [...list.querySelectorAll('.priority-item')];
        const from = items.indexOf(dragged);
        const to = items.indexOf(item);
        list.insertBefore(dragged, from < to ? item.nextSibling : item);
        renumberPriority();
      });
    });
  }

  async function savePriority(button) {
    const order = [...root.querySelectorAll('.priority-item')].map((item) => item.dataset.provider);
    const data = new FormData();
    data.set('csrf_token', getCsrf());
    data.set('action', 'save_priority');
    data.set('priority_order', JSON.stringify(order));
    setButtonLoading(button, true, 'Saving…');
    try {
      const result = await ajaxPost(data);
      toast(result.message || (result.ok ? 'Priority saved.' : 'Priority save failed.'), Boolean(result.ok));
    } catch (error) {
      toast(error.message || 'Priority save failed.', false);
    } finally {
      setButtonLoading(button, false);
    }
  }

  async function toggleProvider(checkbox) {
    const name = checkbox.dataset.providerToggle;
    const active = checkbox.checked;
    const data = new FormData();
    data.set('csrf_token', getCsrf());
    data.set('action', 'toggle_provider');
    data.set('provider', name);
    data.set('active', active ? '1' : '0');
    checkbox.disabled = true;
    updateProviderUI(name, { active, configured: null });
    try {
      const result = await ajaxPost(data);
      if (!result.ok) throw new Error(result.message || 'Provider toggle failed.');
      toast(result.message || `${providerLabel(name)} updated.`);
      await refreshProviderStatus(false);
    } catch (error) {
      checkbox.checked = !active;
      updateProviderUI(name, { active: !active, configured: null });
      toast(error.message || 'Provider toggle failed.', false);
    } finally {
      checkbox.disabled = false;
    }
  }

  async function validateMailerSend(button) {
    const data = new FormData();
    data.set('csrf_token', getCsrf());
    data.set('action', 'validate_mailersend_key');
    data.set('mailersend_api_key', document.getElementById('msKey')?.value.trim() || '');
    setButtonLoading(button, true, 'Validating…');
    try {
      const result = await ajaxPost(data, 30000);
      toast(result.message || 'Validation finished.', Boolean(result.ok));
      if (result.ok) await refreshProviderStatus(false);
    } catch (error) {
      toast(error.message || 'MailerSend validation failed.', false);
    } finally {
      setButtonLoading(button, false);
    }
  }

  async function validateResend(button) {
    const data = new FormData();
    data.set('csrf_token', getCsrf());
    data.set('action', 'validate_resend');
    data.set('resend_api_key', document.getElementById('resendKey')?.value.trim() || '');
    setButtonLoading(button, true, 'Validating…');
    try {
      const result = await ajaxPost(data, 30000);
      toast(result.message || 'Validation finished.', Boolean(result.ok));
      if (result.ok) await refreshProviderStatus(false);
    } catch (error) {
      toast(error.message || 'Resend validation failed.', false);
    } finally {
      setButtonLoading(button, false);
    }
  }

  async function validateSMTP(button) {
    const panel = document.getElementById('smtpTestResult');
    const message = document.getElementById('smtpTestMsg');
    const icon = document.getElementById('smtpTestIcon');
    if (panel) panel.className = 'smtp-result';
    const form = document.getElementById('smtpForm');
    const data = normalizeFormData(form, 'validate_smtp');
    setButtonLoading(button, true, 'Testing…');
    try {
      const result = await ajaxPost(data, 40000);
      if (panel) panel.className = `smtp-result show ${result.ok ? 'ok' : 'err'}`;
      if (icon) icon.textContent = result.ok ? '✓' : '✗';
      if (message) message.textContent = result.message || 'SMTP test completed.';
      toast(result.message || 'SMTP test completed.', Boolean(result.ok));
    } catch (error) {
      if (panel) panel.className = 'smtp-result show err';
      if (icon) icon.textContent = '✗';
      if (message) message.textContent = error.message || 'SMTP connection failed.';
      toast(error.message || 'SMTP connection failed.', false);
    } finally {
      setButtonLoading(button, false);
    }
  }

  async function saveProviderForm(form, button) {
    const action = form.querySelector('input[name="action"]')?.value;
    const labels = {
      save_mailersend: 'MailerSend', save_smtp: 'SMTP', save_resend: 'Resend', save_otp: 'OTP'
    };
    setButtonLoading(button, true, 'Saving…');
    try {
      const result = await ajaxPost(normalizeFormData(form, action), action === 'save_smtp' ? 35000 : 25000);
      toast(result.message || `${labels[action] || 'Settings'} saved.`, Boolean(result.ok));
      if (!result.ok) return;
      if (result.providers) {
        Object.entries(result.providers).forEach(([name, info]) => updateProviderUI(name, info));
      }
      if (action === 'save_mailersend') {
        const field = document.getElementById('msKey');
        if (field) { field.value = ''; field.placeholder = '(set — enter new value to replace)'; }
      }
      if (action === 'save_resend') {
        const field = document.getElementById('resendKey');
        if (field) { field.value = ''; field.placeholder = '(set — enter new value to replace)'; }
      }
      if (action === 'save_smtp') {
        const field = document.getElementById('smtpPass');
        if (field) { field.value = ''; field.placeholder = '(set — enter new value to replace)'; }
      }
      await refreshProviderStatus(false);
    } catch (error) {
      toast(error.message || `${labels[action] || 'Settings'} could not be saved.`, false);
    } finally {
      setButtonLoading(button, false);
    }
  }

  async function sendTestEmail(button) {
    const field = document.getElementById('testEmailInput');
    const email = field?.value.trim() || '';
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) {
      field?.focus();
      toast('Enter a valid test recipient email address.', false);
      return;
    }
    const data = new FormData();
    data.set('csrf_token', getCsrf());
    data.set('action', 'send_test_email');
    data.set('test_email', email);
    setButtonLoading(button, true, 'Sending…');
    try {
      const result = await ajaxPost(data, 45000);
      toast(result.message || 'Test email completed.', Boolean(result.ok), 6500);
    } catch (error) {
      toast(error.message || 'Test email failed.', false, 6500);
    } finally {
      setButtonLoading(button, false);
    }
  }

  async function runDiagnostics(button) {
    const output = document.getElementById('diagOutput');
    setButtonLoading(button, true, 'Running…');
    if (output) output.hidden = true;
    try {
      const response = await fetch(diagnosticsUrl, {
        credentials: 'same-origin', cache: 'no-store', headers: { 'Accept': 'application/json' }
      });
      const result = await readJsonResponse(response);
      if (output) {
        output.textContent = JSON.stringify(result, null, 2);
        output.hidden = false;
        output.style.display = 'block';
      }
    } catch (error) {
      if (output) {
        output.textContent = `Diagnostics request failed: ${error.message || 'network error'}`;
        output.hidden = false;
        output.style.display = 'block';
      }
    } finally {
      setButtonLoading(button, false);
    }
  }

  function applyGmailDefaults() {
    const host = document.getElementById('smtpHost');
    const port = document.getElementById('smtpPort');
    const username = document.getElementById('smtpUser');
    const sender = document.getElementById('smtpSenderEmail');
    const encryption = root.querySelector('[name="smtp_encryption"]');
    if (host) host.value = 'smtp.gmail.com';
    if (port) port.value = '587';
    if (encryption) encryption.value = 'tls';
    if (sender && !sender.value.trim() && username?.value.trim()) sender.value = username.value.trim();
    toast('Gmail defaults applied. Enter your Gmail address and 16-character App Password.');
  }

  function togglePassword(button) {
    const input = document.getElementById(button.dataset.passwordTarget);
    if (!input) return;
    const showing = input.type === 'text';
    input.type = showing ? 'password' : 'text';
    button.textContent = showing ? 'Show' : 'Hide';
    button.setAttribute('aria-pressed', String(!showing));
  }

  root.addEventListener('submit', (event) => {
    const form = event.target.closest('[data-email-form]');
    if (!form) return;
    event.preventDefault();
    if (!form.reportValidity()) return;
    const button = form.querySelector('[type="submit"]');
    saveProviderForm(form, button);
  });

  root.addEventListener('click', (event) => {
    const button = event.target.closest('[data-email-action]');
    if (!button) return;
    const action = button.dataset.emailAction;
    const handlers = {
      refresh: () => refreshProviderStatus(true),
      'save-priority': () => savePriority(button),
      'send-test': () => sendTestEmail(button),
      'validate-mailersend': () => validateMailerSend(button),
      'validate-smtp': () => validateSMTP(button),
      'validate-resend': () => validateResend(button),
      diagnostics: () => runDiagnostics(button),
      'gmail-defaults': applyGmailDefaults,
      'toggle-password': () => togglePassword(button)
    };
    handlers[action]?.();
  });

  root.addEventListener('change', (event) => {
    const toggle = event.target.closest('[data-provider-toggle]');
    if (toggle) toggleProvider(toggle);
    if (event.target.id === 'smtpUser') {
      const sender = document.getElementById('smtpSenderEmail');
      if (sender && !sender.value.trim()) sender.value = event.target.value.trim();
    }
  });

  initPriorityDrag();
  refreshProviderStatus(false);
})();
