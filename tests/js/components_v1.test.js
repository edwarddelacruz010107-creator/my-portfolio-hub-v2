'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { JSDOM } = require('jsdom');

const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'app/static/js/components-v1.js'), 'utf8');

function boot(body) {
  const dom = new JSDOM(`<!doctype html><html><body>${body}</body></html>`, {
    runScripts: 'dangerously',
    url: 'https://example.test/'
  });
  const { window } = dom;
  window.HTMLElement.prototype.focus = function focus() {
    Object.defineProperty(window.document, 'activeElement', { configurable: true, value: this });
  };
  if (window.HTMLDialogElement) {
    window.HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute('open', ''); };
    window.HTMLDialogElement.prototype.close = function close() { this.removeAttribute('open'); };
  }
  window.eval(source);
  window.document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));
  return dom;
}

test('programmatic notifications treat malicious strings as text', () => {
  const dom = boot('');
  const payload = '<img src=x onerror="globalThis.compromised=true">';
  const toast = dom.window.MPHUI.notify(payload, 'not-allowed', 60_000);
  assert.equal(toast.className, 'ui-toast ui-toast--info');
  assert.equal(toast.querySelector('span').textContent, payload);
  assert.equal(toast.querySelector('img'), null);
  assert.equal(dom.window.compromised, undefined);
  dom.window.close();
});

test('dialog opens, closes, and returns focus to its trigger', () => {
  const dom = boot('<button id="open" data-ui-dialog-open="sample">Open</button><dialog id="sample" data-ui-dialog><div class="ui-dialog__surface"><button id="close" data-ui-dialog-close>Close</button></div></dialog>');
  const { document, MouseEvent } = dom.window;
  const open = document.getElementById('open');
  const dialog = document.getElementById('sample');
  open.focus();
  open.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  assert.equal(dialog.hasAttribute('open'), true);
  document.getElementById('close').dispatchEvent(new MouseEvent('click', { bubbles: true }));
  assert.equal(dialog.hasAttribute('open'), false);
  assert.equal(document.activeElement, open);
  dom.window.close();
});

test('dropdown and tabs expose deterministic keyboard state', () => {
  const dom = boot('<div data-ui-dropdown><button id="menuToggle" data-ui-dropdown-toggle aria-expanded="false">Menu</button><div class="ui-dropdown__menu" hidden><button id="menuItem">Item</button></div></div><div data-ui-tabs><div role="tablist"><button id="tab-a" role="tab" aria-controls="panel-a" aria-selected="true" tabindex="0">A</button><button id="tab-b" role="tab" aria-controls="panel-b" aria-selected="false" tabindex="-1">B</button></div><section id="panel-a" role="tabpanel"></section><section id="panel-b" role="tabpanel" hidden></section></div>');
  const { document, MouseEvent, KeyboardEvent } = dom.window;
  const toggle = document.getElementById('menuToggle');
  toggle.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  assert.equal(toggle.getAttribute('aria-expanded'), 'true');
  assert.equal(document.getElementById('menuItem').parentElement.hidden, false);
  document.getElementById('menuItem').dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Escape' }));
  assert.equal(toggle.getAttribute('aria-expanded'), 'false');

  document.getElementById('tab-a').dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'ArrowRight' }));
  assert.equal(document.getElementById('tab-b').getAttribute('aria-selected'), 'true');
  assert.equal(document.getElementById('panel-a').hidden, true);
  assert.equal(document.getElementById('panel-b').hidden, false);
  dom.window.close();
});

test('switch and password controls mirror their accessible state', () => {
  const dom = boot('<div class="ui-switch"><button id="switch" data-ui-switch role="switch" aria-checked="false"><span></span></button><input data-ui-switch-value value="0"></div><input id="password" type="password"><button id="passwordToggle" data-ui-password-toggle="password" aria-pressed="false">Show</button>');
  const { document, MouseEvent } = dom.window;
  document.getElementById('switch').dispatchEvent(new MouseEvent('click', { bubbles: true }));
  assert.equal(document.getElementById('switch').getAttribute('aria-checked'), 'true');
  assert.equal(document.querySelector('[data-ui-switch-value]').value, '1');
  document.getElementById('passwordToggle').dispatchEvent(new MouseEvent('click', { bubbles: true }));
  assert.equal(document.getElementById('password').type, 'text');
  assert.equal(document.getElementById('passwordToggle').getAttribute('aria-pressed'), 'true');
  dom.window.close();
});
