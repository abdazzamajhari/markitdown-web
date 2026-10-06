# PrivasiGuard

A document workspace for personal-data-aware processing: Markdown extraction, image compression and pixel resizing, PDF compression, and visual PDF signatures. The existing GitHub repository and Render address remain the deployment target.

## Categories and document tools

The interface groups features by file category. **Images** contains image OCR and compression/resize; **PDF** contains page OCR, PDF compression, PDF merging, and the visual signature editor; **Other documents** contains Markdown extraction from DOCX/PPTX/XLSX/TXT/CSV/JSON. OCR uploads, result lists, counters, combined Markdown and ZIP downloads are scoped to the selected category. Switching categories retains each category's results and last selected tool.

- **Image compression and resize:** batch PNG/JPEG/WebP/TIFF (`.tif` and `.tiff`) uploads; width/height in pixels, aspect-ratio control, quality 20–95 for JPEG/WebP, PNG and LZW TIFF output, before/after preview, individual downloads, and metadata removal enabled by default. TIFF is supported for OCR and uploaded PDF signature images as well. TIFF previews are converted to metadata-free PNG on the application server because browser decoding varies. Only single-page static images are accepted; multipage TIFF is refused rather than silently truncated. Output uses 8-bit RGB/RGBA; PNG/TIFF encoding is lossless after color-mode conversion, but this does not preserve 16-bit samples or CMYK channels. JPEG transparency is flattened onto white. Keeping metadata retains EXIF and ICC where supported, after EXIF orientation is normalized; obsolete TIFF storage tags are regenerated for the new image.
- **PDF compression:** batch uploads; lossless stream/object optimization or balanced/small image recompression with quality and DPI controls. Selectable text, page dimensions, links and form values are retained. Alpha masks and unusual image color spaces are skipped. Compression is not guaranteed to reduce file size. Document Info/XMP metadata is removed by default; JavaScript actions are scrubbed from exported PDFs.
- **PDF merging:** add 2–20 PDFs, move each file up/down or remove it, and download one PDF containing all pages in the chosen order. Input is limited to 10 MB per file, 20 MB combined and 100 pages in the result; output remains capped at 15 MB. Pages retain their text, images, crop/rotation, ordinary links, annotations and form values; form names are disambiguated so equal names in different documents do not become a shared field. Bookmarks are copied with page offsets. Document-level attachments, optional-content configuration, PDF portfolios and named destinations are not reconstructed. Cryptographic signatures/signature fields and encrypted PDFs are refused. Metadata removal is on by default; when off, Document Info/XMP from the first PDF is used. No external AI is called. Existing OCR/compression/signature tools retain their 30-page limit.
- **PDF signature editor:** upload or draw one signature image, add up to 30 placements across pages, drag, resize, adjust X/Y, remove placements, preview and download the final PDF. Coordinates are normalized to the displayed cropped/rotated page; the original crop and rotation are preserved. This is a **visual signature**, not a certificate-backed cryptographic signature, identity verification or certified signing service. PDFs with signature fields or signature dictionaries are refused for editing/compression to avoid invalidating existing signatures.
- **Privacy controls:** external AI is off by default; image OCR uses local Tesseract unless explicitly enabled. PDF language review requires an explicit `X-External-AI: true` request. Clearing the browser session reloads the page and releases its retained files/results. This runs automatically after the tab is continuously hidden (switching tabs or minimizing the browser) for 60 seconds; returning sooner cancels the deadline. Elapsed time is checked again on return and on a back/forward-cache restore because browsers may suspend background timers. Visible tabs are not cleared for inactivity. Empty sessions do not repeatedly reload in the background. No file or result is stored in web storage. Downloaded files are unaffected. Removing metadata does not redact personal information in visible text, images, annotations or form values.

All operations use the same bounded, short-lived worker, with no inherited provider credentials and worker network calls disabled. Input files are limited to 10 MB; merging accepts up to 20 MB combined and 100 pages, while other PDF tools accept 30 pages. Image compression accepts 24 megapixels; the signature image is limited to 1 MB/2 megapixels. Results are capped at 15 MB. The application does not create a permanent archive of uploads. HTTP API responses use `no-store`; browser assets are served from the same origin with no analytics or third-party frontend resources. HTTPS terminates at Render; files are uploaded to the application server, not processed entirely on the user's device.

The source is available at https://github.com/abdazzamajhari/markitdown-web. The application's source retains its MIT license; the PyMuPDF dependency is separately licensed under AGPL/commercial terms (https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright). Retain dependency notices and observe their terms when redistributing or deploying modified versions.

## PDF pipeline

1. Validate the PDF and enforce the 30-page limit.
2. Render **each entire page** to a JPEG with Poppler (`pdftoppm`, longest side 2800 px). Embedded images, text, stamps, and tables are flattened together in that page image. The **OCR diperbesar** button rerenders the selected page at 3200 px and replaces its OCR text when successful.
3. Run Tesseract (`ind+eng`) once on the rendered image for both text and word coordinates. The original PDF bytes are never sent to an OCR model. The selectable PDF text layer does not form the Markdown output.
4. Display the page image and OCR text. The browser processes all pages in order and assembles the Markdown download only when every page has been processed. Blank/unreadable pages are reported for review.
5. When the user enables external AI, send **only its OCR text** to SumoPod `deepseek-v4.1-flash:netra` for a separate linguistic plausibility review. Show detected languages and up to three literal excerpts that may need inspection. This review cannot compare against the page image and never changes the OCR transcription or blocks Markdown download. A provider failure is shown separately. With external AI off, this step is skipped.

The SumoPod vision probe remains outside the PDF path; the PDF review uses a text-only request. SumoPod is used for separately uploaded PNG/JPEG/WebP/TIFF images only with external AI enabled, after normalization to PNG. Tesseract can misread small print, handwriting, stamps, and tables; inspect the Markdown against page images before relying on sensitive details. PDF images are not sent to SumoPod, but OCR text is transmitted during an enabled language review.

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
| `/api/image-compress` | `width`, `height` (0=original/automatic), `keep_ratio`, `quality`, `format` (`jpeg`, `png`, `webp`, `tiff`), `remove_metadata` |
| `/api/image-preview` | normalized, metadata-free PNG, longest side at most 2048 px; `max_pixels` can lower the 24-megapixel input limit (signature preview uses 2 megapixels) |
| `/api/pdf-compress` | `profile` (`lossless`, `balanced`, `small`), `quality`, `dpi`, `remove_metadata` |
| `/api/pdf-merge` | body=PDF bytes concatenated in the requested order; `sizes` (2–20 positive integer byte lengths, exactly covering the body), `remove_metadata`; 20 MB combined, 100 output pages |
| `/api/pdf-editor-preview` | `page` (1-based); returns JPEG as base64, page dimensions/count; no OCR or AI |
| `/api/pdf-sign` | body=PDF bytes followed by signature bytes; `pdf_size`, `placements` (`page`, `x`, `y`, `w`, `h`, coordinates 0–1), `remove_white`, `remove_metadata` |

Downloads return `Content-Disposition` and `X-Document-Info` JSON with dimensions/page count and applied operations. Invalid settings return 400; signed PDFs 409; exceeded limits 413; invalid/encrypted PDFs 422. Busy workers return 429. No uploaded filename, body or signature bytes are logged by the application.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
node --test tests/test_session.cjs
```

Tests check real binary outputs, merge page order/text/geometry/forms/links/annotations/bookmarks/metadata and malformed framing/limits, TIFF input/output/LZW/metadata/preview/OCR/signatures and multipage refusal, image orientation/transparency/metadata, PDF compression, rotated/cropped signature geometry, multiple placements, encrypted/signed PDF refusal, and external-AI consent. Dependency-free Node tests cover automatic session cleanup, early return, suspended timers, cached-page restore, clock changes and empty sessions. GitHub CI runs both suites and the Docker build; Render's existing service deploys from `main` after checks pass.
