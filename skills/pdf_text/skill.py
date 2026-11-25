"""Extract the text of a PDF in the vault, page by page."""

from pathlib import Path

from pypdf import PdfReader

VAULT = Path("/vault")
LIMIT = 12_000


def extract(pdf: Path) -> str:
    reader = PdfReader(pdf)
    pages = [f"--- page {i} ---\n{(page.extract_text() or '').strip()}" for i, page in enumerate(reader.pages, 1)]
    text = "\n\n".join(pages)
    return text[:LIMIT] + (f"\n[truncated at {LIMIT} characters]" if len(text) > LIMIT else "")


def run(args: dict) -> str:
    rel = str(args.get("path", "")).strip().lstrip("/")
    target = (VAULT / rel).resolve()
    if not str(target).startswith(str(VAULT) + "/"):
        return "That path is outside the vault."
    if target.suffix.lower() != ".pdf":
        return "That isn't a PDF file."
    if not target.is_file():
        return f"There's no PDF at '{rel}'."
    return extract(target)
