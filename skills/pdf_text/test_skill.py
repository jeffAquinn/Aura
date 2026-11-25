"""Runs inside the skill's own sandbox image (no network, vault read-only)."""

from pathlib import Path

from skill import extract, run

def make_pdf(text: str) -> bytes:
    """A minimal, valid one-page PDF showing `text` (offsets computed, so it always parses)."""
    stream = f"BT /F1 24 Tf 20 72 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
    out += b"startxref\n" + str(xref).encode() + b"\n%%EOF\n"
    return out


def test_extracts_text_page_by_page():
    pdf = Path("/tmp/hello.pdf")
    pdf.write_bytes(make_pdf("Hello Aura"))
    out = extract(pdf)
    assert out.startswith("--- page 1 ---") and "Hello Aura" in out


def test_refuses_paths_outside_the_vault():
    assert run({"path": "../etc/passwd.pdf"}) == "That path is outside the vault."


def test_refuses_files_that_are_not_pdfs():
    assert run({"path": "notes.txt"}) == "That isn't a PDF file."


def test_reports_a_missing_file():
    assert "no PDF" in run({"path": "missing/report.pdf"})
