# ADR-020: Notification service — event-driven, logged, sent by tasks

**Status:** Accepted · **Date:** 2026-11-10

## Context

Customers expect a message when their shipment is confirmed, dispatched, out for delivery,
delivered, delayed by a failed attempt, returned or cancelled; couriers need to hear about a
new job; operations about a failed dispatch. Real email/SMS/push providers are out of scope
(PRD §8) but the delivery path must be real enough to test: retries, duplicates, opt-outs,
and "did they get told?" for support.

## Decision

- A **notification** service (:8009, PostgreSQL :5447) consumes `shipment.events` and
  `courier.events`. It keeps its own copies of what it needs — the recipient from
  `shipment.created` (which now carries `recipient {name, phone, email}`) and courier contacts
  from courier snapshots — and never calls another service to look someone up.
- **Rules are data** (`rules.py`): status or event → template, audience (customer, courier,
  operations) and channels. Templates are Jinja; email has an HTML layout with escaping.
- **One row per message** in `notifications`, the delivery log: rendered subject/body/html,
  channel, recipient, status (`pending → sent | failed | suppressed`), attempts, last error,
  provider message id. Its id is `uuid5(event_id, template, channel)` and the insert is
  `ON CONFLICT DO NOTHING`, so a redelivered event can never send twice.
- **Sending is a Celery task** queued after the consumer's transaction commits
  (`sl_platform.consumer.after_commit`). The send locks the row, checks suppressions, calls
  the channel, and on a provider error schedules a retry with backoff (5 s doubling, capped
  at 5 min) until `max_send_attempts` (5), then `failed`. See ADR-019.
- **Channels**: email over SMTP (`aiosmtplib`; Mailpit locally, inbox at :8025) with a stable
  `Message-ID`; SMS and push are mock senders that log, behind the same interface.
- **Suppressions** (channel + address) stop messages to opted-out or wrong addresses; support
  manages them, admins remove them. Support can **resend** any finished message (a new row
  linked by `resend_of`). Couriers read their own messages at `/notifications/mine`.

## Consequences

- Support answers "was the customer told?" from the log without provider dashboards.
- Content is fixed at creation; a template fix applies to new messages, and to old ones only
  by resending.
- Per-customer channel preferences are not modelled (only suppressions); adding them is a
  table and a check in `send`.
