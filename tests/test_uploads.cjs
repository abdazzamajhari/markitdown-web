const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {File: TestFile, Blob: TestBlob} = require('node:buffer');
const {JSDOM, VirtualConsole} = require('jsdom');
const staticRoot = path.join(__dirname, '../app/static');
const tick = () => new Promise(resolve => setTimeout(resolve, 10));
const file = (name, text = 'example') => new TestFile([text], name);

function harness(t, respond) {
  const errors = [], calls = [], virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(fs.readFileSync(path.join(staticRoot, 'index.html'), 'utf8'), {
    url: 'https://example.com/', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole,
  });
  t.after(() => dom.window.close());
  const w = dom.window, $ = id => w.document.getElementById(id);
  let guard, urlCount = 0;
  w.Blob = TestBlob; w.AbortController = AbortController;
  w.URL.createObjectURL = () => `blob:fixture-${++urlCount}`; w.URL.revokeObjectURL = () => {};
  w.Image = class { naturalWidth = 200; naturalHeight = 100; async decode() {} };
  w.HTMLCanvasElement.prototype.getContext = () => ({
    clearRect() {}, drawImage() {}, putImageData() {},
    getImageData: () => ({data: new Uint8ClampedArray(200 * 100 * 4)}),
  });
  w.HTMLCanvasElement.prototype.toBlob = callback => callback(new TestBlob(['png'], {type: 'image/png'}));
  w.PrivasiGuardSession = {watch: config => { guard = config; }};
  w.fetch = async (endpoint, options) => {
    if (endpoint === '/api/capabilities') return Response.json({image_ocr: 'unavailable', image_ocr_model: 'fixture'});
    calls.push({endpoint, body: options.body, options: JSON.parse(Buffer.from(options.headers['X-Options'], 'base64')), name: decodeURIComponent(options.headers['X-Filename'])});
    if (respond) return respond(endpoint, options);
    if (endpoint === '/api/pdf-editor-preview') return Response.json({image: 'anBlZw==', pages: 2, width: 600, height: 800});
    if (endpoint === '/api/image-preview') return new Response('png');
    return new Response('result', {headers: {'X-Document-Info': JSON.stringify({format: 'pdf', pages: 1, files: 2, source_pages: [1, 1]})}});
  };
  for (const name of ['zip.js', 'app.js', 'tools.js']) w.eval(fs.readFileSync(path.join(staticRoot, name), 'utf8'));
  const activate = (category, tool) => {
    w.document.querySelector(`[data-category-select=${category}]`).click();
    w.document.querySelector(`[data-category=${category}][data-tool=${tool}]`).click();
  };
  const drag = (target, type, files, types = ['Files']) => {
    const event = new w.Event(type, {bubbles: true, cancelable: true});
    Object.defineProperty(event, 'dataTransfer', {value: {files, types, items: files.map(() => ({kind: 'file'}))}});
    target.dispatchEvent(event); return event;
  };
  const drop = (id, files) => drag($(id).closest('.upload-field'), 'drop', files);
  const choose = (id, files) => {
    Object.defineProperty($(id), 'files', {configurable: true, value: files});
    $(id).dispatchEvent(new w.Event('change', {bubbles: true}));
    Object.defineProperty($(id), 'files', {configurable: true, value: []});
  };
  return {$, w, calls, errors, activate, drag, drop, choose, get guard() { return guard; }};
}

test('merge drops append once in order and picker selections use the same list', async t => {
  const h = harness(t); h.activate('pdf', 'merge');
  const area = h.$('merge-files').closest('.upload-field');
  const over = h.drag(area, 'dragover', []);
  assert.equal(over.defaultPrevented, true); assert.equal(over.dataTransfer.dropEffect, 'copy'); assert.equal(area.classList.contains('dragging'), true);
  h.drag(area.querySelector('strong'), 'drop', [file('A.pdf'), file('B.pdf')]);
  h.choose('merge-files', [file('C.pdf')]);
  assert.deepEqual([...h.w.document.querySelectorAll('.merge-file-meta strong')].map(n => n.textContent), ['A.pdf', 'B.pdf', 'C.pdf']);
  assert.equal(area.classList.contains('dragging'), false);
  assert.equal(h.w.document.body.classList.contains('page-dragging'), false);
  h.drop('merge-files', [file('wrong.png')]);
  assert.equal(h.w.document.querySelectorAll('.merge-file').length, 3);
  assert.match(h.$('merge-status').textContent, /format/);
  await tick(); assert.deepEqual(h.errors, []);
});

test('dropping elsewhere on the active merge tool routes to its file list', async t => {
  const h = harness(t); h.activate('pdf', 'merge');
  const event = h.drag(h.w.document.body, 'drop', [file('B.pdf'), file('A.pdf')]);
  assert.equal(event.defaultPrevented, true);
  assert.equal(h.w.document.querySelectorAll('.merge-file').length, 2);
  assert.equal(h.w.document.querySelectorAll('#files .file-row').length, 0);
  await tick(); assert.deepEqual(h.errors, []);
});

test('compression processes dropped files without a native FileList and freezes replacement during a request', async t => {
  let release;
  const h = harness(t, () => new Promise(resolve => { release = () => resolve(new Response('pdf', {headers: {'X-Document-Info': '{"pages":1}'}})); }));
  h.activate('pdf', 'compress'); h.drop('compress-files', [file('first.pdf', '%PDF-first')]);
  assert.equal(h.$('compress-files').files.length, 0);
  assert.equal(h.$('compress-files').required, false);
  assert.equal(h.guard.hasData(), true);
  h.$('compress-form').querySelector('[type=submit]').click();
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].name, 'first.pdf');
  assert.equal(await h.calls[0].body.text(), '%PDF-first');
  assert.equal(h.$('compress-files').disabled, true);
  h.drop('compress-files', [file('replacement.pdf')]);
  assert.match(h.$('compress-status').textContent, /Tunggu/);
  assert.match(h.$('compress-selected').textContent, /first.pdf/);
  assert.doesNotMatch(h.$('compress-selected').textContent, /replacement/);
  release(); await tick();
  assert.equal(h.$('compress-files').disabled, false);
  assert.equal(h.$('compress-results').querySelectorAll('.output-card').length, 1);
  assert.deepEqual(h.errors, []);
});

test('image batches accept TIFF, reject invalid batches atomically and replace via picker', async t => {
  const h = harness(t); h.activate('image', 'image');
  h.drop('image-files', [file('first.tiff'), file('second.png')]);
  assert.match(h.$('image-selected').textContent, /2 berkas.*first.tiff.*second.png/);
  assert.equal(h.guard.hasData(), true);
  h.drop('image-files', [file('valid.png'), file('wrong.pdf')]);
  assert.match(h.$('image-selected').textContent, /first.tiff/);
  h.drop('image-files', [{name: 'large.png', size: 11 * 1024 * 1024}]);
  assert.match(h.$('image-status').textContent, /maksimal/);
  h.choose('image-files', [file('new.webp')]);
  assert.match(h.$('image-selected').textContent, /1 berkas.*new.webp/);
  assert.doesNotMatch(h.$('image-selected').textContent, /first.tiff/);
  await tick(); assert.deepEqual(h.errors, []);
});

test('merge limits and busy state also apply to dropped files', async t => {
  let release;
  const h = harness(t, () => new Promise(resolve => { release = () => resolve(new Response('pdf', {headers: {'X-Document-Info': '{"files":2,"pages":2,"source_pages":[1,1]}'}})); }));
  h.activate('pdf', 'merge'); h.drop('merge-files', [file('A.pdf'), file('B.pdf')]);
  h.drop('merge-files', Array.from({length: 19}, (_, i) => file(`${i}.pdf`)));
  assert.equal(h.w.document.querySelectorAll('.merge-file').length, 2);
  h.$('merge-submit').click();
  h.drop('merge-files', [file('later.pdf')]);
  assert.equal(h.w.document.querySelectorAll('.merge-file').length, 2);
  assert.match(h.$('merge-status').textContent, /Tunggu/);
  assert.equal(await h.calls[0].body.text(), 'exampleexample');
  release(); await tick(); assert.deepEqual(h.errors, []);
});

test('editor drops accept one PDF and do not silently truncate multiple files', async t => {
  const h = harness(t); h.activate('pdf', 'sign');
  h.drop('sign-file', [file('A.pdf'), file('B.pdf')]);
  assert.equal(h.calls.length, 0); assert.match(h.$('sign-status').textContent, /satu berkas/);
  h.drag(h.w.document.body, 'drop', [file('document.pdf')]); await tick();
  assert.equal(h.calls[0].endpoint, '/api/pdf-editor-preview');
  assert.equal(h.calls[0].name, 'document.pdf');
  assert.equal(h.$('sign-selected').textContent, 'document.pdf');
  assert.equal(h.$('sign-page').hidden, false);
  assert.deepEqual(h.errors, []);
});

test('signature TIFF drops use the image preview and oversized sources are refused', async t => {
  const h = harness(t); h.activate('pdf', 'sign');
  h.drag(h.w.document.body, 'drop', [file('signature.tiff')]); await tick();
  assert.equal(h.calls[0].endpoint, '/api/image-preview');
  assert.equal(h.calls[0].options.max_pixels, 2000000);
  assert.equal(h.$('signature-selected').textContent, 'signature.tiff');
  h.drop('signature-file', [{name: 'large.png', size: 2 * 1024 * 1024}]); await tick();
  assert.match(h.$('sign-status').textContent, /maksimal/);
  assert.equal(h.$('signature-selected').textContent, 'signature.tiff');
  assert.deepEqual(h.errors, []);
});

test('text drags are left alone and empty file drops give feedback', async t => {
  const h = harness(t); h.activate('pdf', 'merge');
  const area = h.$('merge-files').closest('.upload-field');
  assert.equal(h.drag(area, 'dragover', [], ['text/plain']).defaultPrevented, false);
  const event = h.drop('merge-files', []);
  assert.equal(event.defaultPrevented, true);
  assert.match(h.$('merge-status').textContent, /bukan folder/);
  assert.equal(h.w.document.querySelectorAll('.merge-file').length, 0);
  await tick(); assert.deepEqual(h.errors, []);
});
