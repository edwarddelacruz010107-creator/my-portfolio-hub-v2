(function () {
  'use strict';

  const menus = Array.from(document.querySelectorAll('.tenant-action-menu'));

  function resetPopover(menu) {
    const popover = menu && menu.querySelector('.tenant-action-popover');
    if (!popover) return;
    popover.classList.remove('tenant-action-popover--fixed');
    popover.style.removeProperty('top');
    popover.style.removeProperty('left');
    popover.style.removeProperty('right');
    popover.style.removeProperty('max-height');
  }

  function placePopover(menu) {
    const summary = menu.querySelector('summary');
    const popover = menu.querySelector('.tenant-action-popover');
    if (!summary || !popover) return;

    popover.classList.add('tenant-action-popover--fixed');
    const trigger = summary.getBoundingClientRect();
    const gap = 8;
    const margin = 12;
    const width = Math.min(260, window.innerWidth - margin * 2);
    popover.style.width = width + 'px';
    popover.style.maxHeight = Math.max(220, window.innerHeight - margin * 2) + 'px';

    const measured = popover.getBoundingClientRect();
    let left = trigger.right - width;
    left = Math.max(margin, Math.min(left, window.innerWidth - width - margin));

    const roomBelow = window.innerHeight - trigger.bottom - margin;
    const roomAbove = trigger.top - margin;
    let top;
    if (roomBelow >= measured.height || roomBelow >= roomAbove) {
      top = Math.min(trigger.bottom + gap, window.innerHeight - measured.height - margin);
    } else {
      top = Math.max(margin, trigger.top - measured.height - gap);
    }
    popover.style.left = left + 'px';
    popover.style.top = Math.max(margin, top) + 'px';
  }

  function closeMenus(except) {
    menus.forEach((menu) => {
      if (menu !== except) {
        menu.removeAttribute('open');
        resetPopover(menu);
      }
    });
  }

  menus.forEach((menu) => {
    menu.addEventListener('toggle', () => {
      if (menu.open) {
        closeMenus(menu);
        requestAnimationFrame(() => placePopover(menu));
      } else {
        resetPopover(menu);
      }
    });
  });

  document.addEventListener('click', (event) => {
    if (!event.target.closest('.tenant-action-menu')) closeMenus();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeMenus();
  });
  window.addEventListener('resize', () => menus.filter(m => m.open).forEach(placePopover));
  window.addEventListener('scroll', () => menus.filter(m => m.open).forEach(placePopover), true);

  document.querySelectorAll('form[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      const message = form.getAttribute('data-confirm');
      if (message && !window.confirm(message)) event.preventDefault();
    });
  });
})();
