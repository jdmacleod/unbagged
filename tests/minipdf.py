"""A minimal, valid, multi-page PDF built by hand.

Committing a real PDF is not an option here — .gitignore denies *.pdf outside
fixtures for good reason — and pulling in a PDF writer just to test the reader
would be a dependency for one test file. This emits a few hundred bytes that
pdfplumber reads as text on numbered pages, which is all extraction needs.
"""

def build_pdf(pages: list[str]) -> bytes:
    """One line of text per page, in page order."""
    # Object 1 catalog, 2 page tree, 3 font, then a page and a content
    # stream per page.
    objs: list[tuple[int, bytes]] = []
    n_pages = len(pages)
    page_ids = [4 + i * 2 for i in range(n_pages)]
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objs.append((1, b"<< /Type /Catalog /Pages 2 0 R >>"))
    objs.append((2, f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode()))
    objs.append((3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
    for i, text in enumerate(pages):
        page_id, content_id = 4 + i * 2, 5 + i * 2
        objs.append((page_id, (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode()))
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objs.append((
            content_id,
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        ))
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num, body in sorted(objs):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + body + b"\nendobj\n"
    xref_at = len(out)
    max_id = max(offsets)
    out += b"xref\n0 %d\n" % (max_id + 1)
    out += b"0000000000 65535 f \n"
    for num in range(1, max_id + 1):
        out += b"%010d 00000 n \n" % offsets.get(num, 0)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (max_id + 1, xref_at)
    return bytes(out)
