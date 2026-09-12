"""Generates the shareable score report PDF (feature: users can hand this to
a lender/landlord who isn't integrated with ScoreWise directly). Pure-Python
via reportlab — no system dependency (unlike e.g. weasyprint, which needs a
native GTK/Cairo install), which matters since this runs on a dev machine
without those preinstalled and should deploy the same way anywhere later.
"""

import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .scoring import ScoreResult

_VIOLET = colors.HexColor("#4a3fb8")
_TEXT_SECONDARY = colors.HexColor("#74736a")
_STATUS_COLORS = {
    "strong": colors.HexColor("#17794f"),
    "moderate": colors.HexColor("#b3760f"),
    "risk": colors.HexColor("#c1502e"),
}


def generate_score_report_pdf(profile_name: str | None, result: ScoreResult) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=24 * mm,
        bottomMargin=20 * mm,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title", parent=styles["Title"], textColor=_VIOLET, fontSize=20, spaceAfter=2)
    meta_style = ParagraphStyle("Meta", parent=styles["Normal"], textColor=_TEXT_SECONDARY, fontSize=9, spaceAfter=16)
    score_style = ParagraphStyle("Score", parent=styles["Title"], fontSize=44, leading=48, spaceAfter=0)
    delta_style = ParagraphStyle(
        "Delta",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#17794f") if result.delta >= 0 else colors.HexColor("#c1502e"),
        spaceAfter=16,
    )
    section_style = ParagraphStyle("Section", parent=styles["Heading3"], fontSize=11, spaceBefore=14, spaceAfter=6)
    body_style = ParagraphStyle("Body", parent=styles["Normal"], fontSize=9.5, leading=13.5)
    disclaimer_style = ParagraphStyle("Disclaimer", parent=styles["Normal"], fontSize=7.5, textColor=_TEXT_SECONDARY, leading=11)

    generated_at = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    name = profile_name or "ScoreWise user"

    story = [
        Paragraph("ScoreWise Score Report", title_style),
        Paragraph(f"Prepared for {name} &middot; Generated {generated_at}", meta_style),
        Paragraph(str(result.score), score_style),
        Paragraph(
            f"{'&uarr; +' if result.delta >= 0 else '&darr; '}{result.delta} since last period"
            if result.delta != 0
            else "No change since last period",
            delta_style,
        ),
        Paragraph("What's shaping this score", section_style),
    ]

    table_data = [["Signal", "Status", "Explanation"]]
    for s in result.signals:
        table_data.append([s.label, s.status.capitalize(), Paragraph(s.explanation, body_style)])

    table = Table(table_data, colWidths=[110, 60, 260])
    table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, 0), (-1, 0), _TEXT_SECONDARY),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                ("TOPPADDING", (0, 1), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#e6e3da")),
                ("LINEBELOW", (0, 1), (-1, -2), 0.5, colors.HexColor("#e6e3da")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
            + [("TEXTCOLOR", (1, i + 1), (1, i + 1), _STATUS_COLORS[s.status]) for i, s in enumerate(result.signals)]
        )
    )
    story.append(table)

    story.append(Paragraph("Tip", section_style))
    story.append(Paragraph(result.tip, body_style))

    story.append(Spacer(1, 24))
    story.append(
        Paragraph(
            "This is an informational score estimate produced by ScoreWise from patterns in the user's own M-Pesa "
            "transaction history. It is not an official or regulated credit score, is not issued by any credit "
            "reference bureau, and is not a guarantee that any lender will approve, reject, or offer any particular "
            "terms on a loan. Lenders make their own decisions using their own criteria. Nothing in this report is "
            "financial, credit, investment, or legal advice.",
            disclaimer_style,
        )
    )

    doc.build(story)
    return buf.getvalue()
