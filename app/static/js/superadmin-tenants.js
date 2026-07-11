(function () {
  'use strict';

  const menus = Array.from(document.querySelectorAll('.tenant-action-menu'));

  function closeMenus(except) {
    menus.forEach((menu) => {
      if (menu !== except) menu.removeAttribute('open');
    });
  }

  menus.forEach((menu) => {
    menu.addEventListener('toggle', () => {
      if (menu.open) closeMenus(menu);
    });
  });

  document.addEventListener('click', (event) => {
    if (!event.target.closest('.tenant-action-menu')) closeMenus();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeMenus();
  });

  document.querySelectorAll('form[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      const message = form.getAttribute('data-confirm');
      if (message && !window.confirm(message)) event.preventDefault();
    });
  });
})();
