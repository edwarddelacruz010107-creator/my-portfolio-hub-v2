(function () {
  'use strict';
  var form = document.querySelector('[data-theme-filters]');
  if (!form) return;
  var query = form.querySelector('[data-theme-query]');
  var category = form.querySelector('[data-theme-category]');
  var cards = Array.prototype.slice.call(document.querySelectorAll('[data-theme-card]'));
  var empty = document.querySelector('[data-theme-empty]');

  function filterThemes() {
    var term = String(query && query.value || '').trim().toLowerCase();
    var selected = String(category && category.value || '').trim().toLowerCase();
    var visible = 0;
    cards.forEach(function (card) {
      var matchesName = !term || String(card.dataset.themeName || '').indexOf(term) !== -1;
      var matchesCategory = !selected || String(card.dataset.themeCategory || '') === selected;
      card.hidden = !(matchesName && matchesCategory);
      if (!card.hidden) visible += 1;
    });
    if (empty) empty.hidden = visible !== 0;
  }

  form.addEventListener('input', filterThemes);
  form.addEventListener('change', filterThemes);
}());
