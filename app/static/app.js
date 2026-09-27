const zone = document.querySelector('#dropzone');
const picker = document.querySelector('#picker');
const list = document.querySelector('#files');
const status = document.querySelector('#status');
const notice = document.querySelector('#notice');
const ocrMode = document.querySelector('#ocr-mode');
const emptyState = document.querySelector('#empty-state');
const downloadAll = document.querySelector('#download-all');
const downloadCombined = document.querySelector('#download-combined');
const queue = [];
const completed = [];
const objectUrls = new Set();
const supported = new Set(['pdf', 'docx', 'pptx', 'xlsx', 'txt', 'csv', 'json', 'png', 'jpg', 'jpeg', 'webp']);
const imageFormats = new Set(['png', 'jpg', 'jpeg', 'webp']);
const maxBytes = 10 * 1024 * 1024;
const maxQueue = 12;
let running = false;
let failed = 0;

function formatBytes(bytes) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
function updateStatus() {
  const waiting = queue.length + Number(running);
  status.textContent = `${completed.length} selesai · ${failed} gagal · ${waiting} dalam antrean/proses`;
  downloadAll.disabled = downloadCombined.disabled = !completed.length;
}
function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  objectUrls.add(url);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => { URL.revokeObjectURL(url); objectUrls.delete(url); }, 60000);
}
function singleName(name) {
  return `${name.replace(/\.[^.]+$/, '').replace(/[\\/\x00-\x1f\x7f<>:"|?*]/g, '_') || 'document'}.md`;
}
function makeRow(file) {
  const extension = file.name.split('.').pop().toLowerCase();
  const row = document.createElement('li');
  row.className = 'file-row';
  const head = document.createElement('div');
  head.className = 'file-head';
  const icon = document.createElement('span');
  icon.className = 'file-icon';
  icon.textContent = extension.slice(0, 4);
  const meta = document.createElement('div');
  meta.className = 'file-meta';
  const name = document.createElement('strong');
  name.textContent = file.name;
  const size = document.createElement('small');
  size.textContent = formatBytes(file.size);
  meta.append(name, size);
  const state = document.createElement('span');
  state.className = 'file-state';
  state.textContent = 'Menunggu giliran';
  head.append(icon, meta, state);
  const progress = document.createElement('progress');
  progress.max = 100;
  progress.value = 0;
  progress.setAttribute('aria-label', `Progres ${file.name}`);
  row.append(head, progress);
  list.append(row);
  return {row, state, progress, file, extension};
}
function markError(item, message) {
  item.row.classList.add('error');
  item.state.textContent = message;
  item.progress.value = 0;
  failed += 1;
  updateStatus();
}
function addFiles(files) {
  const selected = Array.from(files);
  if (!selected.length) return;
  let omitted = 0;
  for (const file of selected) {
    if (queue.length + Number(running) >= maxQueue) { omitted += 1; continue; }
    const item = makeRow(file);
    if (!supported.has(item.extension)) markError(item, 'Format tidak didukung');
    else if (!file.size || file.size > maxBytes) markError(item, 'Ukuran harus 1 byte–10 MB');
    else queue.push(item);
  }
  updateStatus();
  notice.textContent = omitted ? `${omitted} berkas belum dimasukkan (maksimum 12 antrean). Letakkan lagi setelah antrean berkurang.` : '';
  void processQueue();
}
function upload(item) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/convert');
    xhr.timeout = 180000;
    xhr.setRequestHeader('X-Filename', encodeURIComponent(item.file.name));
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable) {
        const percent = Math.round(event.loaded / event.total * 100);
        item.progress.value = percent;
        item.state.textContent = `Mengunggah ${percent}%`;
      }
    });
    xhr.upload.addEventListener('load', () => {
      item.progress.removeAttribute('value');
      item.state.textContent = 'Mengekstraksi teks…';
    });
    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve({markdown: xhr.responseText, engine: xhr.getResponseHeader('X-OCR-Engine')});
      } else {
        let message = `HTTP ${xhr.status}`;
        try { message = JSON.parse(xhr.responseText).detail || message; } catch { /* Use HTTP status. */ }
        reject(new Error(message));
      }
    });
    xhr.addEventListener('error', () => reject(new Error('Koneksi terputus')));
    xhr.addEventListener('timeout', () => reject(new Error('Permintaan melebihi batas waktu')));
    xhr.send(item.file);
  });
}

function makePane(title, hint) {
  const pane = document.createElement('div');
  pane.className = 'detail-pane';
  const heading = document.createElement('div');
  heading.className = 'detail-pane-head';
  const label = document.createElement('span');
  label.textContent = title;
  const secondary = document.createElement('small');
  secondary.textContent = hint;
  heading.append(label, secondary);
  pane.append(heading);
  return pane;
}
function showRegions(layer, regions, message) {
  layer.replaceChildren();
  if (!Array.isArray(regions)) throw new Error('Detail OCR tidak valid');
  for (const region of regions) {
    if (![region.x, region.y, region.w, region.h].every(Number.isFinite)) continue;
    const box = document.createElement('button');
    box.type = 'button';
    box.className = 'region-box';
    box.style.left = `${Math.max(0, Math.min(100, region.x * 100))}%`;
    box.style.top = `${Math.max(0, Math.min(100, region.y * 100))}%`;
    box.style.width = `${Math.max(0, Math.min(100, region.w * 100))}%`;
    box.style.height = `${Math.max(0, Math.min(100, region.h * 100))}%`;
    box.title = region.text;
    box.setAttribute('aria-label', `Teks OCR lokal: ${region.text}`);
    box.addEventListener('click', () => {
      layer.querySelector('.selected')?.classList.remove('selected');
      box.classList.add('selected');
      message.textContent = `Teks pada area: ${region.text}`;
    });
    layer.append(box);
  }
  message.textContent = layer.childElementCount
    ? `${layer.childElementCount} area terdeteksi oleh OCR lokal. Klik kotak untuk membaca teks per area.`
    : 'OCR lokal tidak menemukan area teks pada halaman ini. Hasil Markdown tetap tersedia di sebelahnya.';
}
function renderDetail(item, markdown, engine) {
  const actions = document.createElement('div');
  actions.className = 'file-actions';
  const single = document.createElement('button');
  single.type = 'button';
  single.className = 'small-button';
  single.textContent = '↓ Unduh .md';
  single.addEventListener('click', () => saveBlob(new Blob([markdown], {type: 'text/markdown;charset=utf-8'}), singleName(item.file.name)));
  const copy = document.createElement('button');
  copy.type = 'button';
  copy.className = 'small-button';
  copy.textContent = 'Salin teks';
  copy.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(markdown); copy.textContent = 'Tersalin ✓'; }
    catch { notice.textContent = 'Penyalinan gagal. Pilih teks dari pratinjau dan salin secara manual.'; }
    setTimeout(() => { copy.textContent = 'Salin teks'; }, 2500);
  });
  actions.append(single, copy);
  const details = document.createElement('details');
  details.className = 'result-detail';
  const summary = document.createElement('summary');
  summary.textContent = imageFormats.has(item.extension) ? 'Gambar dan kotak OCR · klik untuk sembunyikan' : 'Lihat detail teks yang diekstraksi';
  const grid = document.createElement('div');
  grid.className = 'detail-grid';
  if (!imageFormats.has(item.extension) && item.extension !== 'pdf') grid.classList.add('text-only');
  let loadRegions;
  if (imageFormats.has(item.extension)) {
    details.open = true;
    const source = makePane('Gambar sumber', 'Kotak: OCR lokal');
    const zoom = document.createElement('button');
    zoom.type = 'button';
    zoom.className = 'zoom-button';
    zoom.textContent = 'Perbesar gambar';
    zoom.addEventListener('click', () => {
      const expanded = grid.classList.toggle('zoomed');
      zoom.textContent = expanded ? 'Kembalikan ukuran' : 'Perbesar gambar';
    });
    source.querySelector('.detail-pane-head').append(zoom);
    const legend = document.createElement('div');
    legend.className = 'region-legend';
    legend.textContent = '▣ Kotak merah menandai area OCR lokal. Klik kotaknya untuk melihat teks.';
    source.append(legend);
    const scroll = document.createElement('div');
    scroll.className = 'image-scroll';
    const frame = document.createElement('div');
    frame.className = 'image-frame';
    const image = document.createElement('img');
    image.alt = `Gambar sumber ${item.file.name}`;
    image.loading = 'lazy';
    const layer = document.createElement('div');
    layer.className = 'region-layer';
    frame.append(image, layer);
    scroll.append(frame);
    const regionMessage = document.createElement('p');
    regionMessage.className = 'region-message';
    regionMessage.textContent = 'Menyiapkan kotak OCR…';
    source.append(scroll, regionMessage);
    grid.append(source);
    let loaded = false, loading = false;
    loadRegions = async () => {
      if (loaded || loading) return;
      loading = true;
      if (!image.src) {
        image.src = URL.createObjectURL(item.file);
        objectUrls.add(image.src);
      }
      regionMessage.textContent = 'Memetakan lokasi teks dengan OCR lokal…';
      try {
        const response = await fetch('/api/regions', {
          method: 'POST', headers: {'X-Filename': encodeURIComponent(item.file.name), 'Content-Type': 'application/octet-stream'},
          body: item.file,
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
        showRegions(layer, payload.regions, regionMessage);
        loaded = true;
      } catch (error) {
        regionMessage.textContent = `${error.message}. Tutup lalu buka detail untuk mencoba lagi.`;
      } finally { loading = false; }
    };
  } else if (item.extension === 'pdf') {
    const source = makePane('Halaman PDF dan kotak OCR', 'Kotak: OCR lokal');
    const zoom = document.createElement('button');
    zoom.type = 'button';
    zoom.className = 'zoom-button';
    zoom.textContent = 'Perbesar halaman';
    zoom.addEventListener('click', () => {
      const expanded = grid.classList.toggle('zoomed');
      zoom.textContent = expanded ? 'Kembalikan ukuran' : 'Perbesar halaman';
    });
    source.querySelector('.detail-pane-head').append(zoom);
    const controls = document.createElement('div');
    controls.className = 'page-controls';
    const previous = document.createElement('button');
    previous.type = 'button'; previous.textContent = '← Sebelumnya'; previous.disabled = true;
    const pageLabel = document.createElement('span');
    pageLabel.textContent = 'Halaman 1';
    const next = document.createElement('button');
    next.type = 'button'; next.textContent = 'Berikutnya →'; next.disabled = true;
    const retry = document.createElement('button');
    retry.type = 'button'; retry.textContent = 'Coba lagi'; retry.hidden = true;
    controls.append(previous, pageLabel, next, retry);
    const legend = document.createElement('div');
    legend.className = 'region-legend';
    legend.textContent = '▣ Kotak merah menandai tulisan pada halaman PDF. Klik kotak untuk membaca teks.';
    const scroll = document.createElement('div');
    scroll.className = 'image-scroll pdf-preview-scroll is-loading';
    scroll.setAttribute('aria-busy', 'true');
    const frame = document.createElement('div');
    frame.className = 'image-frame';
    frame.hidden = true;
    const image = document.createElement('img');
    image.alt = `Halaman PDF ${item.file.name}`;
    image.hidden = true;
    const layer = document.createElement('div');
    layer.className = 'region-layer';
    frame.append(image, layer);
    const previewStatus = document.createElement('div');
    previewStatus.className = 'pdf-preview-status';
    previewStatus.setAttribute('role', 'status');
    previewStatus.setAttribute('aria-live', 'polite');
    previewStatus.textContent = 'Memuat halaman 1 dan kotak OCR…';
    scroll.append(frame, previewStatus);
    const regionMessage = document.createElement('p');
    regionMessage.className = 'region-message';
    regionMessage.textContent = 'Menunggu gambar dan hasil OCR halaman 1…';
    source.append(controls, legend, scroll, regionMessage);
    grid.append(source);
    let currentPage = 1, totalPages = 0, loading = false, loaded = false, previewUrl = null;
    const pageCache = new Map();
    const showPage = async (page, refresh = false) => {
      if (loading) return;
      loading = true;
      let pendingUrl = null;
      previous.disabled = next.disabled = retry.disabled = true;
      retry.hidden = true;
      frame.hidden = true;
      previewStatus.hidden = false;
      previewStatus.classList.remove('is-error');
      previewStatus.textContent = `Memuat halaman ${page} dan kotak OCR…`;
      scroll.classList.add('is-loading');
      scroll.setAttribute('aria-busy', 'true');
      regionMessage.textContent = `Memuat halaman ${page} dan kotak OCR…`;
      try {
        let payload = !refresh && pageCache.get(page);
        if (!payload) {
          const response = await fetch(`/api/pdf-preview?page=${page}`, {
            method: 'POST', headers: {'X-Filename': encodeURIComponent(item.file.name), 'Content-Type': 'application/octet-stream'},
            body: item.file,
          });
          payload = await response.json();
          if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
        }
        if (payload.page !== page || !Number.isInteger(payload.total_pages) ||
            payload.total_pages < page || !Array.isArray(payload.regions) ||
            (payload.image !== null && !payload.image?.startsWith('data:image/jpeg;base64,'))) {
          throw new Error('Pratinjau PDF tidak valid');
        }
        if (payload.image) {
          const jpeg = Uint8Array.from(atob(payload.image.split(',', 2)[1]), character => character.charCodeAt(0));
          pendingUrl = URL.createObjectURL(new Blob([jpeg], {type: 'image/jpeg'}));
          objectUrls.add(pendingUrl);
          image.src = pendingUrl;
          image.hidden = false;
          await image.decode();
        }
        if (previewUrl) {
          URL.revokeObjectURL(previewUrl);
          objectUrls.delete(previewUrl);
        }
        previewUrl = pendingUrl;
        pendingUrl = null;
        if (!previewUrl) image.removeAttribute('src');
        image.hidden = !previewUrl;
        currentPage = payload.page;
        totalPages = payload.total_pages;
        pageLabel.textContent = `Halaman ${currentPage} dari ${totalPages}`;
        showRegions(layer, payload.regions, regionMessage);
        if (payload.warning) regionMessage.textContent = payload.warning;
        frame.hidden = !previewUrl;
        previewStatus.hidden = !!previewUrl;
        previewStatus.classList.toggle('is-error', !previewUrl);
        if (!previewUrl) previewStatus.textContent = 'Halaman ini belum dapat ditampilkan.';
        retry.hidden = !payload.warning;
        if (!payload.warning) pageCache.set(page, payload);
        loaded = true;
      } catch (error) {
        if (pendingUrl) { URL.revokeObjectURL(pendingUrl); objectUrls.delete(pendingUrl); }
        if (previewUrl) { URL.revokeObjectURL(previewUrl); objectUrls.delete(previewUrl); previewUrl = null; }
        image.removeAttribute('src'); image.hidden = true;
        frame.hidden = true;
        layer.replaceChildren();
        if (totalPages) {
          currentPage = page;
          pageLabel.textContent = `Halaman ${currentPage} dari ${totalPages}`;
        }
        previewStatus.hidden = false;
        previewStatus.classList.add('is-error');
        previewStatus.textContent = `Halaman ${page} belum dapat ditampilkan.`;
        retry.hidden = false;
        regionMessage.textContent = `${error.message}. Gunakan Coba lagi atau lanjut ke halaman berikutnya.`;
      } finally {
        loading = false;
        scroll.classList.remove('is-loading');
        scroll.setAttribute('aria-busy', 'false');
        previous.disabled = !loaded || currentPage <= 1;
        next.disabled = !loaded || currentPage >= totalPages;
        retry.disabled = false;
      }
    };
    previous.addEventListener('click', () => void showPage(currentPage - 1));
    next.addEventListener('click', () => void showPage(currentPage + 1));
    retry.addEventListener('click', () => void showPage(currentPage, true));
    loadRegions = async () => { if (!loaded) await showPage(1); };
  }
  const engineLabel = engine === 'sumopod' ? 'SumoPod · gpt-4o-mini' : engine === 'tesseract-pdf-partial' ? 'OCR lokal · sebagian' : engine === 'tesseract' || engine === 'tesseract-pdf' ? 'OCR lokal' : 'MarkItDown';
  const transcript = makePane('Teks terdeteksi & terekstraksi', engineLabel);
  const pre = document.createElement('pre');
  pre.className = 'transcript';
  pre.textContent = markdown || '(Tidak ada teks terdeteksi)';
  transcript.append(pre);
  grid.append(transcript);
  const note = document.createElement('p');
  note.className = 'detail-note';
  note.textContent = imageFormats.has(item.extension)
    ? 'Kotak dan teks per area berasal dari Tesseract lokal. Hasil Markdown di sebelahnya berasal dari mesin yang tertera dan belum disejajarkan dengan kotak secara otomatis.'
    : item.extension === 'pdf'
      ? 'Gunakan tombol Sebelumnya/Berikutnya untuk menelusuri halaman. Kotak dibuat oleh Tesseract lokal; pada PDF dengan lapisan teks, Markdown berasal dari MarkItDown sehingga keduanya dapat berbeda.'
    : 'Pratinjau ini memperlihatkan seluruh Markdown yang dihasilkan. Unduhan per berkas dan unduhan massal tersedia di atas.';
  details.append(summary, grid, note);
  if (loadRegions) details.addEventListener('toggle', () => { if (details.open) void loadRegions(); });
  item.row.append(actions, details);
  return loadRegions;
}

async function processQueue() {
  if (running) return;
  running = true;
  while (queue.length) {
    const item = queue.shift();
    item.state.textContent = 'Mengunggah…';
    updateStatus();
    try {
      const {markdown, engine} = await upload(item);
      item.progress.value = 100;
      item.row.classList.add('done');
      const label = engine === 'sumopod' ? 'SumoPod · gpt-4o-mini' : engine === 'tesseract-pdf-partial' ? 'OCR lokal · sebagian' : engine === 'tesseract' || engine === 'tesseract-pdf' ? 'OCR lokal' : 'MarkItDown';
      item.state.textContent = markdown.trim() ? `Selesai (${label})` : `Selesai (${label}) · tidak ada teks`;
      completed.push({name: item.file.name, markdown});
      emptyState.hidden = true;
      const loadRegions = renderDetail(item, markdown, engine);
      updateStatus();
      if (loadRegions && imageFormats.has(item.extension)) await loadRegions();
    } catch (error) { markError(item, error.message || 'Konversi gagal'); }
    updateStatus();
  }
  running = false;
  updateStatus();
}

downloadAll.addEventListener('click', () => {
  try { saveBlob(createMarkdownZip(completed), 'markitdown-hasil.zip'); }
  catch { notice.textContent = 'Gagal menyiapkan ZIP. Coba unduh hasil per berkas.'; }
});
downloadCombined.addEventListener('click', () => {
  const text = completed.map(({name, markdown}) => `# ${name.replace(/[\r\n]/g, ' ')}\n\n${markdown}`).join('\n\n---\n\n');
  saveBlob(new Blob([text], {type: 'text/markdown;charset=utf-8'}), 'markitdown-gabungan.md');
});
for (const box of document.querySelectorAll('.demo-box')) {
  box.addEventListener('click', () => {
    document.querySelector('.demo-box.selected')?.classList.remove('selected');
    box.classList.add('selected');
    document.querySelector('#demo-text').textContent = box.dataset.demo;
  });
}
zone.addEventListener('click', () => picker.click());
picker.addEventListener('change', () => { addFiles(picker.files); picker.value = ''; });
for (const eventName of ['dragenter', 'dragover']) {
  zone.addEventListener(eventName, (event) => { event.preventDefault(); zone.classList.add('dragging'); });
}
zone.addEventListener('dragleave', (event) => { if (!zone.contains(event.relatedTarget)) zone.classList.remove('dragging'); });
zone.addEventListener('drop', (event) => {
  event.preventDefault(); event.stopPropagation(); zone.classList.remove('dragging');
  addFiles(event.dataTransfer.files);
});
document.addEventListener('dragover', (event) => {
  if (event.dataTransfer?.types?.includes('Files')) { event.preventDefault(); document.body.classList.add('page-dragging'); }
});
document.addEventListener('dragleave', (event) => { if (!event.relatedTarget) document.body.classList.remove('page-dragging'); });
document.addEventListener('drop', (event) => {
  if (event.dataTransfer?.files?.length) { event.preventDefault(); addFiles(event.dataTransfer.files); }
  document.body.classList.remove('page-dragging');
});
window.addEventListener('pagehide', () => { for (const url of objectUrls) URL.revokeObjectURL(url); });
fetch('/api/capabilities', {cache: 'no-store'})
  .then((response) => response.json())
  .then(({image_ocr, image_ocr_model}) => {
    ocrMode.textContent = image_ocr === 'sumopod'
      ? `OCR gambar: SumoPod (${image_ocr_model}). Gambar dikirim ke SumoPod; pemetaan kotak memakai OCR lokal.`
      : 'OCR gambar: lokal (Indonesia dan Inggris). Gambar tidak dikirim ke penyedia AI.';
  })
  .catch(() => { ocrMode.textContent = 'Status layanan OCR tidak tersedia.'; });
