# MarkItDown Web

Aplikasi web mandiri untuk mengonversi dokumen ke Markdown menggunakan [microsoft/markitdown](https://github.com/microsoft/markitdown). Proyek ini **bukan** fork atau layanan resmi Microsoft.

## Fitur dan batasan

- Seret banyak berkas ke halaman: antrean diproses berurutan, dengan progres unggah, status OCR/konversi, pratinjau, dan unduhan `.md` per berkas. Klik area drop untuk pemilihan berkas di perangkat yang tidak mendukung drag and drop.
- Endpoint `POST /api/convert` tetap menerima satu berkas per permintaan; antarmuka mengirimnya satu per satu.
- Format: PNG, JPG/JPEG, WebP, PDF, DOCX, PPTX, XLSX, TXT, CSV, JSON. Maksimum 10 MB per berkas, 8 megapiksel per gambar, dan 2 MB hasil Markdown.
- Gambar statis diproses dengan Tesseract OCR lokal (bahasa Indonesia dan Inggris) secara default. Jika `HF_OLMOCR_ENDPOINT_URL` dan `HF_TOKEN` diatur, gambar dikirim ke Hugging Face Inference Endpoint khusus yang menjalankan `allenai/olmOCR-2-7B-1025-FP8`. Halaman menampilkan konfigurasi OCR dan mesin yang digunakan per hasil. Kunci DeepInfra/Cirrascale lama tidak digunakan.
- Akses publik tanpa kunci; berkas tidak ditulis ke penyimpanan permanen. Worker terpisah dibatasi waktu 30 detik, CPU 25 detik, dan ruang alamat 1 GiB (Linux).
- Tidak menerima URL, path server, HTML, ZIP, atau plugin dari pengguna. PDF berbasis gambar tanpa lapisan teks masih dapat menghasilkan teks kosong; OCR saat ini berlaku untuk berkas gambar, bukan halaman PDF hasil pindai atau gambar yang tertanam dalam dokumen.
- Maksimum satu proses konversi aktif dan 12 konversi per menit per instans. Permintaan selebihnya mendapat HTTP 429. Batas ini tidak menggantikan pembatasan trafik di tepi jaringan; untuk beban tinggi perlu antrian kerja dan pengaturan kapasitas terpisah.
- Jika olmOCR 2 terkonfigurasi, dibatasi lagi menjadi 30 permintaan gambar per jam per instans. Ini bukan batas biaya yang kuat karena hitungan di-reset saat proses dimulai ulang; tetapkan batas belanja di akun penyedia sebelum membuka layanan publik.

## Menjalankan lokal

Python 3.12:

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Instal Tesseract dengan paket bahasa `eng` dan `ind` sebelum menjalankan aplikasi (contoh Debian/Ubuntu: `sudo apt-get install tesseract-ocr tesseract-ocr-eng tesseract-ocr-ind`). Buka <http://127.0.0.1:8000>.

Docker:

```bash
docker build -t markitdown-web .
docker run --rm -p 8000:8000 --memory=2g --pids-limit=128 --cap-drop=ALL --security-opt=no-new-privileges markitdown-web
```

## API

Kirim isi berkas sebagai body biner mentah; `X-Filename` berisi nama berkas yang di-URL-encode. Contoh OCR gambar:

```bash
curl -f -X POST 'http://127.0.0.1:8000/api/convert' \
  -H 'X-Filename: screenshot.png' \
  -H 'Content-Type: application/octet-stream' \
  --data-binary '@screenshot.png' -o screenshot.md
```

`GET /health` mengembalikan `{"status":"ok"}`. Kesalahan mengembalikan JSON `{"detail":"..."}`. Endpoint konversi dapat dipakai siapa saja yang mengetahui URL. Gunakan HTTPS saat akses dari internet.

## Mengaktifkan olmOCR 2 (Hugging Face)

Model olmOCR 2 di Hugging Face Hub **belum tersedia di Inference Providers**. Token Hub atau URL model saja tidak menjalankan model. Anda perlu membuat [Inference Endpoint khusus](https://endpoints.huggingface.co/) berbayar dengan GPU:

1. Pilih model `allenai/olmOCR-2-7B-1025-FP8`, engine **vLLM**, endpoint **private**, dan GPU yang memenuhi kebutuhan model. Uji di Playground bahwa endpoint menerima gambar dan menjalankan model tersebut. Periksa biaya per jam dan pengaturan **Scale to Zero** di Hugging Face sebelum membuat endpoint.
2. Salin **base URL** endpoint dari Overview, misalnya `https://contoh.us-east-1.aws.endpoints.huggingface.cloud`. URL harus HTTPS dengan host `*.endpoints.huggingface.cloud`; aplikasi akan menambahkan `/v1/chat/completions`. Sufiks `/v1` pada URL juga diterima. Buat token Hugging Face yang dapat memanggil endpoint private itu.
3. Di Dashboard Render untuk web service ini, buka **Environment**, atur `HF_OLMOCR_ENDPOINT_URL` ke base URL dan `HF_TOKEN` ke token tersebut, lalu simpan agar layanan dideploy ulang. Anda dapat menghapus `DEEPINFRA_API_KEY` dan `CIRRASCALE_API_KEY` lama. Jangan menaruh token dalam GitHub, URL, atau kolom di halaman web.
4. Muat ulang situs; keterangan pada area drop akan berubah menjadi **olmOCR 2 terkonfigurasi**. Coba satu screenshot dan periksa status hasil **Selesai (olmOCR 2)**. Tanpa kedua variabel, aplikasi memakai OCR lokal.

Ketika olmOCR 2 terkonfigurasi, gambar pengguna diteruskan ke endpoint Hugging Face; periksa ketentuan pemrosesan data sebelum mengunggah dokumen sensitif. Layanan Render Free tidak menjalankan model 7B sendiri. Endpoint yang sedang menyala dari Scale to Zero dapat sementara mengembalikan HTTP 502; aplikasi memberi pesan untuk mencoba lagi. Respons penyedia yang gagal ditampilkan sebagai kesalahan dan tidak diam-diam diganti OCR lokal. Model mengembalikan teks yang dibaca dari gambar, bukan terjemahan ke bahasa lain.

## GitHub dan deployment Render

1. Sumber kode tersedia di [abdazzamajhari/markitdown-web](https://github.com/abdazzamajhari/markitdown-web).
2. Di Render, pilih **New → Blueprint**, sambungkan repositori GitHub tersebut, dan gunakan `render.yaml`. Tidak diperlukan variabel lingkungan untuk kunci.
3. Setelah build selesai, buka alamat `https://...onrender.com`, seret beberapa berkas ke area drop, lalu unduh setiap hasilnya. Uji `https://...onrender.com/health`.

Blueprint memakai paket `free` untuk percobaan awal. Kapasitas 512 MB dan batas layanan gratis dapat menggagalkan konversi dokumen besar; pilih paket dengan memori memadai setelah mengukur kebutuhan dan memeriksa biaya di dashboard. `checksPass` menunggu CI GitHub berhasil sebelum auto-deploy. Jika layanan lama masih menyimpan `WEB_API_KEY` di Dashboard Render, Anda boleh menghapusnya; aplikasi mengabaikannya.

GitHub Pages hanya meng-host berkas statis dan tidak dapat menjalankan backend Python. Layanan web ini harus ditempatkan di runtime Python atau Docker. GitHub menyimpan sumber kode; Render menjalankan situs dari repositori itu.

## Pengujian dan operasi

```bash
pip install -r requirements-dev.txt
pytest -q
```

Workflow GitHub Actions memasang Tesseract, menjalankan pengujian OCR dan dokumen, lalu membangun Docker pada push dan pull request. Bilah progres menampilkan persentase pengiriman berkas; saat konversi berlangsung bilah bergerak tanpa persentase karena backend OCR tidak melaporkan kemajuan parsial. Terapkan batas request di reverse proxy, pemantauan memori/CPU, dan pembatasan trafik per IP sebelum membuka layanan untuk publik berskala besar. Kuota dalam aplikasi berlaku per instans dan di-reset saat proses dimulai ulang. Batas waktu dan memori worker bukan isolasi keamanan setara sandbox kernel; jalankan container tanpa hak istimewa dan tanpa mount rahasia. Upgrade dependensi secara berkala dan jalankan CI sebelum rilis.
