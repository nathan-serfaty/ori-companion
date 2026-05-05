import io, re, time
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    KeepTogether, Frame, PageTemplate, BaseDocTemplate
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT


# Colors
NAVY = HexColor("#0F1729")
RED = HexColor("#E63946")
GRAY_LIGHT = HexColor("#F3F4F6")
GRAY_TEXT = HexColor("#6B7280")
GRAY_BORDER = HexColor("#E5E7EB")
AMBER_BG = HexColor("#FFF7ED")
AMBER_BORDER = HexColor("#FED7AA")
AMBER_TEXT = HexColor("#92400E")


def _header_footer(canvas, doc):
    w, h = A4
    # Header band
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, h - 18*mm, w, 18*mm, fill=1, stroke=0)
    # Logo square
    logo_x = 22*mm
    logo_y = h - 14*mm
    canvas.setFillColor(RED)
    canvas.roundRect(logo_x, logo_y, 10*mm, 10*mm, 2*mm, fill=1, stroke=0)
    canvas.setFillColor(white)
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawString(logo_x + 2.8*mm, logo_y + 2.5*mm, "L")
    # Title
    canvas.setFillColor(white)
    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawString(logo_x + 14*mm, logo_y + 5*mm, "L'Etudiant")
    canvas.setFillColor(HexColor("#9CA3AF"))
    canvas.setFont("Helvetica", 8)
    canvas.drawString(logo_x + 14*mm, logo_y + 0.5*mm, "ORI - Assistant d'orientation")
    # Date right
    canvas.setFillColor(HexColor("#9CA3AF"))
    canvas.setFont("Helvetica", 7)
    now = time.strftime("%d/%m/%Y a %H:%M")
    canvas.drawRightString(w - 22*mm, h - 9*mm, f"Genere le {now}")
    tid_short = getattr(doc, '_ori_thread_id', '')[:8]
    canvas.drawRightString(w - 22*mm, h - 13*mm, f"Thread {tid_short}")
    canvas.restoreState()

    # Footer band
    canvas.saveState()
    canvas.setFillColor(GRAY_LIGHT)
    canvas.rect(0, 0, w, 14*mm, fill=1, stroke=0)
    canvas.setFillColor(GRAY_TEXT)
    canvas.setFont("Helvetica-Oblique", 7)
    canvas.drawCentredString(w/2, 8*mm,
        "Ce document est une synthese de ta conversation avec ORI. Il ne constitue pas un avis d'orientation professionnel.")
    canvas.drawCentredString(w/2, 4*mm,
        "Pour valider ton projet, rencontre un conseiller d'orientation (professeur principal, PsyEN, Salon de l'Etudiant).")
    canvas.setFont("Helvetica", 7)
    canvas.drawRightString(w - 22*mm, 5*mm, f"Page {doc.page}")
    canvas.restoreState()


def _md_to_paragraph(text, style):
    """Convert light markdown (**bold**, *italic*) to reportlab XML."""
    t = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)
    t = re.sub(r'\*(.+?)\*', r'<i>\1</i>', t)
    t = t.replace("\n", "<br/>")
    return Paragraph(t, style)


def generate_bilan_pdf(thread_id: str, profile: dict, history: list, last_bilan: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=22*mm, rightMargin=22*mm,
        topMargin=26*mm, bottomMargin=20*mm,
    )
    doc._ori_thread_id = thread_id

    # Styles
    s_title = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=22,
                             textColor=NAVY, leading=26)
    s_subtitle = ParagraphStyle("subtitle", fontName="Helvetica", fontSize=10,
                                textColor=GRAY_TEXT, leading=14, spaceBefore=4)
    s_section = ParagraphStyle("section", fontName="Helvetica-Bold", fontSize=12,
                               textColor=RED, leading=16, spaceBefore=20, spaceAfter=8)
    s_body = ParagraphStyle("body", fontName="Helvetica", fontSize=10.5,
                            textColor=NAVY, leading=15)
    s_body_gray = ParagraphStyle("bodygray", fontName="Helvetica-Oblique", fontSize=10,
                                 textColor=GRAY_TEXT, leading=14)
    s_label = ParagraphStyle("label", fontName="Helvetica", fontSize=10,
                             textColor=GRAY_TEXT)
    s_value = ParagraphStyle("value", fontName="Helvetica-Bold", fontSize=10,
                             textColor=NAVY)
    s_small = ParagraphStyle("small", fontName="Helvetica", fontSize=9,
                             textColor=GRAY_TEXT, leading=13)
    s_small_bold = ParagraphStyle("smallbold", fontName="Helvetica-Bold", fontSize=9,
                                  textColor=NAVY, leading=13)
    s_amber = ParagraphStyle("amber", fontName="Helvetica", fontSize=10,
                             textColor=AMBER_TEXT, leading=14)

    elements = []

    # Title
    elements.append(Paragraph("Mon profil d'orientation", s_title))
    elements.append(Paragraph(
        "Synthese de ta conversation avec ORI, l'assistant d'orientation de L'Etudiant.", s_subtitle))
    elements.append(Spacer(1, 8))

    # Section 01 - Profil
    elements.append(Paragraph("01 — PROFIL DETECTE", s_section))
    profile_map = {
        "secteur_detecte": "Secteur d'interet",
        "niveau": "Niveau actuel",
        "preference": "Preference",
        "maturite": "Maturite du projet",
    }
    table_data = []
    for key, label in profile_map.items():
        val = profile.get(key, "Non detecte")
        table_data.append([
            Paragraph(label, s_label),
            Paragraph(str(val), s_value),
        ])
    if table_data:
        t = Table(table_data, colWidths=[6*cm, 9.5*cm])
        t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LINEBELOW', (0, 0), (-1, -2), 0.5, GRAY_BORDER),
        ]))
        elements.append(t)

    # Section 02 - Bilan
    elements.append(Paragraph("02 — BILAN ORI", s_section))
    if last_bilan:
        for para in last_bilan.split("\n"):
            para = para.strip()
            if para:
                elements.append(_md_to_paragraph(para, s_body))
                elements.append(Spacer(1, 4))
    else:
        elements.append(Paragraph(
            "Aucun bilan formel n'a encore ete produit dans la conversation. "
            "Pose une question de synthese a ORI pour le declencher.", s_body_gray))

    # Section 03 - Conversation extract
    elements.append(Paragraph("03 — EXTRAIT DE CONVERSATION", s_section))
    last_turns = history[-8:]  # Last 4 exchanges = 8 messages
    for msg in last_turns:
        role_label = "Toi" if msg["role"] == "user" else "ORI"
        role_style = s_small_bold if msg["role"] == "user" else ParagraphStyle(
            "ori_msg", fontName="Helvetica-Bold", fontSize=9, textColor=RED, leading=13)
        content = msg["content"][:600]
        if len(msg["content"]) > 600:
            content += "..."
        elements.append(Paragraph(f"<b>{role_label}</b>", role_style))
        elements.append(Paragraph(content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>"), s_small))
        elements.append(Spacer(1, 6))

    # Section 04 - Cadre d'usage
    elements.append(Paragraph("04 — CADRE D'USAGE", s_section))
    amber_text = (
        "<b>ORI fournit des pistes de reflexion, pas une decision.</b> "
        "Toute orientation finale doit etre discutee avec un conseiller humain : "
        "professeur principal, psychologue de l'Education Nationale (PsyEN), "
        "ou conseiller rencontre lors d'un Salon de l'Etudiant."
    )
    amber_para = Paragraph(amber_text, s_amber)
    amber_table = Table([[amber_para]], colWidths=[15*cm])
    amber_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), AMBER_BG),
        ('BOX', (0, 0), (-1, -1), 1, AMBER_BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
    ]))
    elements.append(amber_table)

    # Section 05 - Pour aller plus loin
    elements.append(Paragraph("05 — POUR ALLER PLUS LOIN", s_section))
    resources = [
        ("Salons de l'Etudiant", "Rencontre des conseillers et des ecoles en presentiel sur letudiant.fr/salons"),
        ("Metiers & formations", "Explore les fiches metiers et formations sur letudiant.fr/metiers"),
        ("Parcoursup 2026", "Calendrier officiel et conseils sur letudiant.fr/etudes/parcoursup"),
    ]
    res_data = []
    for title, desc in resources:
        res_data.append([
            Paragraph(f"<b>{title}</b>", s_small_bold),
            Paragraph(desc, s_small),
        ])
    res_table = Table(res_data, colWidths=[5*cm, 10.5*cm])
    res_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LINEBELOW', (0, 0), (-1, -2), 0.5, GRAY_BORDER),
    ]))
    elements.append(res_table)

    doc.build(elements, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return buf.getvalue()
