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
