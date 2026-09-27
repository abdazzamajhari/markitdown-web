const zone = document.querySelector('#dropzone');
const picker = document.querySelector('#picker');
const list = document.querySelector('#files');
const status = document.querySelector('#status');
const notice = document.querySelector('#notice');
const ocrMode = document.querySelector('#ocr-mode');
const queue = [];
const downloads = new Set();
const supported = new Set(['pdf', 'docx', 'pptx', 'xlsx', 'txt', 'csv', 'json', 'png', 'jpg', 'jpeg', 'webp']);
const maxBytes = 10 * 1024 * 1024;
const maxQueue = 12;
let running = false;
let finished = 0;
let failed = 0;

function updateStatus() {
  const waiting = queue.length + Number(running);
  status.textContent = `${finished} selesai · ${failed} gagal · ${waiting} dalam antrean/proses`;
}

function makeRow(file) {
  const row = document.createElement('li');
  row.className = 'file-row';
  const head = document.createElement('div');
  head.className = 'file-head';
  const name = document.createElement('strong');
  name.textContent = file.name;
  const state = document.createElement('span');
  state.className = 'file-state';
  state.textContent = 'Menunggu giliran';
  head.append(name, state);
  const progress = document.createElement('progress');
  progress.max = 100;
  progress.value = 0;
  progress.setAttribute('aria-label', `Progres ${file.name}`);
  row.append(head, progress);
  list.append(row);
  return {row, state, progress};
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
    if (queue.length + Number(running) >= maxQueue) {
      omitted += 1;
      continue;
    }
    const item = {...makeRow(file), file};
    const extension = file.name.split('.').pop().toLowerCase();
    if (!supported.has(extension)) {
      markError(item, 'Format tidak didukung');
    } else if (!file.size || file.size > maxBytes) {
      markError(item, 'Ukuran harus 1 byte–10 MB');
    } else {
      queue.push(item);
    }
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
      item.state.textContent = 'Memproses dokumen/OCR…';
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
      const label = engine === 'olmocr2' ? 'olmOCR 2' : engine === 'tesseract' ? 'OCR lokal' : 'MarkItDown';
      item.state.textContent = markdown.trim() ? `Selesai (${label})` : `Selesai (${label}) — tidak ada teks terdeteksi`;
      if (markdown.trim()) {
        const actions = document.createElement('div');
        actions.className = 'file-actions';
        const download = document.createElement('a');
        const url = URL.createObjectURL(new Blob([markdown], {type: 'text/markdown;charset=utf-8'}));
        downloads.add(url);
        download.href = url;
        download.download = `${item.file.name.replace(/\.[^.]+$/, '')}.md`;
        download.textContent = 'Unduh .md';
        const details = document.createElement('details');
        const summary = document.createElement('summary');
        summary.textContent = 'Lihat hasil';
        const preview = document.createElement('pre');
        preview.textContent = markdown;
        details.append(summary, preview);
        actions.append(download, details);
        item.row.append(actions);
      }
      finished += 1;
    } catch (error) {
      markError(item, error.message || 'Konversi gagal');
    }
    updateStatus();
  }
  running = false;
  updateStatus();
}

zone.addEventListener('click', () => picker.click());
picker.addEventListener('change', () => {
  addFiles(picker.files);
  picker.value = '';
});
for (const eventName of ['dragenter', 'dragover']) {
  zone.addEventListener(eventName, (event) => {
    event.preventDefault();
    zone.classList.add('dragging');
  });
}
zone.addEventListener('dragleave', (event) => {
  if (!zone.contains(event.relatedTarget)) zone.classList.remove('dragging');
});
zone.addEventListener('drop', (event) => {
  event.preventDefault();
  zone.classList.remove('dragging');
  addFiles(event.dataTransfer.files);
});
document.addEventListener('dragover', (event) => event.preventDefault());
document.addEventListener('drop', (event) => event.preventDefault());
window.addEventListener('pagehide', () => {
  for (const url of downloads) URL.revokeObjectURL(url);
});

fetch('/api/capabilities', {cache: 'no-store'})
  .then((response) => response.json())
  .then(({image_ocr}) => {
    ocrMode.textContent = image_ocr === 'olmocr2'
      ? 'olmOCR 2 terkonfigurasi melalui Hugging Face Inference Endpoint. Gambar dikirim ke endpoint tersebut; berkas lain tetap diproses di server ini.'
      : 'OCR lokal aktif untuk gambar bahasa Indonesia dan Inggris. Gambar tidak dikirim ke penyedia AI.';
  })
  .catch(() => { ocrMode.textContent = 'Status layanan OCR tidak tersedia.'; });
