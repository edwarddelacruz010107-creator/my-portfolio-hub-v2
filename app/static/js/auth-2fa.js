(function () {
  'use strict';

  var codeInput = document.getElementById('codeInput');
  var backupInput = document.getElementById('backupInput');
  var form = document.getElementById('totpForm');
  if (!codeInput || !backupInput || !form) return;

  codeInput.addEventListener('input', function () {
    var digits = codeInput.value.replace(/\D/g, '').slice(0, 6);
    codeInput.value = digits;
    if (digits.length === 6) {
      backupInput.value = '';
      if (typeof form.requestSubmit === 'function') form.requestSubmit();
    }
  });

  backupInput.addEventListener('input', function () {
    var value = backupInput.value.toUpperCase().replace(/[^A-Z0-9-]/g, '').replace(/-/g, '');
    backupInput.value = value.length > 5 ? value.slice(0, 5) + '-' + value.slice(5, 10) : value;
    if (backupInput.value) codeInput.value = '';
  });
})();
