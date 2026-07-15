/* Apply the saved color theme before CSS paints. Keep this file synchronous. */
(function () {
  'use strict';

  var root = document.documentElement;
  var primaryKey = root.getAttribute('data-theme-storage') || 'cms_theme';
  var aliases = (root.getAttribute('data-theme-aliases') || '')
    .split(',')
    .map(function (key) { return key.trim(); })
    .filter(Boolean);
  var keys = [primaryKey].concat(aliases);
  var theme = null;

  try {
    for (var index = 0; index < keys.length; index += 1) {
      var candidate = localStorage.getItem(keys[index]);
      if (candidate === 'light' || candidate === 'dark') {
        theme = candidate;
        break;
      }
    }
  } catch (error) {
    // Storage can be unavailable in privacy-restricted browsing contexts.
  }

  if (!theme && window.matchMedia) {
    theme = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }

  root.setAttribute('data-theme', theme || 'dark');
})();
