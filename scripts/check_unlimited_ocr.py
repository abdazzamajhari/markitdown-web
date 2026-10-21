"""Run with python -m scripts.check_unlimited_ocr after configuring the GPU endpoint."""

import io
import secrets
import time

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont

from app import unlimited_ocr


def main() -> int:
    challenge = "OCR " + secrets.token_hex(3).upper()
    with Image.new("RGB", (1000, 260), "white") as image:
        ImageDraw.Draw(image).text((50, 70), challenge, fill="black", font=ImageFont.load_default(size=84))
        output = io.BytesIO()
        image.save(output, "PNG")
    started = time.monotonic()
    try:
        result = unlimited_ocr.analyze(output.getvalue(), "image/png")
        if challenge.split()[1] not in result["markdown"].upper():
            print("FAIL: Unlimited-OCR did not read the fresh image challenge correctly.")
            return 1
    except HTTPException as exc:
        print(f"FAIL ({exc.status_code}): {exc.detail}")
        return 1
    print(f"PASS: configured Unlimited-OCR endpoint read the fresh challenge ({time.monotonic() - started:.1f}s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
