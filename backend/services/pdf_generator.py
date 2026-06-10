"""PDF generation service for Job Descriptions using ReportLab."""

from __future__ import annotations

import logging
from pathlib import Path

from models.jd_generation import GeneratedJD

logger = logging.getLogger("ta_agent.pdf_generator")


def _get_reportlab():
    """Lazy import so startup doesn't fail if reportlab isn't installed."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            HRFlowable,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )

        return colors, A4, ParagraphStyle, getSampleStyleSheet, mm, HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise RuntimeError(
            "reportlab is required for PDF generation. Install it: pip install reportlab"
        ) from exc


def generate_jd_pdf(jd: GeneratedJD, output_dir: Path) -> Path:
    """Render *jd* to a PDF file and return its path.

    The file is saved as ``{output_dir}/{jd.jd_id}.pdf``.
    """
    (
        colors, A4, ParagraphStyle, getSampleStyleSheet, mm,
        HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    ) = _get_reportlab()

    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / f"{jd.jd_id}.pdf"

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=jd.content.title,
        author="Parkar Digital – Talent Acquisition",
    )

    # ── Color palette ──────────────────────────────────────────────────
    parkar_blue = colors.HexColor("#1E3A5F")
    parkar_accent = colors.HexColor("#2563EB")
    light_bg = colors.HexColor("#F0F4FF")
    section_header_bg = colors.HexColor("#E8EEFF")
    text_dark = colors.HexColor("#1E293B")
    text_muted = colors.HexColor("#64748B")
    bullet_color = colors.HexColor("#2563EB")

    styles = getSampleStyleSheet()

    # ── Custom paragraph styles ────────────────────────────────────────
    company_name_style = ParagraphStyle(
        "CompanyName",
        parent=styles["Normal"],
        fontSize=22,
        fontName="Helvetica-Bold",
        textColor=colors.white,
        spaceAfter=2,
        leading=26,
    )
    tagline_style = ParagraphStyle(
        "Tagline",
        parent=styles["Normal"],
        fontSize=10,
        fontName="Helvetica",
        textColor=colors.HexColor("#BFD3FF"),
        spaceAfter=0,
        leading=13,
    )
    job_title_style = ParagraphStyle(
        "JobTitle",
        parent=styles["Normal"],
        fontSize=18,
        fontName="Helvetica-Bold",
        textColor=parkar_blue,
        spaceAfter=4,
        spaceBefore=10,
        leading=22,
    )
    meta_style = ParagraphStyle(
        "Meta",
        parent=styles["Normal"],
        fontSize=10,
        fontName="Helvetica",
        textColor=text_muted,
        spaceAfter=0,
        leading=14,
    )
    section_header_style = ParagraphStyle(
        "SectionHeader",
        parent=styles["Normal"],
        fontSize=12,
        fontName="Helvetica-Bold",
        textColor=parkar_blue,
        spaceBefore=14,
        spaceAfter=6,
        leading=16,
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=10,
        fontName="Helvetica",
        textColor=text_dark,
        spaceAfter=4,
        leading=15,
    )
    bullet_style = ParagraphStyle(
        "Bullet",
        parent=styles["Normal"],
        fontSize=10,
        fontName="Helvetica",
        textColor=text_dark,
        leftIndent=14,
        firstLineIndent=-14,
        spaceAfter=4,
        leading=15,
        bulletIndent=0,
        bulletText=None,
    )

    story = []

    # ── Header banner ──────────────────────────────────────────────────
    header_data = [[
        Paragraph("Parkar Digital", company_name_style),
        Paragraph("Enabling Digital Transformation Globally", tagline_style),
    ]]
    header_table = Table(header_data, colWidths=["100%"])
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), parkar_blue),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 8))

    # ── Job title + meta ──────────────────────────────────────────────
    story.append(Paragraph(jd.content.title, job_title_style))
    story.append(HRFlowable(width="100%", thickness=2, color=parkar_accent, spaceAfter=6))

    meta_info = (
        f"<b>Business Unit:</b> {jd.business_unit} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"<b>Designation:</b> {jd.designation} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"<b>Experience:</b> {jd.years_of_experience}+ year(s)"
    )
    story.append(Paragraph(meta_info, meta_style))
    story.append(Spacer(1, 10))

    # ── Role Summary ──────────────────────────────────────────────────
    story.append(Paragraph("About the Role", section_header_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CBD5E1"), spaceAfter=6))
    story.append(Paragraph(jd.content.summary, body_style))

    # ── Key Responsibilities ───────────────────────────────────────────
    story.append(Paragraph("Key Responsibilities", section_header_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CBD5E1"), spaceAfter=6))
    for item in jd.content.responsibilities:
        story.append(Paragraph(f"•  {item}", bullet_style))

    # ── Required Skills ────────────────────────────────────────────────
    story.append(Paragraph("Required Skills & Qualifications", section_header_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CBD5E1"), spaceAfter=6))
    for item in jd.content.required_skills:
        story.append(Paragraph(f"•  {item}", bullet_style))

    # ── Nice to Have ───────────────────────────────────────────────────
    if jd.content.nice_to_have:
        story.append(Paragraph("Nice to Have", section_header_style))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CBD5E1"), spaceAfter=6))
        for item in jd.content.nice_to_have:
            story.append(Paragraph(f"•  {item}", bullet_style))

    # ── Qualifications ────────────────────────────────────────────────
    if jd.content.qualifications:
        story.append(Paragraph("Qualifications", section_header_style))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CBD5E1"), spaceAfter=6))
        for item in jd.content.qualifications:
            story.append(Paragraph(f"•  {item}", bullet_style))

    # ── What We Offer ─────────────────────────────────────────────────
    if jd.content.what_we_offer:
        story.append(Paragraph("What We Offer", section_header_style))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CBD5E1"), spaceAfter=6))
        for item in jd.content.what_we_offer:
            story.append(Paragraph(f"•  {item}", bullet_style))

    # ── Footer note ────────────────────────────────────────────────────
    story.append(Spacer(1, 16))
    footer_style = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontSize=9,
        fontName="Helvetica-Oblique",
        textColor=text_muted,
        alignment=1,
        leading=13,
    )
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CBD5E1"), spaceAfter=8))
    story.append(Paragraph(
        "Parkar Digital is an equal opportunity employer. "
        "We celebrate diversity and are committed to creating an inclusive environment for all employees.",
        footer_style,
    ))

    doc.build(story)
    logger.info("PDF generated: %s", pdf_path)
    return pdf_path
