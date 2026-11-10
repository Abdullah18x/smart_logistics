"""Message templates: what each notification says, per channel.

Jinja2 with autoescaping for HTML. Email has a subject, a plain-text body and
an HTML body (wrapped in one shared layout); SMS and push are short text only.
Rendering happens when a notification is created, and the result is stored,
so the delivery log shows exactly what each person received.
"""

from __future__ import annotations

from dataclasses import dataclass

from jinja2 import Environment, StrictUndefined, select_autoescape
from markupsafe import Markup

from notification.models import Channel

_text = Environment(undefined=StrictUndefined, autoescape=False, trim_blocks=True)
_html = Environment(undefined=StrictUndefined, autoescape=select_autoescape(default=True))

LAYOUT = """<!doctype html>
<html><body style="margin:0;background:#f4f6f8;font-family:Helvetica,sans-serif;color:#1f2933">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px">
<table width="560" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px">
<tr><td style="padding:20px 28px;border-bottom:3px solid #1d4ed8;font-weight:bold;color:#1d4ed8">
SmartLogistics</td></tr>
<tr><td style="padding:24px 28px;font-size:15px;line-height:1.55">{{ content }}</td></tr>
<tr><td style="padding:14px 28px;font-size:12px;color:#7b8794;border-top:1px solid #e4e7eb">
TransFleet · SmartLogistics · This is an automated message.</td></tr>
</table></td></tr></table></body></html>"""


@dataclass(frozen=True)
class Template:
    subject: str | None = None
    text: str = ""
    html: str | None = None


TEMPLATES: dict[tuple[str, Channel], Template] = {
    # --- customers ------------------------------------------------------------------
    ("shipment_confirmed", Channel.EMAIL): Template(
        subject="We've got your order — {{ reference_no }}",
        text=(
            "Hi {{ name }},\n\nYour shipment {{ reference_no }} is booked and being prepared "
            "at our warehouse. We'll let you know when it is on its way.\n\n"
            "Track it any time: {{ tracking_url }}"
        ),
        html=(
            "<p>Hi {{ name }},</p><p>Your shipment <b>{{ reference_no }}</b> is booked and "
            "being prepared at our warehouse. We'll let you know when it is on its way.</p>"
            '<p><a href="{{ tracking_url }}">Track your shipment</a></p>'
        ),
    ),
    ("shipment_dispatched", Channel.EMAIL): Template(
        subject="Your shipment {{ reference_no }} is on its way",
        text=(
            "Hi {{ name }},\n\nGood news: {{ reference_no }} has left the warehouse and a "
            "courier has it.\n\nTrack it: {{ tracking_url }}"
        ),
        html=(
            "<p>Hi {{ name }},</p><p>Good news: <b>{{ reference_no }}</b> has left the "
            "warehouse and a courier has it.</p>"
            '<p><a href="{{ tracking_url }}">Track your shipment</a></p>'
        ),
    ),
    ("shipment_dispatched", Channel.SMS): Template(
        text="SmartLogistics: {{ reference_no }} is on its way. Track: {{ tracking_url }}"
    ),
    ("out_for_delivery", Channel.SMS): Template(
        text="SmartLogistics: {{ reference_no }} is out for delivery today. Please keep your "
        "phone close."
    ),
    ("delivered", Channel.EMAIL): Template(
        subject="Delivered — {{ reference_no }}",
        text="Hi {{ name }},\n\n{{ reference_no }} has been delivered. Thank you!",
        html="<p>Hi {{ name }},</p><p><b>{{ reference_no }}</b> has been delivered. Thank you!</p>",
    ),
    ("delivered", Channel.SMS): Template(text="SmartLogistics: {{ reference_no }} was delivered."),
    ("delivery_attempt_failed", Channel.SMS): Template(
        text="SmartLogistics: we couldn't deliver {{ reference_no }}"
        "{% if reason %} ({{ reason }}){% endif %}. We'll try again."
    ),
    ("return_started", Channel.EMAIL): Template(
        subject="{{ reference_no }} is being returned",
        text=(
            "Hi {{ name }},\n\nWe could not complete delivery of {{ reference_no }}, so it is "
            "on its way back to our warehouse."
            "{% if reason %}\n\nReason: {{ reason }}{% endif %}"
        ),
        html=(
            "<p>Hi {{ name }},</p><p>We could not complete delivery of <b>{{ reference_no }}"
            "</b>, so it is on its way back to our warehouse.</p>"
            "{% if reason %}<p>Reason: {{ reason }}</p>{% endif %}"
        ),
    ),
    ("shipment_cancelled", Channel.EMAIL): Template(
        subject="Cancelled — {{ reference_no }}",
        text="Hi {{ name }},\n\nShipment {{ reference_no }} has been cancelled."
        "{% if reason %}\n\nReason: {{ reason }}{% endif %}",
        html="<p>Hi {{ name }},</p><p>Shipment <b>{{ reference_no }}</b> has been cancelled.</p>"
        "{% if reason %}<p>Reason: {{ reason }}</p>{% endif %}",
    ),
    # --- couriers -------------------------------------------------------------------
    ("new_assignment", Channel.PUSH): Template(
        text="New job: {{ reference_no }} from {{ warehouse }}. Open the app to start."
    ),
    ("new_assignment", Channel.SMS): Template(
        text="SmartLogistics: you have a new job, {{ reference_no }}, from {{ warehouse }}."
    ),
    # --- operations -----------------------------------------------------------------
    ("dispatch_failed", Channel.EMAIL): Template(
        subject="[Dispatch failed] {{ reference_no }}",
        text=(
            "Dispatch of {{ reference_no }} (shipment {{ shipment_id }}) failed.\n\n"
            "Reason: {{ reason or 'not given' }}\n\n"
            "The stock hold is kept. Fix the cause, set it ready_for_dispatch and dispatch "
            "again, or cancel it."
        ),
        html=(
            "<p>Dispatch of <b>{{ reference_no }}</b> (shipment <code>{{ shipment_id }}</code>) "
            "failed.</p><p>Reason: {{ reason or 'not given' }}</p><p>The stock hold is kept. "
            "Fix the cause, set it <code>ready_for_dispatch</code> and dispatch again, or "
            "cancel it.</p>"
        ),
    ),
}


@dataclass(frozen=True)
class Rendered:
    subject: str | None
    body: str
    html: str | None


def render(template: str, channel: Channel, context: dict) -> Rendered:
    spec = TEMPLATES[(template, channel)]
    subject = _text.from_string(spec.subject).render(context) if spec.subject else None
    html = None
    if spec.html:
        content = _html.from_string(spec.html).render(context)
        html = _html.from_string(LAYOUT).render(content=Markup(content))
    return Rendered(subject=subject, body=_text.from_string(spec.text).render(context), html=html)
