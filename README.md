# PrivasiDoc

A document workspace for personal-data-aware processing: Markdown extraction, image compression and pixel resizing, PDF compression, and visual PDF signatures. The existing GitHub repository and Render address remain the deployment target.

## Document tools

- **Image compression and resize:** batch PNG/JPEG/WebP uploads; width/height in pixels, aspect-ratio control, quality 20–95 for JPEG/WebP, PNG lossless output, before/after preview, individual downloads, and metadata removal enabled by default. JPEG transparency is flattened onto white. Keeping metadata retains EXIF and ICC where supported, after EXIF orientation is normalized.
- **PDF compression:** batch uploads; lossless stream/object optimization or balanced/small image recompression with quality and DPI controls. Selectable text, page dimensions, links and form values are retained. Alpha masks and unusual image color spaces are skipped. Compression is not guaranteed to reduce file size. Document Info/XMP metadata is removed by default; JavaScript actions are scrubbed from exported PDFs.
- **PDF signature editor:** upload or draw one signature image, add up to 30 placements across pages, drag, resize, adjust X/Y, remove placements, preview and download the final PDF. Coordinates are normalized to the displayed cropped/rotated page; the original crop and rotation are preserved. This is a **visual signature**, not a certificate-backed cryptographic signature, identity verification or certified signing service. PDFs with signature fields or signature dictionaries are refused for editing/compression to avoid invalidating existing signatures.
- **Privacy controls:** external AI is off by default; image OCR uses local Tesseract unless explicitly enabled. PDF language review requires an explicit `X-External-AI: true` request. Clearing the browser session reloads the page and releases its retained file/results. Removing metadata does not redact personal information in visible text, images, annotations or form values.

All operations use the same bounded, short-lived worker, with no inherited provider credentials and worker network calls disabled. Input files are limited to 10 MB; PDF tools to 30 pages; image compression to 24 megapixels; the signature image to 1 MB/2 megapixels. Results are capped at 15 MB. The application does not create a permanent archive of uploads. HTTP API responses use `no-store`; browser assets are served from the same origin with no analytics or third-party frontend resources. HTTPS terminates at Render; files are uploaded to the application server, not processed entirely on the user's device.

The source is available at https://github.com/abdazzamajhari/markitdown-web. The application's source retains its MIT license; the PyMuPDF dependency is separately licensed under AGPL/commercial terms (https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright). Retain dependency notices and observe their terms when redistributing or deploying modified versions.

## PDF pipeline

1. Validate the PDF and enforce the 30-page limit.
2. Render **each entire page** to a JPEG with Poppler (`pdftoppm`, longest side 2800 px). Embedded images, text, stamps, and tables are flattened together in that page image. The **OCR diperbesar** button rerenders the selected page at 3200 px and replaces its OCR text when successful.
3. Run Tesseract (`ind+eng`) once on the rendered image for both text and word coordinates. The original PDF bytes are never sent to an OCR model. The selectable PDF text layer does not form the Markdown output.
4. Display the page image and OCR text. The browser processes all pages in order and assembles the Markdown download only when every page has been processed. Blank/unreadable pages are reported for review.
5. When the user enables external AI, send **only its OCR text** to SumoPod `deepseek-v4.1-flash:netra` for a separate linguistic plausibility review. Show detected languages and up to three literal excerpts that may need inspection. This review cannot compare against the page image and never changes the OCR transcription or blocks Markdown download. A provider failure is shown separately. With external AI off, this step is skipped.

The SumoPod vision probe remains outside the PDF path; the PDF review uses a text-only request. SumoPod is used for separately uploaded PNG/JPEG/WebP images only with external AI enabled. Tesseract can misread small print, handwriting, stamps, and tables; inspect the Markdown against page images before relying on sensitive details. PDF images are not sent to SumoPod, but OCR text is transmitted during an enabled language review.

## Run

```bash
pip install -r requirements.txt
# Install poppler-utils, tesseract-ocr, tesseract-ocr-eng, tesseract-ocr-ind
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The Dockerfile installs the OCR dependencies. `SUMOPOD_API_KEY` is required for PDF language review and separate image uploads; PDF OCR remains available when the key or language review fails. Do not commit keys. The server processes uploads in memory and does not persist them. Other supported inputs are DOCX, PPTX, XLSX, TXT, CSV, and JSON.

## API

`POST /api/convert` validates a PDF and returns an empty initial Markdown document. The browser calls `POST /api/pdf-preview?page=N` for each page to receive the rendered JPEG, OCR `page_text`, word `regions`, and page count. For PDF, `page_source` is `tesseract` when text is recognized. `POST /api/pdf-language-check` accepts OCR text and returns a separate text-only Netra review with consent. `GET /health` reports server availability.

New raw-binary endpoints use `X-Filename` (URL-encoded) and `X-Options` (base64url JSON):

| Endpoint | Options |
| --- | --- |
| `/api/image-compress` | `width`, `height` (0=original/automatic), `keep_ratio`, `quality`, `format` (`jpeg`, `png`, `webp`), `remove_metadata` |
| `/api/pdf-compress` | `profile` (`lossless`, `balanced`, `small`), `quality`, `dpi`, `remove_metadata` |
| `/api/pdf-editor-preview` | `page` (1-based); returns JPEG as base64, page dimensions/count; no OCR or AI |
| `/api/pdf-sign` | body=PDF bytes followed by signature bytes; `pdf_size`, `placements` (`page`, `x`, `y`, `w`, `h`, coordinates 0–1), `remove_white`, `remove_metadata` |

Downloads return `Content-Disposition` and `X-Document-Info` JSON with dimensions/page count and applied operations. Invalid settings return 400; signed PDFs 409; exceeded limits 413; invalid/encrypted PDFs 422. Busy workers return 429. No uploaded filename, body or signature bytes are logged by the application.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Tests check real binary outputs, image orientation/transparency/metadata, PDF text/forms, compression, rotated/cropped signature geometry, multiple placements, limits, encrypted/signed PDF refusal, and external-AI consent. GitHub CI runs the suite and Docker build; Render's existing service deploys from `main` after checks pass.
