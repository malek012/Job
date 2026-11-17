"""
pdf_writer.py — PDF generator using ReportLab (embedded fonts, reliable rendering).

Produces valid PDF-1.4 documents with embedded Helvetica font.
Falls back to pure-Python generator if reportlab is unavailable.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    _HAS_REPORTLAB = True
except ImportError:
    _HAS_REPORTLAB = False


def _write_pdf_reportlab(lines: List[str], output_path: Path) -> None:
    """Write PDF using ReportLab with embedded fonts."""
    c = canvas.Canvas(str(output_path), pagesize=A4)
    width, height = A4
    x = 20 * mm
    y = height - 20 * mm
    line_height = 5 * mm
    font_size = 10

    c.setFont("Helvetica", font_size)
    c.setFillColorRGB(0, 0, 0)

    for line in lines:
        if y < 20 * mm:
            c.showPage()
            c.setFont("Helvetica", font_size)
            c.setFillColorRGB(0, 0, 0)
            y = height - 20 * mm
        c.drawString(x, y, line)
        y -= line_height

    c.save()


def _write_pdf_fallback(lines: List[str], output_path: Path) -> None:
    """Pure-Python PDF fallback (no external deps)."""
    from dataclasses import dataclass, field

    @dataclass
    class _PDF:
        pages: List[List[str]] = field(default_factory=list)

        def add_page(self, lines: List[str]) -> None:
            self.pages.append(lines)

        def _escape_pdf_string(self, s: str) -> str:
            s = s.replace("\\", "\\\\")
            s = s.replace("(", "\\(")
            s = s.replace(")", "\\)")
            s = s.replace("\n", "\\n")
            s = s.replace("\r", "\\r")
            return s

        def _content_stream(self, lines: List[str], y_start: float = 740.0,
                            line_height: float = 14.0, x: float = 50.0,
                            font_size: float = 11.0) -> bytes:
            ops: List[str] = [
                "BT",
                f"/F1 {font_size} Tf",
                f"{font_size} TL",
                f"{x} {y_start} Td",
            ]
            for i, line in enumerate(lines):
                if i > 0:
                    ops.append(f"0 -{line_height} Td")
                ops.append(f"({self._escape_pdf_string(line)}) Tj")
            ops.append("ET")
            stream = "\n".join(ops).encode("latin-1", errors="replace")
            return stream

        def _build(self) -> bytes:
            objects: List[str] = []
            objects.append("<< /Type /Catalog /Pages 2 0 R >>")
            kids = " ".join(f"{i + 3} 0 R" for i in range(len(self.pages)))
            objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(self.pages)} >>")
            for _ in self.pages:
                objects.append("<< /Type /Page /Parent 2 0 R >>")

            font_obj_idx = 3 + len(self.pages)
            objects.insert(font_obj_idx, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

            content_obj_idx = font_obj_idx + 1
            for page_idx, lines in enumerate(self.pages):
                page_obj_idx = 3 + page_idx
                content_obj = content_obj_idx + page_idx
                stream = self._content_stream(lines)
                objects[page_obj_idx] = (
                    f"<< /Type /Page /Parent 2 0 R "
                    f"/MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 {content_obj_idx + page_idx + 1} 0 R >> >> "
                    f"/Contents {content_obj_idx + page_idx + 1} 0 R >>"
                )
                objects.insert(content_obj + page_idx,
                               f"<< /Length {len(stream)} >>\nstream\n{stream.decode('latin-1', errors='replace')}\nendstream")

            out: List[str] = []
            out.append("%PDF-1.4")
            xref_offsets: List[int] = []
            obj_body: List[str] = []

            def emit_obj(obj_num: int, body: str) -> int:
                off = len("".join(out)) + len("\n".join(obj_body)) + (1 if obj_body else 0)
                xref_offsets.append(off)
                obj_body.append(f"{obj_num} 0 obj\n{body}\nendobj")

            obj_num = 1
            for body in objects:
                emit_obj(obj_num, body)
                obj_num += 1

            xref_start = len("".join(out)) + len("\n".join(obj_body)) + (1 if obj_body else 0)
            xref_lines: List[str] = ["xref", f"0 {len(xref_offsets) + 1}"]
            xref_lines.append("0000000000 65535 f ")
            for off in xref_offsets:
                xref_lines.append(f"{off:010d} 00000 n ")

            trailer = (
                "trailer\n"
                f"<< /Size {len(xref_offsets) + 1} /Root 1 0 R >>\n"
                "startxref\n"
                f"{xref_start}\n"
                "%%EOF"
            )

            pdf = "\n".join(out) + "\n" + "\n".join(obj_body) + "\n" + "\n".join(xref_lines) + "\n" + trailer + "\n"
            return pdf.encode("latin-1", errors="replace")

    pdf = _PDF()
    pdf.add_page(lines)
    data = pdf._build()
    output_path.write_bytes(data)


def write_pdf(lines: List[str], output_path: Path) -> None:
    """Write a minimal PDF from text lines to output_path."""
    if _HAS_REPORTLAB:
        _write_pdf_reportlab(lines, output_path)
    else:
        _write_pdf_fallback(lines, output_path)


def write_pdf_multi(pages: List[List[str]], output_path: Path) -> None:
    """Write a multi-page PDF from a list of pages."""
    if _HAS_REPORTLAB:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        c = canvas.Canvas(str(output_path), pagesize=A4)
        width, height = A4
        x = 20 * mm
        line_height = 5 * mm
        font_size = 10
        c.setFont("Helvetica", font_size)
        c.setFillColorRGB(0, 0, 0)
        for page_lines in pages:
            y = height - 20 * mm
            for line in page_lines:
                if y < 20 * mm:
                    c.showPage()
                    c.setFont("Helvetica", font_size)
                    c.setFillColorRGB(0, 0, 0)
                    y = height - 20 * mm
                c.drawString(x, y, line)
                y -= line_height
        c.save()
    else:
        _write_pdf_fallback_multi(pages, output_path)


def _write_pdf_fallback_multi(pages: List[List[str]], output_path: Path) -> None:
    from dataclasses import dataclass, field

    @dataclass
    class _PDF:
        pages: List[List[str]] = field(default_factory=list)

        def add_page(self, lines: List[str]) -> None:
            self.pages.append(lines)

        def _escape_pdf_string(self, s: str) -> str:
            s = s.replace("\\", "\\\\")
            s = s.replace("(", "\\(")
            s = s.replace(")", "\\)")
            s = s.replace("\n", "\\n")
            s = s.replace("\r", "\\r")
            return s

        def _content_stream(self, lines: List[str], y_start: float = 740.0,
                            line_height: float = 14.0, x: float = 50.0,
                            font_size: float = 11.0) -> bytes:
            ops: List[str] = [
                "BT",
                f"/F1 {font_size} Tf",
                f"{font_size} TL",
                f"{x} {y_start} Td",
            ]
            for i, line in enumerate(lines):
                if i > 0:
                    ops.append(f"0 -{line_height} Td")
                ops.append(f"({self._escape_pdf_string(line)}) Tj")
            ops.append("ET")
            stream = "\n".join(ops).encode("latin-1", errors="replace")
            return stream

        def _build(self) -> bytes:
            objects: List[str] = []
            objects.append("<< /Type /Catalog /Pages 2 0 R >>")
            kids = " ".join(f"{i + 3} 0 R" for i in range(len(self.pages)))
            objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(self.pages)} >>")
            for _ in self.pages:
                objects.append("<< /Type /Page /Parent 2 0 R >>")

            font_obj_idx = 3 + len(self.pages)
            objects.insert(font_obj_idx, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

            content_obj_idx = font_obj_idx + 1
            for page_idx, lines in enumerate(self.pages):
                page_obj_idx = 3 + page_idx
                content_obj = content_obj_idx + page_idx
                stream = self._content_stream(lines)
                objects[page_obj_idx] = (
                    f"<< /Type /Page /Parent 2 0 R "
                    f"/MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 {content_obj_idx + page_idx + 1} 0 R >> >> "
                    f"/Contents {content_obj_idx + page_idx + 1} 0 R >>"
                )
                objects.insert(content_obj + page_idx,
                               f"<< /Length {len(stream)} >>\nstream\n{stream.decode('latin-1', errors='replace')}\nendstream")

            out: List[str] = []
            out.append("%PDF-1.4")
            xref_offsets: List[int] = []
            obj_body: List[str] = []

            def emit_obj(obj_num: int, body: str) -> int:
                off = len("".join(out)) + len("\n".join(obj_body)) + (1 if obj_body else 0)
                xref_offsets.append(off)
                obj_body.append(f"{obj_num} 0 obj\n{body}\nendobj")

            obj_num = 1
            for body in objects:
                emit_obj(obj_num, body)
                obj_num += 1

            xref_start = len("".join(out)) + len("\n".join(obj_body)) + (1 if obj_body else 0)
            xref_lines: List[str] = ["xref", f"0 {len(xref_offsets) + 1}"]
            xref_lines.append("0000000000 65535 f ")
            for off in xref_offsets:
                xref_lines.append(f"{off:010d} 00000 n ")

            trailer = (
                "trailer\n"
                f"<< /Size {len(xref_offsets) + 1} /Root 1 0 R >>\n"
                "startxref\n"
                f"{xref_start}\n"
                "%%EOF"
            )

            pdf = "\n".join(out) + "\n" + "\n".join(obj_body) + "\n" + "\n".join(xref_lines) + "\n" + trailer + "\n"
            return pdf.encode("latin-1", errors="replace")

    pdf = _PDF()
    for page_lines in pages:
        pdf.add_page(page_lines)
    data = pdf._build()
    output_path.write_bytes(data)
