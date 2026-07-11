(() => {
  'use strict';

  const root = document.documentElement;
  const themeButton = document.getElementById('oauthThemeToggle');
  const applyTheme = (theme) => {
    root.setAttribute('data-theme', theme);
    if (themeButton) {
      themeButton.setAttribute('aria-pressed', theme === 'light' ? 'true' : 'false');
      themeButton.setAttribute('aria-label', `Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`);
    }
    try { localStorage.setItem('phPublicTheme', theme); } catch (_) { /* storage may be disabled */ }
  };

  if (themeButton) {
    applyTheme(root.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
    themeButton.addEventListener('click', () => {
      applyTheme(root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
    });
  }

  document.querySelectorAll('[data-password-toggle]').forEach((button) => {
    button.addEventListener('click', () => {
      const key = button.getAttribute('data-password-toggle');
      const input = document.querySelector(`[data-password-input="${key}"]`);
      if (!input) return;
      const showing = input.type === 'text';
      input.type = showing ? 'password' : 'text';
      button.textContent = showing ? 'Show' : 'Hide';
      button.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
    });
  });

  const password = document.querySelector('[data-password-input="password"]');
  const confirm = document.querySelector('[data-password-input="confirm"]');
  const strengthBar = document.querySelector('[data-strength-bar]');
  const strengthLabel = document.querySelector('[data-strength-label]');
  const matchLabel = document.querySelector('[data-password-match]');

  const updateStrength = () => {
    if (!password || !strengthBar || !strengthLabel) return;
    const value = password.value;
    let score = 0;
    if (value.length >= 8) score += 1;
    if (value.length >= 12) score += 1;
    if (/[a-z]/.test(value) && /[A-Z]/.test(value)) score += 1;
    if (/\d/.test(value)) score += 1;
    if (/[^A-Za-z0-9]/.test(value)) score += 1;
    const states = [
      ['0%', '#ef4444', 'Use a strong password that meets the platform security rules.'],
      ['24%', '#ef4444', 'Password strength: weak'],
      ['45%', '#f59e0b', 'Password strength: fair'],
      ['65%', '#eab308', 'Password strength: good'],
      ['82%', '#22c55e', 'Password strength: strong'],
      ['100%', '#10b981', 'Password strength: excellent'],
    ];
    const state = value ? states[score] : states[0];
    strengthBar.style.width = state[0];
    strengthBar.style.backgroundColor = state[1];
    strengthLabel.textContent = state[2];
  };

  const updateMatch = () => {
    if (!confirm || !matchLabel) return;
    matchLabel.classList.remove('is-match', 'is-mismatch');
    if (!confirm.value) {
      matchLabel.textContent = '';
      return;
    }
    const matches = Boolean(password && password.value === confirm.value);
    matchLabel.textContent = matches ? 'Passwords match.' : 'Passwords do not match yet.';
    matchLabel.classList.add(matches ? 'is-match' : 'is-mismatch');
  };

  if (password) password.addEventListener('input', () => { updateStrength(); updateMatch(); });
  if (confirm) confirm.addEventListener('input', updateMatch);

  const form = document.getElementById('oauthSetupForm');
  if (form) {
    form.addEventListener('submit', () => {
      const submit = form.querySelector('input[type="submit"], button[type="submit"]');
      if (!submit || !form.checkValidity()) return;
      submit.disabled = true;
      if ('value' in submit) submit.value = 'Finishing setup…';
      else submit.textContent = 'Finishing setup…';
    });
  }
})();
