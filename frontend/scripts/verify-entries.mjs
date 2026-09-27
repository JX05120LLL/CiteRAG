import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

for (const [entry, root] of [['index.html', '#app'], ['voice.html', '#voice-app'], ['ui-preview.html', '#preview-app']]) {
  const { document } = new JSDOM(readFileSync(`dist/${entry}`, 'utf8')).window;
  assert.ok(document.title.includes('CiteRAG'));
  assert.ok(document.querySelector(root));
  assert.ok(document.querySelector('script[type="module"][src]'));
  assert.ok(document.querySelector('link[rel="stylesheet"][href]'));
  for (const element of document.querySelectorAll('script[src], link[href]')) {
    const path = element.getAttribute('src') ?? element.getAttribute('href');
    if (path.startsWith('data:')) continue;
    assert.ok(existsSync(`dist/${path.replace(/^\//, '')}`), `Missing asset: ${path}`);
  }
}
console.log('All three built entries and their local assets passed.');
