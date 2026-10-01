# MarkItDown Web

A web interface for converting documents to Markdown. PDF OCR runs on full-page raster images locally; separate image files use the configured SumoPod vision route.

## PDF pipeline

1. Validate the PDF and enforce the 30-page limit.
2. Render **each entire page** to a JPEG with Poppler (`pdftoppm`, longest side 2800 px). Embedded images, text, stamps, and tables are flattened together in that page image. The **OCR diperbesar** button rerenders the selected page at 3200 px and replaces its OCR text when successful.
3. Run Tesseract (`ind+eng`) once on the rendered image for both text and word coordinates. The original PDF bytes are never sent to an OCR model. The selectable PDF text layer does not form the Markdown output.
4. Display the page image and OCR text. The browser processes all pages in order and assembles the Markdown download only when every page has been processed. Blank/unreadable pages are reported for review.
5. After each successful OCR page, send **only its OCR text** to SumoPod `deepseek-v4.1-flash:netra` for a separate linguistic plausibility review. Show detected languages and up to three literal excerpts that may need inspection. This review cannot compare against the page image and never changes the OCR transcription or blocks Markdown download. A provider failure is shown separately.

The SumoPod vision probe remains outside the PDF path; the PDF review uses a text-only request. It remains in use for separately uploaded PNG/JPEG/WebP images. Tesseract can misread small print, handwriting, stamps, and tables; inspect the Markdown against the page images before relying on sensitive details. PDF images are not sent to SumoPod, but OCR text is transmitted to SumoPod during language review.

## Run

```bash
pip install -r requirements.txt
# Install poppler-utils, tesseract-ocr, tesseract-ocr-eng, tesseract-ocr-ind
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The Dockerfile installs the OCR dependencies. `SUMOPOD_API_KEY` is required for PDF language review and separate image uploads; PDF OCR remains available when the key or language review fails. Do not commit keys. The server processes uploads in memory and does not persist them. Other supported inputs are DOCX, PPTX, XLSX, TXT, CSV, and JSON.

## API

`POST /api/convert` validates a PDF and returns an empty initial Markdown document. The browser calls `POST /api/pdf-preview?page=N` for each page to receive the rendered JPEG, OCR `page_text`, word `regions`, and page count. For PDF, `page_source` is `tesseract` when text is recognized. `POST /api/pdf-language-check` accepts OCR text and returns a separate text-only Netra review. `GET /health` reports server availability.
