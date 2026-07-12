(function () {
  'use strict';

  const menus = Array.from(document.querySelectorAll('.tenant-action-menu'));
  const portalState = new WeakMap();

  function getPopover(menu) {
    const state = portalState.get(menu);
    return (state && state.popover) || menu.querySelector('.tenant-action-popover');
  }

  function mountPopover(menu) {
    let state = portalState.get(menu);
    if (state) return state.popover;

    const popover = menu.querySelector('.tenant-action-popover');
    if (!popover) return null;

    const placeholder = document.createComment('tenant-action-popover');
    popover.parentNode.insertBefore(placeholder, popover);
    document.body.appendChild(popover);
    popover.classList.add('tenant-action-popover--portal');
    state = { popover, placeholder };
    portalState.set(menu, state);
    return popover;
  }

  function restorePopover(menu) {
    const state = portalState.get(menu);
    if (!state) return;

    const { popover, placeholder } = state;
    popover.classList.remove('tenant-action-popover--portal');
    popover.style.removeProperty('top');
    popover.style.removeProperty('left');
    popover.style.removeProperty('right');
    popover.style.removeProperty('bottom');
    popover.style.removeProperty('width');
    popover.style.removeProperty('max-height');

    if (placeholder.parentNode) {
      placeholder.parentNode.insertBefore(popover, placeholder);
      placeholder.remove();
    }
    portalState.delete(menu);
  }

  function placePopover(menu) {
    const trigger = menu.querySelector('summary');
    const popover = mountPopover(menu);
    if (!trigger || !popover) return;

    const gap = 8;
    const margin = 12;
    const triggerRect = trigger.getBoundingClientRect();
    const width = Math.min(260, Math.max(220, window.innerWidth - margin * 2));

    popover.style.width = width + 'px';
    popover.style.maxHeight = Math.max(180, window.innerHeight - margin * 2) + 'px';

    const popoverHeight = Math.min(popover.scrollHeight, window.innerHeight - margin * 2);
    let left = triggerRect.right - width;
    left = Math.max(margin, Math.min(left, window.innerWidth - width - margin));

    const roomBelow = window.innerHeight - triggerRect.bottom - margin;
    const roomAbove = triggerRect.top - margin;
    let top;

    if (roomBelow >= Math.min(popoverHeight, 320) || roomBelow >= roomAbove) {
      top = triggerRect.bottom + gap;
      top = Math.min(top, window.innerHeight - popoverHeight - margin);
    } else {
      top = triggerRect.top - popoverHeight - gap;
    }

    popover.style.left = Math.round(left) + 'px';
    popover.style.top = Math.round(Math.max(margin, top)) + 'px';
  }

  function closeMenu(menu) {
    if (!menu) return;
    menu.removeAttribute('open');
    restorePopover(menu);
  }

  function closeMenus(except) {
    menus.forEach((menu) => {
      if (menu !== except) closeMenu(menu);
    });
  }

  menus.forEach((menu) => {
    menu.addEventListener('toggle', () => {
      if (menu.open) {
        closeMenus(menu);
        requestAnimationFrame(() => placePopover(menu));
      } else {
        restorePopover(menu);
      }
    });
  });

  document.addEventListener('click', (event) => {
    const clickedMenu = event.target.closest('.tenant-action-menu');
    const clickedPortal = event.target.closest('.tenant-action-popover--portal');
    if (!clickedMenu && !clickedPortal) closeMenus();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeMenus();
  });

  window.addEventListener('resize', () => menus.filter((menu) => menu.open).forEach(placePopover));
  window.addEventListener('scroll', () => menus.filter((menu) => menu.open).forEach(placePopover), true);

  document.querySelectorAll('form[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      const message = form.getAttribute('data-confirm');
      if (message && !window.confirm(message)) event.preventDefault();
    });
  });
})();
