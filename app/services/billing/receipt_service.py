"""Server-generated invoice and receipt PDFs from immutable records."""
from __future__ import annotations

from io import BytesIO
from textwrap import wrap

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from app.services.billing.money import mask_reference


def _money(value, currency: str) -> str:
    return f"{currency} {value:,.2f}"


def _safe(value, fallback="Unavailable") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def render_invoice_pdf(invoice, *, lines=None, tenant=None) -> bytes:
    """Render one invoice; all totals come from the persisted invoice row."""
    if lines is None:
        from app.models.billing_center import InvoiceLine
        lines = InvoiceLine.query.filter_by(invoice_id=invoice.id).order_by(InvoiceLine.position.asc()).all()
    tenant = tenant or getattr(invoice, "tenant", None)
    buffer = BytesIO()
    width, height = A4
    pdf = canvas.Canvas(buffer, pagesize=A4, pageCompression=1)
    pdf.setTitle(_safe(invoice.invoice_number, "Invoice"))
    pdf.setAuthor("MyPortfolioHub Billing")
    pdf.setSubject("Immutable billing invoice")

    margin = 18 * mm
    navy = colors.HexColor("#17233B")
    violet = colors.HexColor("#6957E8")
    muted = colors.HexColor("#65738B")
    border = colors.HexColor("#DDE3EE")
    soft = colors.HexColor("#F5F7FB")

    pdf.setFillColor(navy)
    pdf.rect(0, height - 48 * mm, width, 48 * mm, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(margin, height - 22 * mm, "MyPortfolioHub")
    pdf.setFont("Helvetica", 9)
    pdf.drawString(margin, height - 29 * mm, "Billing receipt generated from immutable records")
    pdf.setFont("Helvetica-Bold", 24)
    pdf.drawRightString(width - margin, height - 22 * mm, "INVOICE")
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(width - margin, height - 29 * mm, _safe(invoice.invoice_number))

    y = height - 62 * mm
    pdf.setFillColor(navy)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(margin, y, "BILLED TO")
    pdf.drawString(width / 2 + 5 * mm, y, "INVOICE DETAILS")
    y -= 7 * mm
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(margin, y, _safe(getattr(tenant, "company_name", None) or getattr(tenant, "slug", None), "Tenant account"))
    pdf.setFont("Helvetica", 9)
    tenant_slug = getattr(tenant, "slug", None)
    if tenant_slug:
        pdf.drawString(margin, y - 5 * mm, f"Account: {tenant_slug}")

    detail_x = width / 2 + 5 * mm
    pdf.setFont("Helvetica", 9)
    details = [
        ("Issued", invoice.issued_at.strftime("%Y-%m-%d %H:%M UTC") if invoice.issued_at else "Unavailable"),
        ("Status", _safe(invoice.status).upper()),
        ("Plan", _safe(invoice.plan)),
        ("Cycle", _safe(invoice.billing_cycle).title()),
        ("Provider", _safe(invoice.payment_provider).title()),
        ("Reference", mask_reference(invoice.payment_reference)),
    ]
    detail_y = y
    for label, value in details:
        pdf.setFillColor(muted)
        pdf.drawString(detail_x, detail_y, label)
        pdf.setFillColor(navy)
        pdf.drawRightString(width - margin, detail_y, value)
        detail_y -= 5 * mm

    y = min(y - 18 * mm, detail_y - 5 * mm)
    table_width = width - margin * 2
    pdf.setFillColor(soft)
    pdf.roundRect(margin, y - 10 * mm, table_width, 10 * mm, 2 * mm, fill=1, stroke=0)
    pdf.setFillColor(navy)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(margin + 4 * mm, y - 6.5 * mm, "DESCRIPTION")
    pdf.drawRightString(width - margin - 4 * mm, y - 6.5 * mm, "AMOUNT")
    y -= 15 * mm

    rendered_lines = list(lines or [])
    if not rendered_lines:
        rendered_lines = [type("LegacyLine", (), {
            "description": f"{invoice.plan} - {invoice.billing_cycle}",
            "amount": invoice.amount_subtotal,
        })()]
    for line in rendered_lines:
        if y < 55 * mm:
            pdf.showPage()
            y = height - 24 * mm
        pdf.setFillColor(navy)
        pdf.setFont("Helvetica", 9)
        chunks = wrap(_safe(line.description), width=68) or ["Unavailable"]
        for chunk in chunks:
            pdf.drawString(margin + 4 * mm, y, chunk)
            y -= 4.5 * mm
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawRightString(width - margin - 4 * mm, y + 4.5 * mm * len(chunks), _money(line.amount, invoice.currency))
        pdf.setStrokeColor(border)
        pdf.line(margin, y - 2 * mm, width - margin, y - 2 * mm)
        y -= 7 * mm

    totals_x = width - margin - 70 * mm
    y -= 3 * mm
    totals = [
        ("Subtotal", invoice.amount_subtotal),
        ("Discount", -invoice.amount_discount),
        ("Tax", invoice.amount_tax),
    ]
    for label, value in totals:
        pdf.setFillColor(muted)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(totals_x, y, label)
        pdf.setFillColor(navy)
        pdf.drawRightString(width - margin, y, _money(value, invoice.currency))
        y -= 6 * mm
    pdf.setStrokeColor(violet)
    pdf.setLineWidth(1.2)
    pdf.line(totals_x, y + 2 * mm, width - margin, y + 2 * mm)
    pdf.setFillColor(navy)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(totals_x, y - 4 * mm, "Total")
    pdf.drawRightString(width - margin, y - 4 * mm, _money(invoice.amount_total, invoice.currency))

    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 7.5)
    footer = "This document was generated server-side from immutable invoice data. Corrections are issued as status events and replacement invoices."
    pdf.drawCentredString(width / 2, 13 * mm, footer)
    pdf.drawCentredString(width / 2, 8 * mm, f"Invoice {_safe(invoice.invoice_number)} | Plan snapshot {_safe(invoice.plan_version, 'legacy-unversioned')}")
    pdf.save()
    return buffer.getvalue()
