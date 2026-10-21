# PrivasiGuard

A document workspace for personal-data-aware processing: Markdown extraction, image compression and pixel resizing, PDF compression, and visual PDF signatures. The existing GitHub repository and Render address remain the deployment target.

## Categories and document tools

The interface groups features by file category. **Images** contains image OCR and compression/resize; **PDF** contains page OCR, PDF compression, PDF merging, and the visual signature editor; **Other documents** contains Markdown extraction from DOCX/PPTX/XLSX/TXT/CSV/JSON. OCR uploads, result lists, counters, combined Markdown and ZIP downloads are scoped to the selected category. Switching categories retains each category's results and last selected tool.

Every upload supports file-picker clicks and drag and drop, including compression, merging, the PDF editor and signature images. Drop onto the matching upload area or elsewhere on the active tool. Compression drops replace the selected batch; merge drops append files in drop order. The signature editor accepts one PDF or one signature image at a time and routes page-wide drops by extension. File types and size limits are checked before selection changes, and busy tools reject replacements. Dropped files remain part of the browser session and are covered by automatic cleanup.

- **Image compression and resize:** batch PNG/JPEG/WebP/TIFF (`.tif` and `.tiff`) uploads; width/height in pixels, aspect-ratio control, quality 20–95 for JPEG/WebP, PNG and LZW TIFF output, before/after preview, individual downloads, and metadata removal enabled by default. TIFF is supported for OCR and uploaded PDF signature images as well. TIFF previews are converted to metadata-free PNG on the application server because browser decoding varies. Only single-page static images are accepted; multipage TIFF is refused rather than silently truncated. Output uses 8-bit RGB/RGBA; PNG/TIFF encoding is lossless after color-mode conversion, but this does not preserve 16-bit samples or CMYK channels. JPEG transparency is flattened onto white. Keeping metadata retains EXIF and ICC where supported, after EXIF orientation is normalized; obsolete TIFF storage tags are regenerated for the new image.
- **PDF compression:** batch uploads; lossless stream/object optimization or balanced/small image recompression with quality and DPI controls. Selectable text, page dimensions, links and form values are retained. Alpha masks and unusual image color spaces are skipped. Compression is not guaranteed to reduce file size. Document Info/XMP metadata is removed by default; JavaScript actions are scrubbed from exported PDFs.
- **PDF merging:** add 2–20 PDFs, move each file up/down or remove it, and download one PDF containing all pages in the chosen order. Input is limited to 10 MB per file, 20 MB combined and 100 pages in the result; output remains capped at 15 MB. Pages retain their text, images, crop/rotation, ordinary links, annotations and form values; form names are disambiguated so equal names in different documents do not become a shared field. Bookmarks are copied with page offsets. Document-level attachments, optional-content configuration, PDF portfolios and named destinations are not reconstructed. Cryptographic signatures/signature fields and encrypted PDFs are refused. Metadata removal is on by default; when off, Document Info/XMP from the first PDF is used. No external AI is called. Existing OCR/compression/signature tools retain their 30-page limit.
- **PDF signature editor:** upload or draw one signature image, add up to 30 placements across pages, drag, resize, adjust X/Y, remove placements, preview and download the final PDF. Coordinates are normalized to the displayed cropped/rotated page; the original crop and rotation are preserved. This is a **visual signature**, not a certificate-backed cryptographic signature, identity verification or certified signing service. PDFs with signature fields or signature dictionaries are refused for editing/compression to avoid invalidating existing signatures.
- **Privacy controls:** image and PDF OCR use **Unlimited-OCR** and require explicit consent (`X-External-AI: true`) before image transmission to the configured GPU endpoint. There is no automatic fallback to Tesseract or SumoPod vision. SumoPod Netra language review has a separate optional checkbox and transmits OCR text only. Clearing the browser session reloads the page and releases its retained files/results. This runs automatically after the tab is continuously hidden (switching tabs or minimizing the browser) for 60 seconds; returning sooner cancels the deadline. Elapsed time is checked again on return and on a back/forward-cache restore because browsers may suspend background timers. Visible tabs are not cleared for inactivity. Empty sessions do not repeatedly reload in the background. No file or result is stored in web storage. Downloaded files are unaffected. Removing metadata does not redact personal information in visible text, images, annotations or form values.

Document decoding, normalization, rendering and offline tools use a bounded, short-lived worker with no inherited provider credentials and worker network calls disabled. Unlimited-OCR requests run in the parent API process; the configured GPU server receives normalized images only. No public demo or unrelated OCR service is used automatically. Input files are limited to 10 MB; merging accepts up to 20 MB combined and 100 pages, while other PDF tools accept 30 pages. Image compression accepts 24 megapixels; the signature image is limited to 1 MB/2 megapixels. Results are capped at 15 MB. The application does not create a permanent archive of uploads. HTTP API responses use `no-store`; browser assets are served from the same origin with no analytics or third-party frontend resources. HTTPS terminates at Render; files are uploaded to the application server, not processed entirely on the user's device.

The source is available at https://github.com/abdazzamajhari/markitdown-web. The application's source retains its MIT license; the PyMuPDF dependency is separately licensed under AGPL/commercial terms (https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright). Retain dependency notices and observe their terms when redistributing or deploying modified versions.

## Unlimited-OCR migration

Image and PDF OCR use [`baidu/Unlimited-OCR`](https://github.com/baidu/Unlimited-OCR). This replaces both Tesseract OCR and SumoPod vision; the model is **not** installed in the Render web container. The existing Render free service has no GPU. An independently hosted, reachable Unlimited-OCR **vLLM endpoint** is required before this migration can be activated. The Hugging Face public demo is not used as a production backend.

### Configure the inference endpoint

Set these environment variables on the application server (never commit credentials):

| Variable | Value |
| --- | --- |
| `UNLIMITED_OCR_BASE_URL` | HTTPS OpenAI-compatible base URL ending in `/v1`, e.g. `https://ocr.example.com/v1` |
| `UNLIMITED_OCR_API_KEY` | Endpoint bearer token, if required; independent of `SUMOPOD_API_KEY` |
| `UNLIMITED_OCR_MODEL` | Served model name, default `baidu/Unlimited-OCR`; response model identity must match |
| `SUMOPOD_API_KEY` | Optional, **text-only** Netra language review; not used for OCR |

Only loopback endpoints permit HTTP for local development. URLs with credentials, query strings, fragments or invalid ports are refused. Keep the GPU service behind TLS and authentication; apply its own access and retention controls. Clearing this site's browser session does not delete a GPU provider's logs.

Use the [official vLLM recipe](https://recipes.vllm.ai/baidu/Unlimited-OCR), including its custom logits processor. Example on an existing compatible NVIDIA GPU host:

```bash
docker run --rm --gpus all --ipc host -p 127.0.0.1:8000:8000 \
  vllm/vllm-openai:unlimited-ocr \
  baidu/Unlimited-OCR --trust-remote-code \
  --logits_processors vllm.model_executor.models.unlimited_ocr:NGramPerReqLogitsProcessor \
  --no-enable-prefix-caching --mm-processor-cache-gb 0
```

Expose this server through the host's authenticated HTTPS gateway for Render. For an app running on that same host, the development URL can be `http://127.0.0.1:8000/v1`. This command is setup guidance; no GPU host is provisioned by the web app.

The client sends the required literal `<image>document parsing.` prompt, `skip_special_tokens=false`, `temperature=0`, and `vllm_xargs={ngram_size:35,window_size:128}`. Every request contains one image, so the single-image window is used even for a PDF page. It allows 8192 output tokens and rejects `finish_reason=length`, wrong-model, empty, malformed, oversized or failed responses instead of presenting partial text as success. Provider calls use a 120-second time budget and bounded response reads. No automatic rewriting or generic vision probe is applied.

### Image and PDF pipeline

1. Require explicit user consent to send document images to the GPU server. DOCX/PPTX/XLSX/TXT/CSV/JSON extraction and compression/merge/signature tools remain independent of external OCR.
2. Normalize single-page PNG/JPEG/WebP/TIFF to metadata-free PNG in the constrained worker, preserving orientation and flattening transparency. Image input limits remain 10 MB/8 megapixels, output longest side 2048 px.
3. For PDFs, validate the 10 MB/30-page limit and render **each entire page** to JPEG using Poppler (`pdftoppm`, longest side 2800 px; zoom OCR uses 3200 px). Send only that page image to Unlimited-OCR, never original PDF bytes or its selectable text layer.
4. Decode both released grounding forms (`<|ref|>label<|/ref|><|det|>boxes<|/det|>` and `<|det|>label [box]<|/det|>`). Normalize boxes by **999**, matching the upstream model implementation. Preserve the model's reading order and Markdown/HTML table text; exclude image placeholders. Invalid coordinates are omitted without discarding valid text. Missing coordinates do not trigger another model or fabricate boxes.
5. Display the real page image with OCR text and available region boxes. Process pages in numeric order and assemble Markdown in that order. Retain image previews and clear error messages when inference fails; provide complete-document downloads only after every page has usable OCR. Blank/image-only pages require review and are not silently counted as successful OCR.
6. If separately enabled, send OCR text to SumoPod `deepseek-v4.1-flash:netra` for linguistic plausibility review. This never changes Unlimited-OCR transcription, compares against the page image, or blocks its download. A language-review outage is reported separately.

### Run and activation check

```bash
pip install -r requirements.txt
# Install poppler-utils; Tesseract is no longer required.
python -m scripts.check_unlimited_ocr
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Supply the endpoint variables through the host's secret/environment settings before running the check. The check sends a generated image with a fresh random text challenge, verifies the configured model's response, and prints only a pass/fail summary. Passing unit tests with mocked inference does **not** demonstrate GPU availability or actual OCR accuracy. Run this check against the real endpoint, then test an image and a multipage PDF before merging/deploying the migration. `GET /health` checks the web app only; it does not load/probe the GPU. `/api/capabilities` reports configuration availability, not a live inference guarantee.

## API

`POST /api/convert` returns image OCR Markdown or validates a PDF and returns empty initial Markdown. OCR requests require `X-Filename` and `X-External-AI: true`; missing GPU configuration returns 503. The browser calls `POST /api/pdf-preview?page=N` with the same consent header to receive the full-page JPEG, `page_text`, `regions`, and page count. Successful PDF OCR has `page_source=unlimited-ocr`. Provider failure retains the image and returns `ocr_error`/`ocr_status` with no successful page text. `preview_only=true` renders locally without OCR and requires no external consent. `POST /api/regions` returns Unlimited-OCR grounding regions, with consent. `POST /api/pdf-language-check` accepts text and its own consent request for the separate optional Netra review. No provider credentials or endpoint URL appear in capability responses.

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
npm ci --ignore-scripts
npm test
```

Tests check real binary outputs, merge page order/text/geometry/forms/links/annotations/bookmarks/metadata and malformed framing/limits, TIFF input/output/LZW/metadata/preview/OCR/signatures and multipage refusal, image orientation/transparency/metadata, PDF compression, rotated/cropped signature geometry, multiple placements, encrypted/signed PDF refusal, and external-OCR consent. Mocked vLLM tests verify required payloads, model identity, text/box parsing, metadata removal, image-only PDF transmission, provider errors/timeouts, truncation and independent text-only language review. Node tests cover automatic session cleanup and DOM upload flows: file drops, picker parity, page-wide routing, TIFF, malformed selections, busy controls, merge order and retained-session detection. GitHub CI runs both suites and the Docker build; Render's existing service deploys from `main` after checks pass.
