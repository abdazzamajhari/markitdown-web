# MarkItDown Web

Aplikasi web mandiri untuk mengonversi dokumen ke Markdown menggunakan [microsoft/markitdown](https://github.com/microsoft/markitdown). Proyek ini **bukan** fork atau layanan resmi Microsoft.

## Fitur dan batasan

- Seret banyak berkas ke area unggah **atau bagian lain halaman**: antrean diproses berurutan, dengan progres unggah dan status OCR/konversi. Klik area unggah untuk memilih berkas di perangkat tanpa drag and drop.
- Halaman awal menampilkan **ilustrasi contoh** kotak OCR yang dapat diklik. Setelah gambar berhasil dikonversi, detail gambar sumber dan transkripsi **terbuka otomatis**; kotak OCR dimuat sebelum berkas berikutnya diproses. Tombol **Perbesar gambar** membantu memeriksa tulisan kecil. Kotak dan teks per area dihitung menggunakan **Tesseract lokal** melalui `POST /api/regions`; transkripsi utama tetap dihasilkan oleh mesin yang disebut pada hasil (SumoPod atau Tesseract). Keduanya **tidak disejajarkan secara otomatis** dan kotak dapat tidak lengkap meskipun transkripsi SumoPod membaca teks lain. Jika OCR lokal tidak menemukan area, halaman menyebutkannya secara eksplisit, tanpa membuat kotak palsu pada hasil unggahan.
- Unduh `.md` per berkas, **semua `.md` dalam satu ZIP**, atau **satu `.md` gabungan**. ZIP dibuat di browser dari hasil yang sudah diterima; berkas gagal tidak dimasukkan. Hasil kosong yang berhasil diproses menghasilkan `.md` kosong. Periksa hasil sebelum digunakan sebagai data penelitian atau dokumen resmi.
- Endpoint `POST /api/convert` tetap menerima satu berkas per permintaan; antarmuka mengirimnya satu per satu.
- `POST /api/regions` menerima berkas gambar biner dengan header `X-Filename` yang sama dan mengembalikan `{"engine":"tesseract","regions":[{"x":0.1,"y":0.2,"w":0.3,"h":0.04,"text":"..."}]}`. Koordinat dinormalisasi terhadap gambar berorientasi benar yang diproses worker. Endpoint dipanggil otomatis setelah gambar berhasil dikonversi, memakai kuota terpisah 12 pratinjau per menit per instans, dan tidak mengirim gambar ke SumoPod. Detail dapat ditutup dan dibuka ulang untuk mencoba kembali bila pemetaan awal gagal.
- Format: PNG, JPG/JPEG, WebP, PDF, DOCX, PPTX, XLSX, TXT, CSV, JSON. Maksimum 10 MB per berkas, 8 megapiksel per gambar, dan 2 MB hasil Markdown.
- Gambar statis diproses dengan Tesseract OCR lokal (bahasa Indonesia dan Inggris) secara default. Jika `SUMOPOD_API_KEY` diatur, gambar dikirim ke SumoPod untuk ditranskripsikan dengan model vision `gpt-4o-mini`. Halaman menampilkan konfigurasi OCR dan mesin yang digunakan per hasil. Konfigurasi Hugging Face, DeepInfra, dan Cirrascale lama tidak digunakan.
- Akses publik tanpa kunci pengguna; berkas tidak ditulis ke penyimpanan permanen. Worker terpisah dibatasi waktu 30 detik, CPU 25 detik, dan ruang alamat 1 GiB (Linux). Hasil unduhan massal dirakit sementara di memori browser; muat ulang halaman akan menghapus daftar hasilnya.
- Tidak menerima URL, path server, HTML, ZIP, atau plugin dari pengguna. PDF berbasis gambar tanpa lapisan teks masih dapat menghasilkan teks kosong; OCR saat ini berlaku untuk berkas gambar, bukan halaman PDF hasil pindai atau gambar yang tertanam dalam dokumen.
- Maksimum satu proses konversi aktif dan 12 konversi per menit per instans. Permintaan selebihnya mendapat HTTP 429. Batas ini tidak menggantikan pembatasan trafik di tepi jaringan; untuk beban tinggi perlu antrian kerja dan pengaturan kapasitas terpisah.
- Jika SumoPod terkonfigurasi, dibatasi lagi menjadi 30 permintaan gambar per jam per instans. Ini bukan batas biaya yang kuat karena hitungan di-reset saat proses dimulai ulang; tetapkan batas belanja pada kunci API SumoPod sebelum membuka layanan publik.

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

Contoh mengambil lokasi OCR lokal untuk gambar (koordinat dan teks per baris, **bukan** bounding box dari SumoPod):

```bash
curl -f -X POST 'http://127.0.0.1:8000/api/regions' \
  -H 'X-Filename: screenshot.png' \
  -H 'Content-Type: application/octet-stream' \
  --data-binary '@screenshot.png'
```

## Mengaktifkan OCR AI (SumoPod)

Layanan ini menggunakan endpoint OpenAI-compatible `https://ai.sumopod.com/v1/chat/completions` dan model `gpt-4o-mini` untuk gambar. Ini adalah OCR berbasis model vision, **bukan olmOCR 2**.

1. Di SumoPod, buat API key dan tetapkan batas anggaran untuk kunci tersebut.
2. Di Dashboard Render untuk web service ini, buka **Environment**, atur `SUMOPOD_API_KEY` ke nilai kunci, lalu simpan agar layanan dideploy ulang. Anda dapat menghapus `HF_OLMOCR_ENDPOINT_URL`, `HF_TOKEN`, `DEEPINFRA_API_KEY`, dan `CIRRASCALE_API_KEY` lama. Jangan menaruh kunci dalam GitHub, URL, atau kolom di halaman web.
3. Muat ulang situs; keterangan pada area unggah akan menampilkan **OCR gambar: SumoPod**. Coba satu screenshot dan periksa status hasil **Selesai (SumoPod · gpt-4o-mini)**. Buka detail untuk melihat transkripsi dan kotak OCR lokal. Tanpa variabel itu, aplikasi memakai OCR lokal.

Ketika SumoPod terkonfigurasi, gambar pengguna diteruskan ke SumoPod; periksa ketentuan pemrosesan data sebelum mengunggah dokumen sensitif. Respons penyedia yang gagal ditampilkan sebagai kesalahan dan tidak diam-diam diganti OCR lokal. Instruksi model meminta transkripsi teks asli dari gambar, bukan terjemahan ke bahasa lain. Biaya model bergantung pada pemakaian token gambar dan teks.

## GitHub dan deployment Render

1. Sumber kode tersedia di [abdazzamajhari/markitdown-web](https://github.com/abdazzamajhari/markitdown-web).
2. Di Render, pilih **New → Blueprint**, sambungkan repositori GitHub tersebut, dan gunakan `render.yaml`. `SUMOPOD_API_KEY` bersifat opsional untuk OCR gambar AI; tanpa variabel itu digunakan OCR lokal.
3. Setelah build selesai, buka alamat `https://...onrender.com`, seret beberapa berkas, lihat detail hasil, lalu uji unduhan per berkas, ZIP seluruh `.md`, dan `.md` gabungan. Uji `https://...onrender.com/health`.

Blueprint memakai paket `free` untuk percobaan awal. Kapasitas 512 MB dan batas layanan gratis dapat menggagalkan konversi dokumen besar; pilih paket dengan memori memadai setelah mengukur kebutuhan dan memeriksa biaya di dashboard. `checksPass` menunggu CI GitHub berhasil sebelum auto-deploy. Jika layanan lama masih menyimpan `WEB_API_KEY` di Dashboard Render, Anda boleh menghapusnya; aplikasi mengabaikannya.

GitHub Pages hanya meng-host berkas statis dan tidak dapat menjalankan backend Python. Layanan web ini harus ditempatkan di runtime Python atau Docker. GitHub menyimpan sumber kode; Render menjalankan situs dari repositori itu.

## Pengujian dan operasi

```bash
pip install -r requirements-dev.txt
pytest -q
```

Workflow GitHub Actions memasang Tesseract, menjalankan pengujian OCR dan dokumen, lalu membangun Docker pada push dan pull request. Bilah progres menampilkan persentase pengiriman berkas; saat konversi berlangsung bilah bergerak tanpa persentase karena backend OCR tidak melaporkan kemajuan parsial. Terapkan batas request di reverse proxy, pemantauan memori/CPU, dan pembatasan trafik per IP sebelum membuka layanan untuk publik berskala besar. Kuota dalam aplikasi berlaku per instans dan di-reset saat proses dimulai ulang. Batas waktu dan memori worker bukan isolasi keamanan setara sandbox kernel; jalankan container tanpa hak istimewa dan tanpa mount rahasia. Upgrade dependensi secara berkala dan jalankan CI sebelum rilis.
