# MarkItDown Web

Aplikasi web mandiri untuk mengubah dokumen menjadi Markdown. Teks yang dapat dipilih diekstraksi dengan [microsoft/markitdown](https://github.com/microsoft/markitdown); OCR gambar dan seluruh halaman PDF memakai **`deepseek-v4-flash-vision-exp`** melalui API DeepSeek resmi atau SumoPod. Proyek ini bukan layanan resmi Microsoft, DeepSeek, atau SumoPod.

## Fitur

- Unggah banyak berkas sekaligus atau seret ke halaman. Jumlah PDF dalam antrean tidak dibatasi; berkas diproses berurutan. Setiap berkas maksimal 10 MB.
- **Setiap PDF maksimal 30 halaman.** PDF dengan 31 halaman atau lebih langsung ditandai **Dilewati** dan antrean melanjutkan berkas berikutnya. PDF hasil pindai dan PDF campuran dengan gambar tertanam sama-sama diproses per halaman oleh DeepSeek. Progres `OCR halaman N/M` tampil sampai selesai.
- Panel detail PDF terbuka otomatis. Teks DeepSeek per halaman muncul di awal panel kanan dan ikut masuk ke `.md` per berkas, ZIP seluruh hasil, serta `.md` gabungan. Teks lapisan PDF asli tetap dipertahankan setelah transkripsi halaman sebagai referensi.
- Kotak merah pada teks yang dapat dipilih memakai koordinat lapisan PDF. Untuk gambar, aplikasi meminta DeepSeek mengembalikan koordinat teks. Koordinat model dapat tidak lengkap; bila tidak tersedia, halaman menyebutkannya tanpa membuat kotak palsu. Kotak dapat diklik untuk membaca teks per area.
- Tombol Sebelumnya/Berikutnya menelusuri halaman PDF, OCR seluruh halaman mengulang pemrosesan, dan Coba lagi mengulang halaman yang gagal. Permintaan yang terkena pembatasan sementara diulang otomatis. Hasil sebagian tetap dapat diunduh.
- Format: PNG, JPG/JPEG, WebP, PDF, DOCX, PPTX, XLSX, TXT, CSV, JSON. Gambar tertanam dalam DOCX/PPTX belum di-OCR.

## Menjalankan lokal

Python 3.12 dan Poppler diperlukan. Di Debian/Ubuntu, instal `poppler-utils`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export SUMOPOD_API_KEY='kunci-penyedia-vision-anda'
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Atau jalankan Dockerfile. Konfigurasi yang tersedia ialah `SUMOPOD_API_KEY` (`https://ai.sumopod.com/v1/chat/completions`) dan `DEEPSEEK_API_KEY` (`https://api.deepseek.com/chat/completions`); jika keduanya ada, kode memilih API resmi DeepSeek. Keduanya mengirim ID **`deepseek-v4-flash-vision-exp`** dan mematikan thinking. **Dukungan vision ID tersebut belum terverifikasi pada kedua jalur.** Jangan taruh kunci di repositori atau antarmuka web. Sebelum OCR pertama, aplikasi meminta penyedia membaca kode acak dari gambar; jawaban yang gagal akan menghentikan OCR. Pastikan penyedia dan akun yang dipakai secara eksplisit mendukung input gambar untuk ID itu sebelum memakai berkas penting.

Dokumentasi [model API resmi DeepSeek](https://api-docs.deepseek.com/api/list-models/) mencantumkan V4 Flash dan V4 Pro, sedangkan [panduan vision resminya](https://api-docs.deepseek.com/quick_start/agent_integrations/github_copilot/) menyebut V4 sebagai model teks dan memakai model lain untuk membaca gambar. ID `deepseek-v4-flash-vision-exp` tidak terdokumentasi sebagai model vision API resmi. Menambah saldo API resmi menyelesaikan HTTP 402, tetapi belum menjamin OCR dapat bekerja. SumoPod juga harus membuktikan dukungan gambar melalui uji kode yang ada di aplikasi.

Halaman PDF dan gambar diunggah ke penyedia yang dikonfigurasi saat OCR dijalankan. Periksa kebijakan data dan biaya penyedia sebelum memakai dokumen sensitif. Aplikasi tidak menyimpan berkas secara permanen; daftar hasil di browser hilang setelah halaman dimuat ulang. Banyak PDF berarti banyak panggilan model, satu untuk setiap halaman dan satu tambahan untuk pemetaan kotak gambar biasa.

## API

Body unggahan berisi berkas biner mentah; header `X-Filename` berisi nama berkas yang di-URL-encode.

```bash
curl -f -X POST 'http://127.0.0.1:8000/api/convert' \
  -H 'X-Filename: dokumen.pdf' \
  -H 'Content-Type: application/octet-stream' \
  --data-binary '@dokumen.pdf' -o hasil-awal.md
```

Untuk PDF, `/api/convert` mengembalikan teks lapisan dokumen yang tersedia. Antarmuka kemudian memanggil `POST /api/pdf-preview?page=N` pada setiap halaman, memakai berkas PDF yang sama, untuk memperoleh gambar, transkripsi DeepSeek (`page_text`, `page_source: deepseek`), dan `regions`. Antarmuka merakit Markdown akhir setelah semua halaman selesai. `POST /api/regions` memetakan kotak pada gambar biasa melalui DeepSeek. `GET /api/capabilities` menampilkan model OCR yang aktif; `GET /health` menampilkan status layanan.

Jika OCR penyedia gagal, `/api/pdf-preview` tetap mengirim gambar halaman dan kotak dari lapisan teks PDF, disertai `ocr_error` dan `ocr_status`. Antarmuka menandai berkas gagal serta menonaktifkan unduhan Markdown lengkap; halaman lain masih dapat dijelajahi sebagai pratinjau tanpa memanggil model lagi. `preview_only=true` meminta gambar halaman tanpa OCR. Teks lapisan PDF yang muncul di panel bukan transkripsi tulisan dalam gambar.

**HTTP 402:** akun penyedia yang aktif menolak permintaan karena pembayaran/kredit. Pada [API resmi DeepSeek](https://api-docs.deepseek.com/quick_start/error_codes/), 402 berarti saldo tidak mencukupi. Jika memakai SumoPod, periksa kredit/paket pada akun SumoPod. `DEEPSEEK_API_KEY` diprioritaskan apabila terpasang; melepasnya dari Render akan memilih `SUMOPOD_API_KEY` jika tersedia. **Jangan mengisi saldo semata-mata berdasarkan pesan 402:** konfirmasi dahulu bahwa penyedia mendukung input gambar untuk ID model ini. Setelah akses vision benar-benar bekerja, unggah ulang berkas untuk OCR seluruh halaman.

Layanan menerima maksimal 12 permintaan konversi dan 12 permintaan pratinjau per menit per instans. Ini pembatas laju, **bukan batas jumlah PDF**: antarmuka menunggu `Retry-After` dan melanjutkan antrean. Hanya satu pemrosesan aktif per instans. PDF dengan lebih dari 30 halaman ditolak HTTP 413 dengan keterangan Dilewati.

## GitHub dan Render

Sumber kode: [abdazzamajhari/markitdown-web](https://github.com/abdazzamajhari/markitdown-web). Hubungkan repositori ini sebagai Render Blueprint memakai `render.yaml`, lalu atur kredensial penyedia yang telah memverifikasi dukungan gambar di Environment; `SUMOPOD_API_KEY` dipakai bila `DEEPSEEK_API_KEY` tidak terpasang. Build Docker memasang Poppler untuk render PDF. Render memakai instance `free` untuk percobaan; memori 512 MB dan batas waktu penyedia bisa memengaruhi dokumen besar. Periksa kebijakan biaya penyedia untuk pemakaian banyak halaman.

```bash
pip install -r requirements-dev.txt
pytest -q
```

Workflow GitHub Actions menjalankan tes dan build Docker pada push dan pull request. Batas request dan worker adalah pembatas sumber daya aplikasi, bukan pengganti pengamanan jaringan untuk layanan publik berskala besar.
