# MarkItDown Web

Aplikasi web mandiri untuk mengonversi dokumen ke Markdown menggunakan [microsoft/markitdown](https://github.com/microsoft/markitdown). Proyek ini **bukan** fork atau layanan resmi Microsoft.

## Fitur dan batasan

- Antarmuka web dan endpoint `POST /api/convert`, satu berkas per permintaan.
- Format: PDF, DOCX, PPTX, XLSX, TXT, CSV, JSON. Maksimum 10 MB per berkas dan 2 MB hasil Markdown.
- Akses publik tanpa kunci; berkas tidak ditulis ke penyimpanan permanen. Worker terpisah dibatasi waktu 30 detik, CPU 25 detik, dan ruang alamat 1 GiB (Linux).
- Tidak menerima URL, path server, HTML, ZIP, plugin, atau layanan AI/OCR eksternal. PDF berbasis gambar tanpa lapisan teks dapat menghasilkan teks kosong; gambar tidak di-OCR oleh konfigurasi ini.
- Maksimum satu proses konversi aktif dan 12 konversi per menit per instans. Permintaan selebihnya mendapat HTTP 429. Batas ini tidak menggantikan pembatasan trafik di tepi jaringan; untuk beban tinggi perlu antrian kerja dan pengaturan kapasitas terpisah.

## Menjalankan lokal

Python 3.12:

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Buka <http://127.0.0.1:8000>.

Docker:

```bash
docker build -t markitdown-web .
docker run --rm -p 8000:8000 --memory=2g --pids-limit=128 --cap-drop=ALL --security-opt=no-new-privileges markitdown-web
```

## API

Kirim isi berkas sebagai body biner mentah; `X-Filename` berisi nama berkas yang di-URL-encode. Contoh:

```bash
curl -f -X POST 'http://127.0.0.1:8000/api/convert' \
  -H 'X-Filename: contoh.pdf' \
  -H 'Content-Type: application/octet-stream' \
  --data-binary '@contoh.pdf' -o contoh.md
```

`GET /health` mengembalikan `{"status":"ok"}`. Kesalahan mengembalikan JSON `{"detail":"..."}`. Endpoint konversi dapat dipakai siapa saja yang mengetahui URL. Gunakan HTTPS saat akses dari internet.

## GitHub dan deployment Render

1. Sumber kode tersedia di [abdazzamajhari/markitdown-web](https://github.com/abdazzamajhari/markitdown-web).
2. Di Render, pilih **New → Blueprint**, sambungkan repositori GitHub tersebut, dan gunakan `render.yaml`. Tidak diperlukan variabel lingkungan untuk kunci.
3. Setelah build selesai, buka alamat `https://...onrender.com`, pilih dokumen, lalu klik **Konversi ke Markdown**. Uji `https://...onrender.com/health`.

Blueprint memakai paket `free` untuk percobaan awal. Kapasitas 512 MB dan batas layanan gratis dapat menggagalkan konversi dokumen besar; pilih paket dengan memori memadai setelah mengukur kebutuhan dan memeriksa biaya di dashboard. `checksPass` menunggu CI GitHub berhasil sebelum auto-deploy. Jika layanan lama masih menyimpan `WEB_API_KEY` di Dashboard Render, Anda boleh menghapusnya; aplikasi mengabaikannya.

GitHub Pages hanya meng-host berkas statis dan tidak dapat menjalankan backend Python. Layanan web ini harus ditempatkan di runtime Python atau Docker. GitHub menyimpan sumber kode; Render menjalankan situs dari repositori itu.

## Pengujian dan operasi

```bash
pip install -r requirements-dev.txt
pytest -q
```

Workflow GitHub Actions menjalankan pengujian dan build Docker pada push dan pull request. Terapkan batas request di reverse proxy, pemantauan memori/CPU, dan pembatasan trafik per IP sebelum membuka layanan untuk publik berskala besar. Kuota dalam aplikasi berlaku per instans dan di-reset saat proses dimulai ulang. Batas waktu dan memori worker bukan isolasi keamanan setara sandbox kernel; jalankan container tanpa hak istimewa dan tanpa mount rahasia. Upgrade dependensi secara berkala dan jalankan CI sebelum rilis.
