(function () {
  'use strict';

  var root = document.documentElement;
  var themeToggle = document.getElementById('ph-theme-toggle');
  var storageKey = root.getAttribute('data-theme-storage') || 'phPublicTheme';

  if (themeToggle) {
    themeToggle.addEventListener('click', function () {
      var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try {
        localStorage.setItem(storageKey, next);
      } catch (error) {
        // Theme changes remain usable even when storage is unavailable.
      }
    });
  }

  var navToggle = document.getElementById('ph-nav-toggle');
  var navLinks = document.getElementById('ph-nav-links');
  if (navToggle && navLinks) {
    navToggle.addEventListener('click', function () {
      var open = navLinks.classList.toggle('is-open');
      navToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }
})();
