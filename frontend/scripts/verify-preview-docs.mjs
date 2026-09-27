import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const root = fileURLToPath(new URL('../../', import.meta.url));
const docs = ['README.md', 'AGENTS.md', 'docs/README.md', 'docs/development/REACT-UI-MIGRATION.md',
  'docs/development/CI.md', 'docs/development/LOCAL-DEVELOPMENT.md', 'docs/development/ROADMAP.md', 'docs/多模态知识助手_技术选型与架构设计_v0.1.md',
  'design/ui/README.md', 'frontend/vendor/livekit/README.md',
  'design/ui/react-candidates/README.md', 'design/ui/react-candidates/DESIGN.md'];
let links = 0;
for (const doc of docs) {
  const filename = resolve(root, doc);
  const content = readFileSync(filename, 'utf8');
  for (const match of content.matchAll(/\[[^\]]*\]\(([^)]+)\)/g)) {
    const target = match[1].split('#')[0];
    if (!target || /^[a-z]+:/i.test(target)) continue;
    assert.ok(existsSync(resolve(dirname(filename), decodeURI(target))), 'Missing document target: ' + doc + ' -> ' + target);
    links++;
  }
}
const gallery = resolve(root, 'design/ui/react-candidates/index.html');
const { document } = new JSDOM(readFileSync(gallery, 'utf8')).window;
let images = 0;
for (const element of document.querySelectorAll('img[src], a[href]')) {
  const target = element.getAttribute('src') ?? element.getAttribute('href');
  if (!target || target.startsWith('#') || /^[a-z]+:/i.test(target)) continue;
  assert.ok(existsSync(resolve(dirname(gallery), target)), 'Missing gallery artifact: ' + target);
  if (element.tagName === 'IMG') images++;
}
console.log('Document targets passed: ' + links + '; gallery image references passed: ' + images);
