"""Shipping labels: one A6 PDF per package, with a scannable Code 128 barcode.

A real carrier integration would call the carrier's label API here; the label
layout below carries what a warehouse and a courier actually read: reference,
package sequence, barcode, origin, destination, service level and weight.
"""

from __future__ import annotations

import io

from reportlab.graphics.barcode import code128
from reportlab.lib.pagesizes import A6
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from shipment.models import Package, Shipment


def render_label(shipment: Shipment, package: Package, *, package_count: int) -> bytes:
    buffer = io.BytesIO()
    width, height = A6
    pdf = canvas.Canvas(buffer, pagesize=A6)
    pdf.setTitle(f"{shipment.reference_no} {package.sequence_no}/{package_count}")
    margin = 6 * mm
    y = height - margin

    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(margin, y - 10, "SmartLogistics")
    pdf.setFont("Helvetica", 8)
    pdf.drawRightString(width - margin, y - 10, shipment.service_level.value.upper())
    y -= 18
    pdf.line(margin, y, width - margin, y)

    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(margin, y - 14, shipment.reference_no)
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(width - margin, y - 14, f"Package {package.sequence_no} of {package_count}")
    y -= 22

    barcode = code128.Code128(package.barcode, barHeight=16 * mm, barWidth=0.32 * mm)
    barcode.drawOn(pdf, (width - barcode.width) / 2, y - 16 * mm - 2)
    y -= 16 * mm + 6
    pdf.setFont("Courier", 8)
    pdf.drawCentredString(width / 2, y - 6, package.barcode)
    y -= 14
    pdf.line(margin, y, width - margin, y)

    address = shipment.destination_address
    pdf.setFont("Helvetica", 7)
    pdf.drawString(margin, y - 10, "SHIP TO")
    pdf.setFont("Helvetica-Bold", 10)
    lines = [
        address.contact_name,
        address.line1,
        address.line2,
        ", ".join(p for p in (address.city, address.postal_code) if p),
        address.contact_phone,
    ]
    text_y = y - 22
    for line in (line for line in lines if line):
        pdf.drawString(margin, text_y, line[:48])
        pdf.setFont("Helvetica", 9)
        text_y -= 11
    y = text_y - 4
    pdf.line(margin, y, width - margin, y)

    pdf.setFont("Helvetica", 7)
    pdf.drawString(margin, y - 10, f"FROM  {shipment.origin_warehouse_code}")
    pdf.drawRightString(width - margin, y - 10, f"{package.weight_g / 1000:.2f} kg")
    if shipment.special_instructions:
        pdf.drawString(margin, y - 20, shipment.special_instructions[:70])

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
