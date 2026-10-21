const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {File} = require('node:buffer');
const {JSDOM, VirtualConsole} = require('jsdom');
const root = path.join(__dirname, '../app/static');
const until = async check => {
  for (let i = 0; i < 100; i++) {
    if (check()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.fail('OCR UI did not reach the expected state');
};

function harness(t, {failurePage = 0, onPreview} = {}) {
  const errors = [], calls = [], uploads = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', e => errors.push(e.message));
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'index.html'), 'utf8'), {
    url: 'https://example.test/', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole,
  });
  t.after(() => dom.window.close());
  const w = dom.window, $ = id => w.document.getElementById(id);
  w.HTMLCanvasElement.prototype.getContext = () => ({clearRect() {}});
  w.PrivasiGuardSession = {watch() {}};
  w.URL.createObjectURL = () => 'blob:preview'; w.URL.revokeObjectURL = () => {};
  w.HTMLImageElement.prototype.decode = async () => {};
  w.XMLHttpRequest = class {
    status = 200; responseText = ''; listeners = {}; headers = {}; upload = {addEventListener() {}};
    open() {}
    setRequestHeader(name, value) { this.headers[name] = value; }
    getResponseHeader(name) { return name === 'X-OCR-Engine' ? 'pdf-images' : null; }
    addEventListener(type, fn) { this.listeners[type] = fn; }
    send(body) { uploads.push({headers: this.headers, body}); queueMicrotask(() => this.listeners.load()); }
  };
  w.fetch = async (endpoint, options) => {
    if (endpoint === '/api/capabilities') return Response.json({image_ocr: 'unlimited-ocr', image_ocr_model: 'baidu/Unlimited-OCR'});
    calls.push({endpoint, options});
    if (endpoint === '/api/pdf-language-check') return Response.json({languages: ['Indonesia'], assessment: 'wajar', suspect_spans: []});
    const page = Number(new URL(endpoint, 'https://example.test').searchParams.get('page'));
    if (onPreview) await onPreview(page);
    return Response.json({page, total_pages: 2, image: 'data:image/jpeg;base64,anBlZw==',
      regions: [], page_source: page === failurePage ? null : 'unlimited-ocr',
      page_text: page === failurePage ? '' : `TEKS HALAMAN ${page}`,
      ...(page === failurePage ? {ocr_error: 'Server Unlimited-OCR gagal', ocr_status: 502} : {}),
    });
  };
  for (const script of ['zip.js', 'app.js', 'tools.js']) w.eval(fs.readFileSync(path.join(root, script), 'utf8'));
  w.document.querySelector('[data-category-select=pdf]').click();
  const drop = () => {
    const event = new w.Event('drop', {bubbles: true, cancelable: true});
    Object.defineProperty(event, 'dataTransfer', {value: {files: [new File(['%PDF-fixture'], 'scan.pdf')], types: ['Files']}});
    w.document.body.dispatchEvent(event);
  };
  return {w, $, drop, calls, uploads, errors};
}

test('OCR drops require consent before upload and do not opt into language review', async t => {
  const h = harness(t); h.drop();
  await until(() => h.$('notice').textContent.includes('Izinkan'));
  assert.equal(h.uploads.length, 0); assert.equal(h.calls.length, 0);
  assert.equal(h.$('language-review-ai').checked, false);
  assert.match(h.$('ocr-ai-hint').textContent, /Setiap halaman dirender penuh/);
  assert.deepEqual(h.errors, []);
});

test('Unlimited-OCR PDF text is assembled in page order with consent on every request', async t => {
  const h = harness(t); h.$('external-ai').checked = true; h.drop();
  await until(() => !h.$('download-all').disabled);
  assert.equal(h.uploads[0].headers['X-External-AI'], 'true');
  assert.deepEqual(h.calls.map(c => c.endpoint), ['/api/pdf-preview?page=1', '/api/pdf-preview?page=2']);
  assert.ok(h.calls.every(c => c.options.headers['X-External-AI'] === 'true'));
  const text = h.w.document.querySelector('pre.transcript').textContent;
  assert.ok(text.indexOf('TEKS HALAMAN 1') < text.indexOf('TEKS HALAMAN 2'));
  assert.match(text, /Unlimited-OCR/); assert.doesNotMatch(text, /Tesseract/);
  assert.deepEqual(h.errors, []);
});

test('failed page keeps complete-document downloads disabled', async t => {
  const h = harness(t, {failurePage: 2}); h.$('external-ai').checked = true; h.drop();
  await until(() => h.w.document.querySelector('.file-row.error'));
  assert.equal(h.$('download-all').disabled, true);
  assert.match(h.w.document.querySelector('.file-state').textContent, /Server Unlimited-OCR gagal/);
  assert.deepEqual(h.errors, []);
});

test('revoking image consent stops further PDF page uploads', async t => {
  let release;
  const h = harness(t, {onPreview: () => new Promise(resolve => { release = resolve; })});
  h.$('external-ai').checked = true; h.drop();
  await until(() => !!release);
  h.$('external-ai').checked = false; release();
  await until(() => h.w.document.querySelector('.file-row.error'));
  assert.equal(h.calls.length, 1); assert.equal(h.$('download-all').disabled, true);
  assert.deepEqual(h.errors, []);
});

test('Netra review has separate consent and receives only OCR text', async t => {
  const h = harness(t); h.$('external-ai').checked = h.$('language-review-ai').checked = true; h.drop();
  await until(() => h.calls.filter(c => c.endpoint === '/api/pdf-language-check').length === 2);
  for (const call of h.calls.filter(c => c.endpoint === '/api/pdf-language-check')) {
    const body = JSON.parse(call.options.body);
    assert.deepEqual(Object.keys(body), ['text']);
    assert.match(body.text, /^TEKS HALAMAN [12]$/);
  }
  assert.deepEqual(h.errors, []);
});
