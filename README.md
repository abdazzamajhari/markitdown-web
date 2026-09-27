# MarkItDown Web

Aplikasi web mandiri untuk mengonversi dokumen ke Markdown menggunakan [microsoft/markitdown](https://github.com/microsoft/markitdown). Proyek ini **bukan** fork atau layanan resmi Microsoft.

## Fitur dan batasan

- Antarmuka web dan endpoint `POST /api/convert`, satu berkas per permintaan.
- Format: PDF, DOCX, PPTX, XLSX, TXT, CSV, JSON. Maksimum 10 MB per berkas dan 2 MB hasil Markdown.
- Kunci API wajib; berkas tidak ditulis ke penyimpanan permanen. Worker terpisah dibatasi waktu 30 detik, CPU 25 detik, dan ruang alamat 1 GiB (Linux).
- Tidak menerima URL, path server, HTML, ZIP, plugin, atau layanan AI/OCR eksternal. PDF berbasis gambar tanpa lapisan teks dapat menghasilkan teks kosong; gambar tidak di-OCR oleh konfigurasi ini.
- Maksimum satu proses konversi aktif per instans. Permintaan lain mendapat HTTP 429. Untuk beban tinggi perlu antrian kerja dan pengaturan kapasitas terpisah.

## Menjalankan lokal

Python 3.12:

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
export WEB_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Buka <http://127.0.0.1:8000>. Di Windows PowerShell, atur variabel dengan `$env:WEB_API_KEY = 'kunci-rahasia-panjang'` sebelum menjalankan Uvicorn.

Docker:

```bash
docker build -t markitdown-web .
docker run --rm -p 8000:8000 -e WEB_API_KEY='kunci-rahasia-panjang' --memory=2g --pids-limit=128 --cap-drop=ALL --security-opt=no-new-privileges markitdown-web
```

## API

Kirim isi berkas sebagai body biner mentah; `X-Filename` berisi nama berkas yang di-URL-encode. Contoh:

```bash
curl -f -X POST 'http://127.0.0.1:8000/api/convert' \
  -H "Authorization: Bearer $WEB_API_KEY" \
  -H 'X-Filename: contoh.pdf' \
  -H 'Content-Type: application/octet-stream' \
  --data-binary '@contoh.pdf' -o contoh.md
```

`GET /health` tidak memerlukan kunci dan mengembalikan `{"status":"ok"}`. Kesalahan mengembalikan JSON `{"detail":"..."}`. Kunci hanya dikirim dalam header, jangan taruh di URL atau repositori. Gunakan HTTPS saat akses dari internet.

## GitHub dan deployment Render

1. Sumber kode tersedia di [abdazzamajhari/markitdown-web](https://github.com/abdazzamajhari/markitdown-web). Jangan menambahkan berkas `.env` atau kunci ke Git.
2. Di Render, pilih **New → Blueprint**, sambungkan repositori GitHub tersebut, dan gunakan `render.yaml`. Saat pembuatan, masukkan `WEB_API_KEY` berupa nilai acak dari `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
3. Setelah build selesai, buka alamat `https://...onrender.com` dan masukkan kunci yang sama di formulir. Uji `https://...onrender.com/health`.

Blueprint memakai paket `free` untuk percobaan awal. Kapasitas 512 MB dan batas layanan gratis dapat menggagalkan konversi dokumen besar; pilih paket dengan memori memadai setelah mengukur kebutuhan dan memeriksa biaya di dashboard. `checksPass` menunggu CI GitHub berhasil sebelum auto-deploy. Pada pembaruan Blueprint, nilai `sync: false` yang baru perlu disetel manual di dashboard Render.

GitHub Pages hanya meng-host berkas statis dan tidak dapat menjalankan backend Python. Layanan web ini harus ditempatkan di runtime Python atau Docker. GitHub menyimpan sumber kode; Render menjalankan situs dari repositori itu.

## Pengujian dan operasi

```bash
pip install -r requirements-dev.txt
pytest -q
```

Workflow GitHub Actions menjalankan pengujian dan build Docker pada push dan pull request. Rotasi `WEB_API_KEY` di dashboard bila kunci bocor. Terapkan batas request di reverse proxy, pemantauan memori/CPU, dan pembatasan trafik per IP sebelum membuka layanan untuk publik berskala besar. Batas waktu dan memori worker bukan isolasi keamanan setara sandbox kernel; jalankan container tanpa hak istimewa dan tanpa mount rahasia. Upgrade dependensi secara berkala dan jalankan CI sebelum rilis.
