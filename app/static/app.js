const form = document.querySelector('#form');
const fileInput = document.querySelector('#file');
const keyInput = document.querySelector('#key');
const status = document.querySelector('#status');
const preview = document.querySelector('#preview');
const result = document.querySelector('#result');
const link = document.querySelector('#download');
const submit = document.querySelector('#submit');
let objectUrl = null;

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const file = fileInput.files[0];
  if (!file) return;
  result.hidden = true;
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  objectUrl = null;
  if (file.size > 10 * 1024 * 1024) {
    status.textContent = 'Ukuran berkas melebihi 10 MB.';
    return;
  }
  submit.disabled = true;
  status.textContent = 'Mengonversi berkas…';
  try {
    const response = await fetch('/api/convert', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${keyInput.value}`,
        'X-Filename': encodeURIComponent(file.name),
        'Content-Type': 'application/octet-stream',
      },
      body: file,
      cache: 'no-store',
    });
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    const markdown = await response.text();
    preview.textContent = markdown || '(Hasil kosong)';
    objectUrl = URL.createObjectURL(new Blob([markdown], {type: 'text/markdown;charset=utf-8'}));
    link.href = objectUrl;
    link.download = `${file.name.replace(/\.[^.]+$/, '')}.md`;
    result.hidden = false;
    status.textContent = 'Konversi selesai.';
  } catch (error) {
    status.textContent = error.message || 'Gagal mengonversi berkas.';
  } finally {
    submit.disabled = false;
  }
});
