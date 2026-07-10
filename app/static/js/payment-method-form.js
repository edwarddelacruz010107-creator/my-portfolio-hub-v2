(function () {
  'use strict';

  function qs(root, selector) { return root.querySelector(selector); }
  function qsa(root, selector) { return Array.prototype.slice.call(root.querySelectorAll(selector)); }

  function initPaymentMethodEditor(root) {
    var typeSelect = qs(root, '[data-method-type]');
    var nameInput = qs(root, '[data-live-name]');
    var scopeSelect = qs(root, '[data-live-scope]');
    var accountInput = qs(root, '[data-live-account]');
    var activeInput = qs(root, '[data-live-active]');
    var defaultInput = qs(root, '[data-live-default]');
    var context = qs(root, '[data-method-context]');
    var uploadZone = qs(root, '[data-upload-zone]');
    var uploadInput = qs(root, '[data-qr-input]');
    var uploadPreview = qs(root, '[data-upload-preview]');
    var uploadFilename = qs(root, '[data-upload-filename]');
    var previewQr = qs(root, '[data-preview-qr]');

    var methodConfigs = {
      ewallet: {
        label: 'E-Wallet', icon: 'lucide:smartphone',
        text: '<strong>E-wallet selected.</strong> Add the registered account name and mobile number.',
        visible: ['account_name', 'mobile_number']
      },
      bank: {
        label: 'Bank Transfer', icon: 'lucide:landmark',
        text: '<strong>Bank transfer selected.</strong> Add the bank, account holder, and account number.',
        visible: ['account_name', 'account_number', 'bank_name']
      },
      paymongo: {
        label: 'PayMongo', icon: 'lucide:credit-card',
        text: '<strong>PayMongo selected.</strong> Account details are optional because checkout is handled by the gateway.',
        visible: []
      },
      crypto: {
        label: 'Crypto', icon: 'lucide:bitcoin',
        text: '<strong>Crypto selected.</strong> Use Account Number for the wallet address and explain the network in the instructions.',
        visible: ['account_name', 'account_number']
      }
    };

    function updateType() {
      if (!typeSelect) return;
      var config = methodConfigs[typeSelect.value] || methodConfigs.ewallet;
      qsa(root, '[data-field-group]').forEach(function (field) {
        field.hidden = config.visible.indexOf(field.getAttribute('data-field-group')) === -1;
      });
      if (context) {
        var p = qs(context, 'p');
        if (p) p.innerHTML = config.text;
      }
      var previewType = qs(root, '[data-preview-type]');
      var previewIcon = qs(root, '[data-preview-icon]');
      if (previewType) previewType.textContent = config.label;
      if (previewIcon) previewIcon.innerHTML = '<iconify-icon icon="' + config.icon + '" width="23"></iconify-icon>';
    }

    function updatePreviewText() {
      var previewName = qs(root, '[data-preview-name]');
      var previewScope = qs(root, '[data-preview-scope]');
      var previewAccount = qs(root, '[data-preview-account]');
      var previewStatus = qs(root, '[data-preview-status]');
      var previewDefault = qs(root, '[data-preview-default]');

      if (previewName) previewName.textContent = (nameInput && nameInput.value.trim()) || 'Payment method';
      if (previewScope) {
        var option = scopeSelect && scopeSelect.options[scopeSelect.selectedIndex];
        previewScope.textContent = option ? option.textContent.trim() : 'All tenants';
      }
      if (previewAccount) previewAccount.textContent = (accountInput && accountInput.value.trim()) || 'Not added yet';
      if (previewStatus && activeInput) {
        previewStatus.textContent = activeInput.checked ? 'Active' : 'Inactive';
        previewStatus.classList.toggle('is-inactive', !activeInput.checked);
      }
      if (previewDefault && defaultInput) previewDefault.hidden = !defaultInput.checked;
    }

    function renderFile(file) {
      if (!file || !file.type || file.type.indexOf('image/') !== 0) return;
      if (uploadFilename) uploadFilename.textContent = file.name;
      var reader = new FileReader();
      reader.onload = function (event) {
        var src = event.target && event.target.result;
        if (!src) return;
        if (uploadPreview) uploadPreview.innerHTML = '<img src="' + src + '" alt="Selected QR code preview">';
        if (previewQr) previewQr.innerHTML = '<img src="' + src + '" alt="Selected QR code preview">';
      };
      reader.readAsDataURL(file);
    }

    if (typeSelect) typeSelect.addEventListener('change', function () { updateType(); updatePreviewText(); });
    [nameInput, scopeSelect, accountInput, activeInput, defaultInput].forEach(function (element) {
      if (!element) return;
      element.addEventListener(element.tagName === 'SELECT' || element.type === 'checkbox' ? 'change' : 'input', updatePreviewText);
    });

    if (uploadZone && uploadInput) {
      uploadZone.addEventListener('click', function (event) {
        if (event.target === uploadInput) return;
        uploadInput.click();
      });
      uploadZone.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          uploadInput.click();
        }
      });
      uploadInput.addEventListener('change', function () { renderFile(uploadInput.files && uploadInput.files[0]); });
      ['dragenter', 'dragover'].forEach(function (name) {
        uploadZone.addEventListener(name, function (event) {
          event.preventDefault();
          uploadZone.classList.add('is-dragging');
        });
      });
      ['dragleave', 'drop'].forEach(function (name) {
        uploadZone.addEventListener(name, function (event) {
          event.preventDefault();
          uploadZone.classList.remove('is-dragging');
        });
      });
      uploadZone.addEventListener('drop', function (event) {
        var files = event.dataTransfer && event.dataTransfer.files;
        if (!files || !files.length) return;
        try {
          var transfer = new DataTransfer();
          transfer.items.add(files[0]);
          uploadInput.files = transfer.files;
        } catch (error) {
          // Some browsers prevent programmatic FileList assignment. The preview can still be shown.
        }
        renderFile(files[0]);
      });
    }

    updateType();
    updatePreviewText();
  }

  document.addEventListener('DOMContentLoaded', function () {
    qsa(document, '[data-payment-method-editor]').forEach(initPaymentMethodEditor);
  });
})();
