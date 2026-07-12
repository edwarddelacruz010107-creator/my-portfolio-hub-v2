(function () {
  'use strict';

  function initBillingPlans() {
    var planCards = Array.prototype.slice.call(document.querySelectorAll('.plan-card-v2[data-plan]'));
    var stepPayment = document.getElementById('step-payment');
    if (!planCards.length || !stepPayment) return;

    var spbName = document.getElementById('spbPlanName');
    var spbPrice = document.getElementById('spbPrice');
    var cycleRadios = Array.prototype.slice.call(document.querySelectorAll('input[name="billing_cycle"]'));
    var priceEls = Array.prototype.slice.call(document.querySelectorAll('.price-display'));
    var yearNotes = Array.prototype.slice.call(document.querySelectorAll('.plan-yearly-note'));
    var payLinks = Array.prototype.slice.call(document.querySelectorAll('.js-pay-btn'));
    var pmBtn = document.getElementById('paymongoBtn');

    function selectedCard() {
      return document.querySelector('.plan-card-v2.selected');
    }

    function selectedPlan() {
      var card = selectedCard();
      return card ? card.getAttribute('data-plan') || '' : '';
    }

    function selectedCycle() {
      var checked = document.querySelector('input[name="billing_cycle"]:checked');
      return checked ? checked.value : 'monthly';
    }

    function priceFor(plan, cycle) {
      var card = planCards.find(function (item) { return item.getAttribute('data-plan') === plan; });
      if (!card) return '';
      var price = card.querySelector('.price-display');
      if (!price) return '';
      return cycle === 'yearly' ? price.getAttribute('data-yearly') : price.getAttribute('data-monthly');
    }

    function updateLinks() {
      var plan = selectedPlan();
      var cycle = selectedCycle();
      payLinks.forEach(function (link) {
        var url = new URL(link.href, window.location.origin);
        url.searchParams.set('billing_cycle', cycle);
        if (plan) url.searchParams.set('plan', plan);
        else url.searchParams.delete('plan');
        link.href = url.toString();
      });
      if (pmBtn) {
        var pmUrl = new URL(pmBtn.href, window.location.origin);
        pmUrl.searchParams.set('action', 'checkout');
        pmUrl.searchParams.set('billing_cycle', cycle);
        if (plan) pmUrl.searchParams.set('plan', plan);
        pmBtn.href = pmUrl.toString();
      }
    }

    function updateSummary() {
      var plan = selectedPlan();
      if (!plan) return;
      if (spbName) spbName.textContent = plan;
      if (spbPrice) spbPrice.textContent = priceFor(plan, selectedCycle());
    }

    function applyCycle(cycle) {
      priceEls.forEach(function (price) {
        price.textContent = cycle === 'yearly' ? price.getAttribute('data-yearly') : price.getAttribute('data-monthly');
      });
      yearNotes.forEach(function (note) {
        note.hidden = cycle !== 'yearly';
        note.style.display = cycle === 'yearly' ? 'flex' : 'none';
      });
      document.querySelectorAll('.cycle-toggle').forEach(function (label) {
        var input = label.querySelector('input');
        label.classList.toggle('is-selected', Boolean(input && input.checked));
      });
      updateSummary();
      updateLinks();
    }

    function choosePlan(card, shouldScroll) {
      planCards.forEach(function (item) { item.classList.remove('selected'); });
      card.classList.add('selected');
      var radio = card.querySelector('.plan-radio');
      if (radio) radio.checked = true;
      stepPayment.hidden = false;
      stepPayment.style.display = '';
      updateSummary();
      updateLinks();
      if (shouldScroll && window.matchMedia('(max-width: 760px)').matches) {
        stepPayment.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }

    planCards.forEach(function (card) {
      card.tabIndex = 0;
      card.setAttribute('role', 'button');
      card.addEventListener('click', function () { choosePlan(card, true); });
      card.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          choosePlan(card, true);
        }
      });
    });

    cycleRadios.forEach(function (radio) {
      radio.addEventListener('change', function () { applyCycle(radio.value); });
    });

    var preselected = selectedCard();
    if (preselected) choosePlan(preselected, false);
    else {
      stepPayment.hidden = true;
      stepPayment.style.display = 'none';
    }
    applyCycle(selectedCycle());

    var promoBanner = document.getElementById('promoBanner');
    if (promoBanner && promoBanner.getAttribute('data-expires')) {
      var output = document.getElementById('promoCountdownText');
      var expiresAt = new Date(promoBanner.getAttribute('data-expires')).getTime();
      function formatRemaining(ms) {
        var secondsTotal = Math.max(0, Math.floor(ms / 1000));
        var days = Math.floor(secondsTotal / 86400);
        var hours = Math.floor((secondsTotal % 86400) / 3600);
        var minutes = Math.floor((secondsTotal % 3600) / 60);
        var seconds = secondsTotal % 60;
        if (days) return days + 'd ' + hours + 'h ' + minutes + 'm';
        if (hours) return hours + 'h ' + minutes + 'm ' + seconds + 's';
        if (minutes) return minutes + 'm ' + seconds + 's';
        return seconds + 's';
      }
      function tick() {
        var remaining = expiresAt - Date.now();
        if (remaining <= 0) {
          promoBanner.hidden = true;
          return false;
        }
        if (output) output.textContent = formatRemaining(remaining);
        return true;
      }
      if (tick()) {
        var timer = window.setInterval(function () {
          if (!tick()) window.clearInterval(timer);
        }, 1000);
      }
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initBillingPlans, { once: true });
  } else {
    initBillingPlans();
  }
})();
