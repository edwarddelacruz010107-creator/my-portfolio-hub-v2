(() => {
  'use strict';
  const modal = document.getElementById('disable2faModal');
  const openButton = document.querySelector('[data-open-disable-2fa]');
  const closeButtons = document.querySelectorAll('[data-close-disable-2fa]');
  const open = () => {
    if (!modal) return;
    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    modal.querySelector('input')?.focus();
  };
  const close = () => {
    if (!modal) return;
    modal.classList.add('hidden');
    document.body.style.overflow = '';
    openButton?.focus();
  };
  openButton?.addEventListener('click', open);
  closeButtons.forEach((button) => button.addEventListener('click', close));
  modal?.addEventListener('click', (event) => { if (event.target === modal) close(); });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && modal && !modal.classList.contains('hidden')) close(); });
})();

// Region and currency preferences
(() => {
  const form = document.getElementById('regionCurrencyForm');
  if (!form) return;
  const country = document.getElementById('country_code');
  const currency = document.getElementById('preferred_currency');
  const automatic = document.getElementById('auto_currency');
  const countryPreview = document.getElementById('regionCountryPreview');
  const currencyPreview = document.getElementById('regionCurrencyPreview');

  const sync = () => {
    const selected = country.options[country.selectedIndex];
    const suggested = selected?.dataset.currency || 'USD';
    currency.disabled = automatic.checked;
    if (automatic.checked) currency.value = suggested;
    if (countryPreview) countryPreview.textContent = selected?.textContent?.trim() || 'Not set';
    if (currencyPreview) currencyPreview.textContent = currency.value || suggested;
  };
  country.addEventListener('change', sync);
  automatic.addEventListener('change', sync);
  currency.addEventListener('change', sync);
  form.addEventListener('submit', () => { currency.disabled = false; });
  sync();
})();
