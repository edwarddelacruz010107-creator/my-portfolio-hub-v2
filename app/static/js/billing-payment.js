(function () {
  'use strict';

  var root = document.getElementById('paymentCheckout');
  if (!root) return;

  function qs(selector) { return root.querySelector(selector); }
  function qsa(selector) { return Array.prototype.slice.call(root.querySelectorAll(selector)); }
  function money(symbol, value, code) {
    var amount = Number(value || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
    return symbol + amount + (code ? ' ' + code : '');
  }

  qsa('.js-copy-payment').forEach(function (button) {
    button.addEventListener('click', function () {
      var text = button.getAttribute('data-copy') || '';
      var original = button.textContent;
      var done = function () {
        button.textContent = 'Copied';
        setTimeout(function () { button.textContent = original; }, 1400);
      };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done).catch(function () {});
      } else {
        var input = document.createElement('textarea');
        input.value = text;
        input.style.position = 'fixed';
        input.style.opacity = '0';
        document.body.appendChild(input);
        input.select();
        try { document.execCommand('copy'); done(); } catch (err) {}
        input.remove();
      }
    });
  });

  var zone = qs('#uploadZone');
  var input = qs('#paymentProofInput');
  var preview = qs('#uploadPreview');
  var content = qs('#uploadZoneContent');
  var fileName = qs('#uploadFileName');
  var clearUpload = qs('#clearUpload');

  function showFile(file) {
    if (!file) return;
    if (fileName) fileName.textContent = file.name;
    if (preview) preview.hidden = false;
    if (content) content.hidden = true;
  }
  function resetFile() {
    if (input) input.value = '';
    if (preview) preview.hidden = true;
    if (content) content.hidden = false;
  }
  if (zone && input) {
    ['dragenter', 'dragover'].forEach(function (eventName) {
      zone.addEventListener(eventName, function (event) {
        event.preventDefault();
        zone.classList.add('drag-over');
      });
    });
    ['dragleave', 'drop'].forEach(function (eventName) {
      zone.addEventListener(eventName, function (event) {
        event.preventDefault();
        zone.classList.remove('drag-over');
      });
    });
    input.addEventListener('change', function () { showFile(input.files && input.files[0]); });
  }
  if (clearUpload) {
    clearUpload.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      resetFile();
    });
  }

  var countrySelect = qs('#billingCountry');
  var amountHidden = qs('#amount_paid');
  var submitButton = qs('#submitBtn');
  var quoteUrl = root.getAttribute('data-quote-url');
  var billingCycle = root.getAttribute('data-billing-cycle') || 'monthly';

  function setQuoteLoading(value) {
    root.classList.toggle('payment-loading', value);
    if (countrySelect) countrySelect.disabled = value;
    if (submitButton) submitButton.disabled = value;
  }

  function setFxMessage(ok, message) {
    var el = qs('#fxMessage');
    if (!el) return;
    el.classList.toggle('fx-message--error', !ok);
    var icon = el.querySelector('iconify-icon');
    if (icon) icon.setAttribute('icon', ok ? 'lucide:refresh-cw' : 'lucide:triangle-alert');
    var span = el.querySelector('span');
    if (span) span.textContent = message;
  }

  function updateQuote(data) {
    var code = data.currency;
    var symbol = data.symbol;
    var amount = Number(data.amount || 0);
    var country = data.country || {};

    var amountValue = qs('#localAmountValue');
    var amountSymbol = qs('#localAmountSymbol');
    var amountCurrency = qs('#localAmountCurrency');
    var checkoutLabel = qs('#checkoutAmountLabel');
    var sendLabel = qs('#sendAmountLabel');
    var usdLabel = qs('#baseUsdAmount');
    var currencyLabel = qs('#countryCurrencyLabel');
    var rateLabel = qs('#fxRateText');
    var providerLabel = qs('#fxProviderText');

    if (amountValue) amountValue.textContent = amount.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
    if (amountSymbol) amountSymbol.textContent = symbol;
    if (amountCurrency) amountCurrency.textContent = code;
    if (checkoutLabel) checkoutLabel.textContent = money(symbol, amount, code);
    if (sendLabel) sendLabel.textContent = money(symbol, amount, code);
    if (amountHidden) amountHidden.value = amount.toFixed(2);
    if (usdLabel) usdLabel.textContent = 'Base price after discount: $' + Number(data.amount_usd || 0).toFixed(2) + ' USD';
    if (currencyLabel) currencyLabel.textContent = (country.flag ? country.flag + ' ' : '') + code;
    if (rateLabel) rateLabel.textContent = code === 'USD' ? 'No conversion required' : '1 USD = ' + Number(data.rate || 1).toFixed(4) + ' ' + code;
    if (providerLabel) providerLabel.textContent = 'Rate source: ' + String(data.provider || 'fixed').replace(/-/g, ' ') + (data.stale ? ' · cached rate' : '');

    var original = qs('#discountOriginal');
    var saving = qs('#discountSaving');
    var finalTotal = qs('#discountFinal');
    if (original) original.textContent = money(symbol, data.amount_before, '');
    if (saving) saving.textContent = '− ' + money(symbol, data.discount, '');
    if (finalTotal) finalTotal.textContent = money(symbol, amount, code);

    setFxMessage(true, 'Amount updated. The final exchange rate will be saved with your payment submission.');
    if (submitButton) submitButton.disabled = false;

    try {
      var url = new URL(window.location.href);
      url.searchParams.set('country', country.code || countrySelect.value);
      window.history.replaceState({}, '', url.toString());
    } catch (err) {}
  }

  function refreshQuote() {
    if (!countrySelect || !quoteUrl) return;
    setQuoteLoading(true);
    var url = quoteUrl + '?country=' + encodeURIComponent(countrySelect.value) + '&billing_cycle=' + encodeURIComponent(billingCycle);
    fetch(url, {credentials: 'same-origin', headers: {'Accept': 'application/json'}})
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok || !data.ok) throw new Error(data.error || 'Exchange rate unavailable.');
          return data;
        });
      })
      .then(updateQuote)
      .catch(function (error) {
        setFxMessage(false, error.message || 'Unable to refresh the exchange rate. Please try again.');
        if (submitButton) submitButton.disabled = true;
      })
      .finally(function () {
        root.classList.remove('payment-loading');
        if (countrySelect) countrySelect.disabled = false;
      });
  }

  if (countrySelect) countrySelect.addEventListener('change', refreshQuote);

  var form = qs('#paymentForm');
  if (form) {
    form.addEventListener('submit', function (event) {
      var reference = qs('#payment_reference');
      if (!reference || !reference.value.trim()) {
        event.preventDefault();
        if (reference) {
          reference.focus();
          reference.setAttribute('aria-invalid', 'true');
        }
        return;
      }
      if (!input || !input.files || !input.files.length) {
        event.preventDefault();
        if (zone) {
          zone.style.borderColor = 'rgba(239,68,68,.85)';
          zone.scrollIntoView({behavior: 'smooth', block: 'center'});
          setTimeout(function () { zone.style.borderColor = ''; }, 3000);
        }
        return;
      }
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.innerHTML = '<iconify-icon icon="lucide:loader-circle" width="17"></iconify-icon> Submitting…';
      }
    });
  }
})();
