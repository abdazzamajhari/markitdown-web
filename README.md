# MarkItDown Web

Aplikasi web mandiri untuk mengubah dokumen menjadi Markdown. Teks yang dapat dipilih diekstraksi dengan [microsoft/markitdown](https://github.com/microsoft/markitdown); OCR gambar dan seluruh halaman PDF memakai **`qwen3.8-max` melalui SumoPod**. Proyek ini bukan layanan resmi Microsoft, Qwen, atau SumoPod.

## Fitur

- Unggah banyak berkas sekaligus atau seret ke halaman. Jumlah PDF dalam antrean tidak dibatasi; berkas diproses berurutan. Setiap berkas maksimal 10 MB.
- **Setiap PDF maksimal 30 halaman.** PDF dengan 31 halaman atau lebih langsung ditandai **Dilewati** dan antrean melanjutkan berkas berikutnya. PDF hasil pindai dan PDF campuran dengan gambar tertanam sama-sama diproses per halaman oleh Qwen. Progres `OCR halaman N/M` tampil sampai selesai.
- Panel detail PDF terbuka otomatis. Teks Qwen per halaman muncul di awal panel kanan dan ikut masuk ke `.md` per berkas, ZIP seluruh hasil, serta `.md` gabungan. Teks lapisan PDF asli tetap dipertahankan setelah transkripsi halaman sebagai referensi.
- Kotak merah pada teks yang dapat dipilih memakai koordinat lapisan PDF. Untuk gambar, aplikasi meminta Qwen mengembalikan koordinat teks. Koordinat model dapat tidak lengkap; bila tidak tersedia, halaman menyebutkannya tanpa membuat kotak palsu. Kotak dapat diklik untuk membaca teks per area.
- Tombol Sebelumnya/Berikutnya menelusuri halaman PDF, OCR seluruh halaman mengulang pemrosesan, dan Coba lagi mengulang halaman yang gagal. Permintaan yang terkena pembatasan sementara diulang otomatis. Hasil sebagian tetap dapat diunduh.
- Format: PNG, JPG/JPEG, WebP, PDF, DOCX, PPTX, XLSX, TXT, CSV, JSON. Gambar tertanam dalam DOCX/PPTX belum di-OCR.

## Menjalankan lokal

Python 3.12 dan Poppler diperlukan. Di Debian/Ubuntu, instal `poppler-utils`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export SUMOPOD_API_KEY='kunci-anda'
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Atau jalankan Dockerfile. `SUMOPOD_API_KEY` wajib untuk OCR gambar dan PDF; simpan sebagai environment variable di Render, jangan taruh di repositori atau antarmuka web. Endpoint OpenAI-compatible yang digunakan adalah `https://ai.sumopod.com/v1/chat/completions` dengan model **`qwen3.8-max` saja**. Kegagalan penyedia ditampilkan sebagai kesalahan; aplikasi tidak menggantinya dengan model lain.

Halaman PDF dan gambar diunggah ke SumoPod saat OCR dijalankan. Periksa kebijakan data dan biaya penyedia sebelum memakai dokumen sensitif. Aplikasi tidak menyimpan berkas secara permanen; daftar hasil di browser hilang setelah halaman dimuat ulang. Banyak PDF berarti banyak panggilan model, satu untuk setiap halaman dan satu tambahan untuk pemetaan kotak gambar biasa.

## API

Body unggahan berisi berkas biner mentah; header `X-Filename` berisi nama berkas yang di-URL-encode.

```bash
curl -f -X POST 'http://127.0.0.1:8000/api/convert' \
  -H 'X-Filename: dokumen.pdf' \
  -H 'Content-Type: application/octet-stream' \
  --data-binary '@dokumen.pdf' -o hasil-awal.md
```

Untuk PDF, `/api/convert` mengembalikan teks lapisan dokumen yang tersedia. Antarmuka kemudian memanggil `POST /api/pdf-preview?page=N` pada setiap halaman, memakai berkas PDF yang sama, untuk memperoleh gambar, transkripsi Qwen (`page_text`, `page_source: qwen`), dan `regions`. Antarmuka merakit Markdown akhir setelah semua halaman selesai. `POST /api/regions` memetakan kotak pada gambar biasa melalui Qwen. `GET /api/capabilities` menampilkan model OCR yang aktif; `GET /health` menampilkan status layanan.

Layanan menerima maksimal 12 permintaan konversi dan 12 permintaan pratinjau per menit per instans. Ini pembatas laju, **bukan batas jumlah PDF**: antarmuka menunggu `Retry-After` dan melanjutkan antrean. Hanya satu pemrosesan aktif per instans. PDF dengan lebih dari 30 halaman ditolak HTTP 413 dengan keterangan Dilewati.

## GitHub dan Render

Sumber kode: [abdazzamajhari/markitdown-web](https://github.com/abdazzamajhari/markitdown-web). Hubungkan repositori ini sebagai Render Blueprint memakai `render.yaml`, lalu atur `SUMOPOD_API_KEY` di Environment. Build Docker memasang Poppler untuk render PDF. Render memakai instance `free` untuk percobaan; memori 512 MB dan batas waktu penyedia bisa memengaruhi dokumen besar. Periksa kebijakan biaya SumoPod untuk pemakaian banyak halaman.

```bash
pip install -r requirements-dev.txt
pytest -q
```

Workflow GitHub Actions menjalankan tes dan build Docker pada push dan pull request. Batas request dan worker adalah pembatas sumber daya aplikasi, bukan pengganti pengamanan jaringan untuk layanan publik berskala besar.
