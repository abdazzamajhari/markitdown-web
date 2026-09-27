"""Check that the browser export produces a real, safely named ZIP archive."""

import base64
import io
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest


def test_browser_zip_contains_every_markdown():
    if not shutil.which("node"):
        pytest.skip("Node.js unavailable")
    script = """
      require('./app/static/zip.js');
      (async () => {
        const zip = createMarkdownZip([
          {name: 'invoice.png', markdown: '# Invoice\\nSumoPod'},
          {name: 'invoice.pdf', markdown: 'PDF content'},
          {name: '../hasil_日本語.txt', markdown: ''}
        ]);
        process.stdout.write(Buffer.from(await zip.arrayBuffer()).toString('base64'));
      })().catch(error => { console.error(error); process.exit(1); });
    """
    root = Path(__file__).resolve().parents[1]
    encoded = subprocess.check_output(["node", "-e", script], cwd=root, timeout=15)
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(encoded))) as archive:
        assert archive.namelist() == ["invoice.md", "invoice (2).md", "hasil_日本語.md"]
        assert archive.read("invoice.md").decode() == "# Invoice\nSumoPod"
        assert archive.read("invoice (2).md").decode() == "PDF content"
        assert archive.read("hasil_日本語.md") == b""
        assert archive.testzip() is None
