# MarkItDown Web

Aplikasi web mandiri untuk mengubah dokumen menjadi Markdown. Teks yang dapat dipilih diekstraksi dengan [microsoft/markitdown](https://github.com/microsoft/markitdown); OCR gambar dan seluruh halaman PDF dikirim ke **`deepseek-v4.1-flash:netra`** melalui API SumoPod. Proyek ini bukan layanan resmi Microsoft, DeepSeek, atau SumoPod.

## Fitur

- Unggah banyak berkas sekaligus atau seret ke halaman. Jumlah PDF dalam antrean tidak dibatasi; berkas diproses berurutan. Setiap berkas maksimal 10 MB.
- **Setiap PDF maksimal 30 halaman.** PDF dengan 31 halaman atau lebih langsung ditandai **Dilewati** dan antrean melanjutkan berkas berikutnya. PDF hasil pindai dan PDF campuran dengan gambar tertanam sama-sama diproses per halaman oleh DeepSeek. Progres `OCR halaman N/M` tampil sampai selesai.
- Panel detail PDF terbuka otomatis. Teks DeepSeek per halaman muncul di awal panel kanan dan ikut masuk ke `.md` per berkas, ZIP seluruh hasil, serta `.md` gabungan. Teks lapisan PDF asli tetap dipertahankan setelah transkripsi halaman sebagai referensi.
- Kotak merah pada teks yang dapat dipilih memakai koordinat lapisan PDF. Setelah transkripsi teks, aplikasi meminta DeepSeek memetakan koordinat teks pada gambar dengan panggilan terpisah; bila pemetaan gagal, teks hasil OCR tetap masuk ke Markdown. Koordinat model dapat tidak lengkap; bila tidak tersedia, halaman menyebutkannya tanpa membuat kotak palsu. Kotak dapat diklik untuk membaca teks per area.
- Tombol Sebelumnya/Berikutnya menelusuri halaman PDF, OCR seluruh halaman mengulang pemrosesan, dan Coba lagi mengulang halaman yang gagal. Permintaan yang terkena pembatasan sementara diulang otomatis. Unduhan Markdown lengkap dinonaktifkan jika OCR halaman gagal.
- Format: PNG, JPG/JPEG, WebP, PDF, DOCX, PPTX, XLSX, TXT, CSV, JSON. Gambar tertanam dalam DOCX/PPTX belum di-OCR.

## Menjalankan lokal

Python 3.12 dan Poppler diperlukan. Di Debian/Ubuntu, instal `poppler-utils`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export SUMOPOD_API_KEY='kunci-sumopod-anda'
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Atau jalankan Dockerfile. Untuk OCR gambar dan PDF, atur **`SUMOPOD_API_KEY`** sebagai environment variable di Render. Aplikasi mencoba rute SumoPod `https://ai.sumopod.com/v1/chat/completions` dan `https://ai.sumopod.com/v1/responses` dengan model **`deepseek-v4.1-flash:netra`**; `DEEPSEEK_API_KEY` diabaikan. Permintaan gambar memakai blok teks dan gambar PNG/JPEG base64 sesuai format masing-masing rute, dengan parameter untuk menonaktifkan mode berpikir; pada rute SumoPod parameter ini belum tentu diterapkan oleh model di belakang gateway. Jangan taruh kunci di repositori atau antarmuka web. Sebelum OCR pertama, aplikasi meminta model membaca kata uji acak dari gambar PNG di kedua rute; OCR hanya memakai rute yang lulus dan menampilkan kesalahan bila keduanya gagal membaca gambar. Respons OCR kosong pada halaman PDF juga tidak ditandai berhasil. Hasil dari penyedia tetap perlu diperiksa terhadap halaman asli.

Menurut [catatan perubahan resmi DeepSeek](https://api-docs.deepseek.com/updates/), ID lama `deepseek-v4-flash-vision-exp` telah pensiun dan hanya menjadi alias sementara ke V4.1 Flash pada API resmi DeepSeek. Model SumoPod yang kini dikirim aplikasi adalah `deepseek-v4.1-flash:netra`, sesuai ID lengkap yang tampil di daftar model SumoPod. ID ini adalah ID rute SumoPod yang perlu diverifikasi melalui gambar uji; identitas respons model saja tidak membuktikan dukungan input gambar. Dukungan vision pada rute akun SumoPod harus dibuktikan dengan gambar uji, karena status model pada API resmi DeepSeek tidak menjamin perutean identik di SumoPod.

Halaman PDF dan gambar diunggah ke penyedia yang dikonfigurasi saat OCR dijalankan. Periksa kebijakan data dan biaya penyedia sebelum memakai dokumen sensitif. Aplikasi tidak menyimpan berkas secara permanen; daftar hasil di browser hilang setelah halaman dimuat ulang. Banyak PDF berarti banyak panggilan model, sedikitnya satu untuk transkripsi tiap halaman dan satu tambahan untuk pemetaan kotak bila koordinat belum tersedia.

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

**HTTP 402:** SumoPod menolak permintaan karena pembayaran atau kredit diperlukan. Periksa status akun dan tagihan di SumoPod; aplikasi tidak dapat melihat detail saldo atau keputusan tagihan penyedia. Saldo akun API DeepSeek resmi tidak relevan untuk jalur ini. Setelah akses SumoPod pulih, unggah ulang berkas untuk OCR seluruh halaman.

**Rute SumoPod belum membaca gambar uji:** aplikasi mengirim gambar uji PNG base64 dengan kata acak sebelum OCR. Pada pengujian 29 September 2026, gambar bertuliskan `MERAH` dijawab `BIODATA` oleh `chat/completions` dan `PADAMU NEGERI` oleh `responses` dengan model `deepseek-flash` yang sebelumnya dipakai. Kedua rute mengembalikan respons, tetapi hasil pada model tersebut tidak sesuai piksel gambar. Aplikasi kemudian dikonfigurasi memakai ID `deepseek-v4-flash-vision-exp` dari daftar SumoPod. Pada uji langsung model tersebut, rute `chat` menjawab `Tidak ada gambar` untuk PNG base64 yang valid, sedangkan rute `responses` tidak mengembalikan kata uji. Pada uji awal dengan batas 256 token, SumoPod merutekan ID itu ke `deepseek-v4.1-flash` dan menghabiskan jatah untuk penalaran; setelah jatah uji dinaikkan menjadi 4096 token, gambar tetap tidak terbaca. Ini membuktikan OCR melalui kedua rute yang dipakai aplikasi belum berfungsi, tetapi tidak mengungkap penyebab internal gateway. Periksa pada SumoPod apakah model tersebut benar-benar menerima `image_url`/`input_image` melalui kunci API Anda dan minta contoh permintaan vision yang berhasil pada endpoint SumoPod. Status HTTP 200 atau daftar model saja tidak membuktikan dukungan gambar. Aplikasi sengaja tidak membuat Markdown dari jawaban yang gagal uji agar OCR lampiran tidak berisi teks rekaan. Atas permintaan pengguna, aplikasi kemudian mencoba `deepseek-v4.1-flash` dengan pemeriksaan gambar uji yang sama. Pada uji langsung setelah deploy 29 September 2026, gambar uji berisi `JENDELA`, tetapi `chat/completions` menjawab `BANGUN` dan `responses` menjawab `MAAF`. Karena keduanya tidak sesuai gambar, OCR gambar PDF masih belum berhasil; teks lapisan PDF yang tampil bukan hasil OCR gambar. Aplikasi menahan unduhan Markdown lengkap sampai rute gambar terbukti berfungsi. Uji `deepseek-v4.1-flash` tadi **tanpa akhiran `:netra`**, sehingga tidak menguji rute Netra yang diminta pengguna. Kini aplikasi memakai ID lengkap `deepseek-v4.1-flash:netra`. Pada uji langsung 29 September 2026, gambar uji bertuliskan `MERAH` dijawab `BERLATIH` oleh `chat/completions` dan `SMPN 1 BAURENO` oleh `responses`. Respons ini membuktikan bahwa kedua rute tersebut belum menghasilkan OCR yang sesuai gambar uji; penyebab internal SumoPod tidak terungkap dari respons. PDF dengan gambar tertanam tetap belum dapat ditranskripsikan hingga SumoPod menyediakan rute vision yang bekerja.

Layanan menerima maksimal 12 permintaan konversi dan 12 permintaan pratinjau per menit per instans. Ini pembatas laju, **bukan batas jumlah PDF**: antarmuka menunggu `Retry-After` dan melanjutkan antrean. Hanya satu pemrosesan aktif per instans. PDF dengan lebih dari 30 halaman ditolak HTTP 413 dengan keterangan Dilewati.

## GitHub dan Render

Sumber kode: [abdazzamajhari/markitdown-web](https://github.com/abdazzamajhari/markitdown-web). Hubungkan repositori ini sebagai Render Blueprint memakai `render.yaml`, lalu atur `SUMOPOD_API_KEY` di Environment untuk OCR melalui SumoPod. Build Docker memasang Poppler untuk render PDF. Render memakai instance `free` untuk percobaan; memori 512 MB dan batas waktu penyedia bisa memengaruhi dokumen besar. Periksa kebijakan biaya penyedia untuk pemakaian banyak halaman.

```bash
pip install -r requirements-dev.txt
pytest -q
```

Workflow GitHub Actions menjalankan tes dan build Docker pada push dan pull request. Batas request dan worker adalah pembatas sumber daya aplikasi, bukan pengganti pengamanan jaringan untuk layanan publik berskala besar.
