/* PrivasiDoc: offline server tools and a normalized-coordinate PDF editor. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const urls = new Set();
  const makeUrl = (blob) => { const url = URL.createObjectURL(blob); urls.add(url); return url; };
  const revoke = (url) => { if (url) { URL.revokeObjectURL(url); urls.delete(url); } };
  const bytesLabel = (n) => n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(2)} MB`;
  const checkFile = (file, extensions, max = 10 * 1024 * 1024) => {
    if (!file || !extensions.includes(file.name.split('.').pop().toLowerCase())) throw new Error('Pilih berkas dengan format yang didukung.');
    if (!file.size || file.size > max) throw new Error(`Ukuran berkas harus lebih dari 0 dan maksimal ${bytesLabel(max)}.`);
  };
  const download = (blob, name) => {
    const url = makeUrl(blob), a = document.createElement('a');
    a.href = url; a.download = name; document.body.append(a); a.click(); a.remove();
    setTimeout(() => revoke(url), 60000);
  };
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

  async function request(path, file, options = {}, body = file) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 75000);
    try {
      const response = await fetch(path, {method: 'POST', body, signal: controller.signal,
        headers: {'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(file.name),
          'X-Options': btoa(JSON.stringify(options))}});
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || `Pemrosesan gagal (HTTP ${response.status}).`);
      }
      return response;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('Pemrosesan melebihi batas waktu. Coba berkas yang lebih kecil.');
      throw error;
    } finally { clearTimeout(timer); }
  }

  document.querySelectorAll('[data-tool]').forEach((button) => button.addEventListener('click', () => {
    document.querySelectorAll('[data-tool]').forEach((tab) => { tab.classList.toggle('active', tab === button); tab.setAttribute('aria-pressed', String(tab === button)); });
    document.querySelectorAll('.tool-panel').forEach((panel) => { panel.hidden = panel.id !== `tool-${button.dataset.tool}`; });
  }));
  $('clear-session').addEventListener('click', () => {
    for (const url of urls) revoke(url);
    document.querySelectorAll('input[type=file]').forEach((input) => { input.value = ''; });
    $('external-ai').checked = false;
    window.location.reload();
  });
  window.addEventListener('pagehide', () => { for (const url of urls) URL.revokeObjectURL(url); });
  [['image-quality', 'image-quality-value'], ['pdf-quality', 'pdf-quality-value']].forEach(([input, output]) => {
    $(input).addEventListener('input', () => { $(output).value = $(input).value; });
  });
  $('image-format').addEventListener('change', () => { $('image-quality').disabled = $('image-format').value === 'png'; });
  $('pdf-profile').addEventListener('change', () => {
    const profile = $('pdf-profile').value;
    $('pdf-dpi').disabled = $('pdf-quality').disabled = profile === 'lossless';
    $('pdf-dpi').value = profile === 'small' ? 96 : 150;
    $('pdf-quality').value = profile === 'small' ? 55 : 75;
    $('pdf-quality-value').value = $('pdf-quality').value;
  });

  function outputCard(file, blob, response, images = false) {
    const card = document.createElement('article'); card.className = 'output-card';
    const title = document.createElement('h3'); title.textContent = file.name;
    const info = JSON.parse(response.headers.get('X-Document-Info') || '{}');
    const change = (1 - blob.size / file.size) * 100;
    const stats = document.createElement('p');
    stats.className = 'size-comparison';
    stats.textContent = `${bytesLabel(file.size)} → ${bytesLabel(blob.size)} · ${change >= 0 ? 'berkurang' : 'bertambah'} ${Math.abs(change).toFixed(1)}%`;
    const detail = document.createElement('p'); detail.className = 'hint';
    detail.textContent = images ? `${info.original_pixels.join(' × ')} → ${info.pixels.join(' × ')} px · ${info.format.toUpperCase()}` : `${info.pages} halaman · ${info.images_reencoded || 0} gambar dikompres ulang`;
    const name = response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] || 'privasidoc-hasil';
    const button = document.createElement('button'); button.type = 'button'; button.className = 'primary-button'; button.textContent = '↓ Unduh hasil';
    button.addEventListener('click', () => download(blob, name));
    card.append(title, stats, detail);
    if (images) {
      const compare = document.createElement('div'); compare.className = 'compare-images';
      [[file, 'Sebelum'], [blob, 'Sesudah']].forEach(([source, label]) => {
        const figure = document.createElement('figure'), img = document.createElement('img'), caption = document.createElement('figcaption');
        img.src = makeUrl(source); img.alt = `${label}: ${file.name}`; caption.textContent = label;
        figure.append(img, caption); compare.append(figure);
      });
      card.append(compare);
    }
    card.append(button);
    return card;
  }

  function setupBatch(kind, path, extensions, options) {
    $(kind + '-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const form = event.currentTarget, submit = form.querySelector('[type=submit]');
      const files = [...$(kind + '-files').files], status = $(kind + '-status'), result = $(kind + '-results');
      if (!files.length) return;
      // Freeze the selected settings for the complete batch.
      const settings = options();
      submit.disabled = true; result.replaceChildren();
      for (const [i, file] of files.entries()) {
        status.textContent = `Memproses ${i + 1}/${files.length}: ${file.name}…`;
        try {
          checkFile(file, extensions);
          const response = await request(path, file, settings), blob = await response.blob();
          result.append(outputCard(file, blob, response, kind === 'image'));
        } catch (error) {
          const message = document.createElement('p'); message.className = 'notice'; message.textContent = `${file.name}: ${error.message}`; result.append(message);
        }
      }
      status.textContent = 'Pemrosesan selesai. Periksa hasil sebelum mengunduh.'; submit.disabled = false;
    });
  }
  setupBatch('image', '/api/image-compress', ['png', 'jpg', 'jpeg', 'webp'], () => ({
    width: Number($('image-width').value), height: Number($('image-height').value), quality: Number($('image-quality').value),
    format: $('image-format').value, keep_ratio: $('image-ratio').checked, remove_metadata: $('image-metadata').checked,
  }));
  setupBatch('compress', '/api/pdf-compress', ['pdf'], () => ({profile: $('pdf-profile').value,
    dpi: Number($('pdf-dpi').value), quality: Number($('pdf-quality').value), remove_metadata: $('pdf-metadata').checked,
  }));

  // The signature remains in browser memory until PDF export.
  const canvas = $('signature-canvas'), ctx = canvas.getContext('2d');
  ctx.strokeStyle = '#142c42'; ctx.lineWidth = 3; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  let drawing = false, ink = false, signature = null, signatureUrl = null, signatureAspect = 1, whitePreviewUrl = null;
  let pdfFile = null, page = 1, pages = 0, pageWidth = 1, pageHeight = 1, pageUrl = null;
  let placements = [], selected = null, loading = false, previewSequence = 0;
  const canvasPoint = (e) => { const r = canvas.getBoundingClientRect(); return [(e.clientX - r.left) / r.width * canvas.width, (e.clientY - r.top) / r.height * canvas.height]; };
  canvas.addEventListener('pointerdown', (e) => { if (loading) return; e.preventDefault(); drawing = true; canvas.setPointerCapture(e.pointerId); ctx.beginPath(); ctx.moveTo(...canvasPoint(e)); });
  canvas.addEventListener('pointermove', (e) => { if (drawing) { ink = true; ctx.lineTo(...canvasPoint(e)); ctx.stroke(); } });
  ['pointerup', 'pointercancel'].forEach((event) => canvas.addEventListener(event, () => { drawing = false; }));
  $('signature-clear').addEventListener('click', () => { ctx.clearRect(0, 0, canvas.width, canvas.height); ink = false; });
  const cropCanvas = () => {
    const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    let left = canvas.width, top = canvas.height, right = 0, bottom = 0;
    for (let y = 0; y < canvas.height; y++) for (let x = 0; x < canvas.width; x++) {
      if (pixels[(y * canvas.width + x) * 4 + 3]) { left = Math.min(left, x); right = Math.max(right, x); top = Math.min(top, y); bottom = Math.max(bottom, y); }
    }
    const cropped = document.createElement('canvas'); cropped.width = right - left + 17; cropped.height = bottom - top + 17;
    cropped.getContext('2d').drawImage(canvas, left, top, right - left + 1, bottom - top + 1, 8, 8, right - left + 1, bottom - top + 1);
    return cropped;
  };
  async function setSignature(blob) {
    const url = makeUrl(blob), image = new Image(); image.src = url;
    try { await image.decode(); }
    catch { revoke(url); throw new Error('Gambar tanda tangan tidak dapat dibaca.'); }
    if (image.naturalWidth * image.naturalHeight > 2000000) { revoke(url); throw new Error('Tanda tangan maksimal 2 megapiksel.'); }
    revoke(signatureUrl); revoke(whitePreviewUrl); whitePreviewUrl = null;
    signature = blob; signatureUrl = url; signatureAspect = image.naturalWidth / image.naturalHeight;
    const previewCanvas = document.createElement('canvas'); previewCanvas.width = image.naturalWidth; previewCanvas.height = image.naturalHeight;
    const previewCtx = previewCanvas.getContext('2d'); previewCtx.drawImage(image, 0, 0);
    const pixels = previewCtx.getImageData(0, 0, previewCanvas.width, previewCanvas.height);
    for (let i = 0; i < pixels.data.length; i += 4) if (Math.min(pixels.data[i], pixels.data[i + 1], pixels.data[i + 2]) >= 245) pixels.data[i + 3] = 0;
    previewCtx.putImageData(pixels, 0, 0);
    const previewBlob = await new Promise((resolve) => previewCanvas.toBlob(resolve, 'image/png'));
    if (previewBlob) whitePreviewUrl = makeUrl(previewBlob);
    // A new source invalidates old placements so preview and export agree.
    placements = []; selected = null; renderPlacements(); updateEditor();
    $('sign-status').textContent = 'Tanda tangan siap. Klik “Letakkan di halaman ini”.';
  }
  $('signature-use').addEventListener('click', () => {
    if (!ink) { $('sign-status').textContent = 'Gambar tanda tangan terlebih dahulu.'; return; }
    cropCanvas().toBlob((blob) => { if (blob) void setSignature(blob).catch((e) => { $('sign-status').textContent = e.message; }); }, 'image/png');
  });
  $('signature-file').addEventListener('change', async (event) => {
    try { const file = event.target.files[0]; checkFile(file, ['png', 'jpg', 'jpeg', 'webp'], 1024 * 1024); await setSignature(file); }
    catch (error) { $('sign-status').textContent = error.message; }
  });

  function updateEditor() {
    ['sign-file', 'signature-file', 'signature-use', 'signature-clear', 'signature-white', 'sign-metadata'].forEach((id) => { $(id).disabled = loading; });
    $('sign-prev').disabled = loading || !pdfFile || page <= 1;
    $('sign-next').disabled = loading || !pdfFile || page >= pages;
    $('signature-add').disabled = loading || !signature || !pageUrl || placements.length >= 30;
    $('sign-export').disabled = loading || !pdfFile || !signature || !placements.length;
    $('signature-remove').disabled = loading || !selected;
    $('signature-width').disabled = $('signature-x').disabled = $('signature-y').disabled = loading || !selected;
    $('signature-count').textContent = `${placements.length} tanda tangan · ${placements.filter((p) => p.page === page).length} di halaman ini`;
    $('sign-page-label').textContent = pages ? `Halaman ${page} / ${pages}` : 'Halaman —';
    if (selected) {
      $('signature-width').value = selected.w * 100;
      $('signature-x').value = (selected.x * 100).toFixed(1); $('signature-y').value = (selected.y * 100).toFixed(1);
    }
  }
  function renderPlacements() {
    const layer = $('signature-layer'); layer.replaceChildren();
    placements.filter((p) => p.page === page).forEach((p) => {
      const box = document.createElement('button'); box.type = 'button'; box.className = 'signature-box';
      box.classList.toggle('selected', p === selected); box.setAttribute('aria-label', 'Pilih dan geser tanda tangan');
      box.style.left = `${p.x * 100}%`; box.style.top = `${p.y * 100}%`; box.style.width = `${p.w * 100}%`; box.style.height = `${p.h * 100}%`;
      const image = document.createElement('img'); image.src = $('signature-white').checked && whitePreviewUrl ? whitePreviewUrl : signatureUrl; image.alt = 'Tanda tangan visual'; image.draggable = false; box.append(image);
      box.addEventListener('click', () => { selected = p; renderPlacements(); updateEditor(); });
      box.addEventListener('pointerdown', (e) => {
        if (loading) return;
        e.preventDefault(); selected = p; layer.querySelectorAll('.signature-box').forEach((b) => b.classList.toggle('selected', b === box)); updateEditor();
        box.setPointerCapture(e.pointerId);
        const r = $('sign-stage').getBoundingClientRect(), startX = e.clientX, startY = e.clientY, x = p.x, y = p.y;
        const move = (event) => { p.x = clamp(x + (event.clientX - startX) / r.width, 0, 1 - p.w); p.y = clamp(y + (event.clientY - startY) / r.height, 0, 1 - p.h); box.style.left = `${p.x * 100}%`; box.style.top = `${p.y * 100}%`; updateEditor(); };
        const end = () => { box.removeEventListener('pointermove', move); box.removeEventListener('pointerup', end); box.removeEventListener('pointercancel', end); };
        box.addEventListener('pointermove', move); box.addEventListener('pointerup', end); box.addEventListener('pointercancel', end);
      });
      layer.append(box);
    });
  }
  async function showPage(target) {
    if (!pdfFile || loading) return;
    const seq = ++previewSequence, file = pdfFile;
    loading = true; selected = null; updateEditor(); $('sign-status').textContent = `Memuat halaman ${target}…`;
    try {
      const response = await request('/api/pdf-editor-preview', file, {page: target}); const payload = await response.json();
      if (seq !== previewSequence || file !== pdfFile) return;
      const raw = Uint8Array.from(atob(payload.image), (c) => c.charCodeAt(0));
      const url = makeUrl(new Blob([raw], {type: 'image/jpeg'}));
      const testImage = new Image(); testImage.src = url; await testImage.decode();
      revoke(pageUrl); pageUrl = url; page = target; pages = payload.pages; pageWidth = payload.width; pageHeight = payload.height;
      $('sign-page').src = url; $('sign-page').hidden = false; $('sign-empty').hidden = true;
      renderPlacements(); $('sign-status').textContent = 'Pratinjau siap. Tambahkan atau geser tanda tangan.';
    } catch (error) { $('sign-status').textContent = error.message; }
    finally { loading = false; updateEditor(); }
  }
  $('sign-file').addEventListener('change', async (event) => {
    // Prevent changing the source while a request is running.
    if (loading) { $('sign-status').textContent = 'Tunggu proses halaman selesai sebelum mengganti PDF.'; return; }
    try {
      const file = event.target.files[0]; checkFile(file, ['pdf']); pdfFile = file; page = 1; pages = 0;
      placements = []; selected = null; revoke(pageUrl); pageUrl = null;
      $('sign-page').hidden = true; $('sign-empty').hidden = false; $('sign-result').replaceChildren(); renderPlacements(); await showPage(1);
    } catch (error) { $('sign-status').textContent = error.message; }
  });
  $('sign-prev').addEventListener('click', () => { void showPage(page - 1); });
  $('sign-next').addEventListener('click', () => { void showPage(page + 1); });
  $('signature-add').addEventListener('click', () => {
    const w = Math.min(.25, .8 * signatureAspect * pageHeight / pageWidth), h = w * pageWidth / (signatureAspect * pageHeight);
    selected = {page, x: .1, y: Math.min(.75, 1 - h), w, h}; placements.push(selected); renderPlacements(); updateEditor();
  });
  $('signature-width').addEventListener('input', () => {
    if (!selected) return;
    const ratio = selected.h / selected.w;
    selected.w = Math.min(Number($('signature-width').value) / 100, .9 / ratio);
    selected.h = selected.w * ratio; selected.x = Math.min(selected.x, 1 - selected.w); selected.y = Math.min(selected.y, 1 - selected.h);
    renderPlacements(); updateEditor();
  });
  ['x', 'y'].forEach((axis) => $('signature-' + axis).addEventListener('change', () => {
    if (!selected) return;
    const value = Number($('signature-' + axis).value);
    if (!Number.isFinite(value)) return;
    selected[axis] = clamp(value / 100, 0, 1 - selected[axis === 'x' ? 'w' : 'h']); renderPlacements(); updateEditor();
  }));
  $('signature-remove').addEventListener('click', () => { placements = placements.filter((p) => p !== selected); selected = null; renderPlacements(); updateEditor(); });
  $('signature-white').addEventListener('change', renderPlacements);
  $('sign-export').addEventListener('click', async () => {
    if (loading || !pdfFile || !signature || !placements.length) return;
    const file = pdfFile, source = signature;
    const settings = {pdf_size: file.size, placements: placements.map((p) => ({...p})), remove_white: $('signature-white').checked, remove_metadata: $('sign-metadata').checked};
    loading = true; updateEditor(); $('sign-status').textContent = 'Menempatkan tanda tangan dan menyiapkan PDF…';
    try {
      const response = await request('/api/pdf-sign', file, settings, new Blob([file, source])); const result = await response.blob();
      const name = response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] || 'privasidoc-tanda-tangan.pdf';
      const info = JSON.parse(response.headers.get('X-Document-Info'));
      $('sign-status').textContent = `PDF siap · ${info.signatures} tanda tangan visual · ${bytesLabel(result.size)}.`;
      const button = document.createElement('button'); button.className = 'secondary-button'; button.type = 'button'; button.textContent = '↓ Unduh PDF';
      button.addEventListener('click', () => download(result, name)); $('sign-result').replaceChildren(button); download(result, name);
    } catch (error) { $('sign-status').textContent = error.message; }
    finally { loading = false; updateEditor(); }
  });
})();
