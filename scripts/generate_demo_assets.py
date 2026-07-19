"""Generate the deterministic multimodal PDF used by the demo and integration tests."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image as ReportLabImage,
)
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def _status_image(path: Path) -> None:
    image = Image.new("RGB", (960, 360), "#0b1020")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.load_default(size=28)
    label_font = ImageFont.load_default(size=22)
    value_font = ImageFont.load_default(size=20)
    draw.rounded_rectangle(
        (40, 36, 920, 324), radius=28, fill="#141b34", outline="#7c5cff", width=4
    )
    draw.text((80, 68), "Continuation readiness", fill="#f5f7ff", font=title_font)
    rows = [
        ("Canonical evidence", 0.95, "#55d6be"),
        ("Browser validation", 0.82, "#7c5cff"),
        ("Hosted proof", 0.24, "#ffb86b"),
    ]
    for index, (label, ratio, color) in enumerate(rows):
        top = 126 + index * 62
        draw.text((80, top), label, fill="#dce4ff", font=label_font)
        draw.rounded_rectangle((350, top, 850, top + 26), radius=13, fill="#283252")
        draw.rounded_rectangle((350, top, 350 + int(500 * ratio), top + 26), radius=13, fill=color)
        draw.text((864, top), f"{ratio:.0%}", fill="#f5f7ff", font=value_font)
    image.save(path, format="PNG")


def generate_pdf(output: Path) -> Path:
    """Write a two-page PDF containing prose, a table, and a raster chart."""

    output.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    with tempfile.TemporaryDirectory(prefix="handoff-forge-pdf-") as temp_dir:
        image_path = Path(temp_dir) / "continuation-readiness.png"
        _status_image(image_path)
        document = SimpleDocTemplate(
            str(output),
            pagesize=letter,
            rightMargin=0.65 * inch,
            leftMargin=0.65 * inch,
            topMargin=0.6 * inch,
            bottomMargin=0.6 * inch,
            title="Northstar Continuity Review",
            author="Handoff Forge",
        )
        story = [
            Paragraph("Northstar Continuity Review", styles["Title"]),
            Spacer(1, 0.16 * inch),
            Paragraph(
                "This report is a deterministic fixture for proving that handoff evidence keeps "
                "native text, tables, page locations, images, and validation boundaries together.",
                styles["BodyText"],
            ),
            Spacer(1, 0.2 * inch),
        ]
        table_data = [
            ["Workstream", "State", "Evidence", "Owner"],
            ["Canonical store", "Complete", "42 fixture assertions", "Platform"],
            ["Merge planner", "In progress", "Conflict ledger green", "Runtime"],
            ["Hosted launch", "Deferred", "Local preview only", "Operations"],
        ]
        table = Table(table_data, colWidths=[1.55 * inch, 1.15 * inch, 2.25 * inch, 1.1 * inch])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4634a8")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#eef1ff")),
                    ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#17203d")),
                    ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#8793bd")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        story.extend(
            [
                table,
                Spacer(1, 0.24 * inch),
                Paragraph(
                    "Decision: preserve local/offline proof separately from hosted and provider "
                    "proof. Do not remove the source hashes or the explicit Do Not Touch rules.",
                    styles["BodyText"],
                ),
                PageBreak(),
                Paragraph("Readiness evidence", styles["Heading1"]),
                Spacer(1, 0.15 * inch),
                ReportLabImage(str(image_path), width=6.4 * inch, height=2.4 * inch),
                Spacer(1, 0.18 * inch),
                Paragraph(
                    "The chart shows that canonical evidence and browser validation are ahead "
                    "of hosted proof. A continuation plan must keep hosted launch as deferred "
                    "until an environment-specific validation exists.",
                    styles["BodyText"],
                ),
            ]
        )
        document.build(story)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("examples/northstar-continuity-review.pdf"),
    )
    args = parser.parse_args()
    print(generate_pdf(args.output.resolve()))


if __name__ == "__main__":
    main()
