"""
LuxeLife API — Agreement PDF rendering.

Generates a PDF rendition of an Agreement when it becomes ACTIVE
(both parties accounted for, advance confirmed) and uploads it to
GCS via the existing StorageService. The public URL is stored on
agreement.pdf_url and surfaced to the FE Download button.

Triggered from two activation sites:
  - AgreementService.admin_confirm_advance  (manager path)
  - PaymentService._activate_agreement_for_payment  (verify path)
"""

import io
import uuid
from datetime import datetime, timezone

import structlog
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib import colors

from app.models.agreement import Agreement
from app.services.storage_service import StorageService

logger = structlog.get_logger()


class AgreementPdfService:
    """Renders an Agreement to PDF + uploads to cloud storage."""

    @classmethod
    async def render_and_upload(cls, agreement: Agreement) -> str | None:
        """
        Render the given Agreement to a PDF, upload to GCS, and return the URL.

        Idempotent at the storage layer — each render uses a unique key so
        re-runs do not overwrite previous copies. Callers should persist the
        returned URL on `agreement.pdf_url`.

        Returns None on render/upload failure rather than raising; the
        activation path should not be blocked by PDF generation.
        """
        try:
            pdf_bytes = cls._render(agreement)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Agreement PDF render failed",
                agreement_id=agreement.id,
                error=str(e),
            )
            return None

        try:
            key = f"agreements/{agreement.id}_{uuid.uuid4().hex}.pdf"
            url = StorageService._upload(key, pdf_bytes, "application/pdf")
            logger.info("Agreement PDF uploaded", agreement_id=agreement.id, url=url)
            return url
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Agreement PDF upload failed",
                agreement_id=agreement.id,
                error=str(e),
            )
            return None

    # ── internal ─────────────────────────────────────────────────────────

    @classmethod
    def _render(cls, agreement: Agreement) -> bytes:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=18 * mm,
            bottomMargin=18 * mm,
            title=f"Rental Agreement — {agreement.id}",
        )

        styles = getSampleStyleSheet()
        h_title = ParagraphStyle(
            "TitleStyle",
            parent=styles["Title"],
            fontSize=16,
            spaceAfter=8,
            alignment=TA_CENTER,
        )
        h_sub = ParagraphStyle(
            "SubStyle",
            parent=styles["Normal"],
            fontSize=9,
            textColor=colors.grey,
            alignment=TA_CENTER,
            spaceAfter=12,
        )
        body = ParagraphStyle(
            "BodyStyle",
            parent=styles["Normal"],
            fontSize=10,
            leading=14,
            alignment=TA_LEFT,
        )
        label = ParagraphStyle(
            "LabelStyle",
            parent=styles["Normal"],
            fontSize=8,
            textColor=colors.grey,
            spaceAfter=2,
        )
        sig_name = ParagraphStyle(
            "SigName",
            parent=styles["Normal"],
            fontSize=11,
            fontName="Helvetica-Bold",
        )

        story = []

        # Header
        story.append(Paragraph("RESIDENTIAL LEASE AGREEMENT", h_title))
        story.append(
            Paragraph(
                f"Agreement ID: {agreement.id} · "
                f"Generated: {datetime.now(timezone.utc).strftime('%d %b %Y, %H:%M UTC')}",
                h_sub,
            )
        )

        # Parties summary table
        prop = getattr(agreement, "property", None)
        tenant = getattr(agreement, "tenant", None)
        owner = getattr(agreement, "owner", None)

        summary_rows = [
            ["Property",  prop.name if prop else agreement.property_id],
            ["Landlord",  owner.name if owner else agreement.owner_id],
            ["Tenant",    tenant.name if tenant else agreement.tenant_id],
            ["Monthly Rent",      f"Rs. {agreement.rent_amount:,}"],
            ["Security Deposit",  f"Rs. {agreement.security_deposit:,}"],
            ["Maintenance",       f"Rs. {agreement.maintenance_charges:,}"],
            ["Lease Period",
             f"{_fmt_date(agreement.lease_start)} to {_fmt_date(agreement.lease_end)} "
             f"({agreement.lease_duration_months} months)"],
            ["Status",  agreement.status.value.upper()],
        ]
        tbl = Table(summary_rows, colWidths=[45 * mm, 120 * mm])
        tbl.setStyle(TableStyle([
            ("FONTNAME",   (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE",   (0, 0), (-1, -1), 9),
            ("TEXTCOLOR",  (0, 0), (0, -1), colors.grey),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("LINEBELOW",  (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 10))

        # Body — the existing terms_text wall, split into paragraphs.
        if agreement.terms_text:
            for chunk in agreement.terms_text.split("\n\n"):
                lines = [l.strip() for l in chunk.splitlines() if l.strip()]
                if not lines:
                    continue
                # Preserve hard line breaks within a chunk via <br/>
                html = "<br/>".join(_escape(l) for l in lines)
                story.append(Paragraph(html, body))
                story.append(Spacer(1, 6))

        story.append(Spacer(1, 18))

        # Signature blocks
        sig_data = [
            [
                Paragraph("LANDLORD", label),
                Paragraph("TENANT", label),
            ],
            [
                Paragraph(_escape(owner.name) if owner else "—", sig_name),
                Paragraph(_escape(tenant.name) if tenant else "—", sig_name),
            ],
            [
                Paragraph(
                    _sig_line(agreement.owner_signature, agreement.owner_signed_at),
                    body,
                ),
                Paragraph(
                    _sig_line(agreement.tenant_signature, agreement.tenant_signed_at),
                    body,
                ),
            ],
        ]
        sig_tbl = Table(sig_data, colWidths=[85 * mm, 85 * mm])
        sig_tbl.setStyle(TableStyle([
            ("VALIGN",  (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING",    (0, 0), (-1, -1), 6),
            ("LINEABOVE", (0, 2), (-1, 2), 0.5, colors.black),
        ]))
        story.append(sig_tbl)

        if agreement.advance_confirmed:
            story.append(Spacer(1, 10))
            story.append(
                Paragraph(
                    "<i>Advance / security deposit has been confirmed by the platform administrator. "
                    "This agreement is ACTIVE.</i>",
                    body,
                )
            )

        doc.build(story)
        return buffer.getvalue()


def _escape(s: str | None) -> str:
    if not s:
        return ""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
    )


def _fmt_date(d) -> str:
    if not d:
        return "—"
    return d.strftime("%d %b %Y")


def _sig_line(signature: str | None, signed_at) -> str:
    if not signature:
        return "<i>Not signed</i>"
    when = signed_at.strftime("%d %b %Y, %H:%M UTC") if signed_at else "—"
    # Signature stored as drawn URL or typed string. Render typed name as the
    # visible mark; URLs (signature image) are referenced by URL since
    # reportlab won't fetch remote images here.
    if signature.startswith(("http://", "https://")):
        mark = "(digital signature on file)"
    else:
        mark = _escape(signature)
    return f"<b>{mark}</b><br/>Signed: {when}"
