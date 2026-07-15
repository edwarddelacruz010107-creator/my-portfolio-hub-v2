'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { JSDOM } = require('jsdom');

const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'app/static/js/notifications-v1.js'), 'utf8');

function response(status, payload, etag) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: (name) => name === 'ETag' ? etag : null },
    json: async () => payload
  };
}

function markup() {
  return '<div data-notification-center data-feed-url="/api/notifications/feed"><button data-notification-toggle><span data-notification-badge class="is-hidden">0</span></button><div data-notification-items></div></div>';
}

async function boot(fetchImpl, visibility = 'visible') {
  const dom = new JSDOM(`<!doctype html><html><body>${markup()}</body></html>`, {
    runScripts: 'dangerously',
    url: 'https://example.test/'
  });
  Object.defineProperty(dom.window.document, 'visibilityState', { configurable: true, value: visibility });
  dom.window.fetch = fetchImpl;
  dom.window.eval(source);
  dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
  await new Promise((resolve) => dom.window.setTimeout(resolve, 20));
  return dom;
}

test('feed renders malicious values as text and rejects cross-origin actions', async () => {
  const payload = '<img src=x onerror="globalThis.compromised=true">';
  const dom = await boot(async () => response(200, {
    unread_count: 1,
    notifications: [{
      title: payload,
      message: payload,
      is_read: false,
      created_at: '2026-07-14T12:00:00Z',
      action_url: 'https://evil.test/steal'
    }]
  }, '"v1"'));
  const { document } = dom.window;
  assert.equal(document.querySelector('[data-notification-items] strong').textContent, payload);
  assert.equal(document.querySelector('[data-notification-items] img'), null);
  assert.equal(document.querySelector('[data-notification-items] a'), null);
  assert.equal(document.querySelector('[data-notification-badge]').textContent, '1');
  assert.equal(dom.window.compromised, undefined);
  dom.window.close();
});

test('conditional refresh reuses the ETag after visibility resumes', async () => {
  const calls = [];
  const dom = await boot(async (_url, options) => {
    calls.push(options.headers);
    if (calls.length === 1) {
      return response(200, { unread_count: 0, notifications: [] }, '"feed-v1"');
    }
    return response(304, null, '"feed-v1"');
  });
  dom.window.document.dispatchEvent(new dom.window.Event('visibilitychange'));
  await new Promise((resolve) => dom.window.setTimeout(resolve, 20));
  assert.equal(calls.length, 2);
  assert.equal(calls[1]['If-None-Match'], '"feed-v1"');
  dom.window.close();
});

test('hidden pages do not issue notification requests', async () => {
  let calls = 0;
  const dom = await boot(async () => {
    calls += 1;
    return response(200, { unread_count: 0, notifications: [] }, '"v1"');
  }, 'hidden');
  assert.equal(calls, 0);
  dom.window.close();
});
