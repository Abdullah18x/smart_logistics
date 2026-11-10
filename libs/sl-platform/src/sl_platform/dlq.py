"""Dead letters: events a consumer could not handle, kept for inspection and replay.

A consumer that gives up on an event — its handler failed every retry, the
message is not a valid envelope, or the payload breaks its contract — publishes
the original bytes to ``dlq.<consumer>`` with headers saying why. Its offset is
then committed, so one bad event never blocks the partition behind it.

Operators look and replay with ``./dev.sh dlq``:

    ./dev.sh dlq list                       every dead letter, by consumer
    ./dev.sh dlq list inventory             one consumer's
    ./dev.sh dlq replay inventory --all     send them back to that consumer
    ./dev.sh dlq replay inventory <event-id> ...

Replay publishes the original event to ``retry.<consumer>``: only that consumer
reads it, so services that handled the event the first time are not involved,
and the consumer's own dedupe makes a second replay harmless. A marker on the
dead-letter topic records the replay, so ``list`` shows it as replayed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sl_platform.events import Topics
from sl_platform.metrics import DEAD_LETTERS
from sl_platform.outbox import Publisher

H = "dlq."  # header prefix


class DeadLetters:
    """Publishes a consumer's failed events to its dead-letter topic."""

    def __init__(self, publisher: Publisher) -> None:
        self.publisher = publisher

    async def send(
        self,
        consumer: str,
        value: bytes,
        *,
        reason: str,
        error: str,
        source_topic: str | None,
        key: str | None = None,
        event_id: str | None = None,
        event_type: str | None = None,
        attempts: int = 0,
    ) -> None:
        headers = {
            f"{H}consumer": consumer,
            f"{H}reason": reason,
            f"{H}error": error[:1000],
            f"{H}source-topic": source_topic or "",
            f"{H}event-id": event_id or "",
            f"{H}event-type": event_type or "",
            f"{H}attempts": str(attempts),
            f"{H}failed-at": datetime.now(UTC).isoformat(),
            f"{H}status": "dead",
        }
        await self.publisher.send(
            Topics.dead_letters(consumer), key or event_id or "", value, headers
        )
        DEAD_LETTERS.labels(consumer, reason).inc()


# --- reading them back (the CLI) -----------------------------------------------------


@dataclass
class Letter:
    consumer: str
    event_id: str
    event_type: str
    reason: str
    error: str
    source_topic: str
    failed_at: str
    attempts: int
    value: bytes
    key: str
    status: str = "dead"
    replays: list[str] = field(default_factory=list)


def collapse(records: list[tuple[dict[str, str], bytes, str]]) -> list[Letter]:
    """Fold a dead-letter topic's records (headers, value, key) into one entry per
    event: the failure, then any replay markers that followed it."""
    letters: dict[tuple[str, str], Letter] = {}
    for headers, value, key in records:
        consumer = headers.get(f"{H}consumer", "?")
        event_id = headers.get(f"{H}event-id") or key or "?"
        ident = (consumer, event_id)
        if headers.get(f"{H}status") == "replayed":
            if ident in letters:
                letters[ident].status = "replayed"
                letters[ident].replays.append(headers.get(f"{H}replayed-at", ""))
            continue
        letters[ident] = Letter(
            consumer=consumer,
            event_id=event_id,
            event_type=headers.get(f"{H}event-type", ""),
            reason=headers.get(f"{H}reason", ""),
            error=headers.get(f"{H}error", ""),
            source_topic=headers.get(f"{H}source-topic", ""),
            failed_at=headers.get(f"{H}failed-at", ""),
            attempts=int(headers.get(f"{H}attempts") or 0),
            value=value,
            key=key,
        )
    return list(letters.values())


async def _read_topics(bootstrap: str, topics: list[str] | None) -> list[tuple[dict, bytes, str]]:
    from aiokafka import AIOKafkaConsumer

    consumer = AIOKafkaConsumer(
        bootstrap_servers=bootstrap, enable_auto_commit=False, auto_offset_reset="earliest"
    )
    try:
        await consumer.start()
    except Exception as exc:
        await consumer.stop()
        raise KafkaUnavailableError(bootstrap) from exc
    try:
        names = topics or [t for t in await consumer.topics() if t.startswith("dlq.")]
        if not names:
            return []
        from aiokafka import TopicPartition

        partitions = [
            TopicPartition(t, p) for t in names for p in consumer.partitions_for_topic(t) or ()
        ]
        consumer.assign(partitions)
        await consumer.seek_to_beginning(*partitions)
        end = await consumer.end_offsets(partitions)
        records: list[tuple[dict, bytes, str]] = []
        while any(await consumer.position(tp) < end[tp] for tp in partitions):
            batch = await consumer.getmany(timeout_ms=1000)
            for messages in batch.values():
                for m in messages:
                    headers = {k: v.decode() for k, v in (m.headers or ())}
                    records.append((headers, m.value or b"", (m.key or b"").decode()))
        return records
    finally:
        await consumer.stop()


async def _list(bootstrap: str, consumer: str | None) -> int:
    topics = [Topics.dead_letters(consumer)] if consumer else None
    letters = collapse(await _read_topics(bootstrap, topics))
    if not letters:
        print("No dead letters." + ("" if consumer else " (No dlq.* topics yet.)"))
        return 0
    print(f"{'consumer':14} {'status':9} {'event type':30} {'event id':37} reason")
    for letter in sorted(letters, key=lambda x: (x.consumer, x.failed_at)):
        print(
            f"{letter.consumer:14} {letter.status:9} {letter.event_type[:30]:30} "
            f"{letter.event_id:37} {letter.reason}"
        )
        print(f"{'':14} {'':9} failed {letter.failed_at} after {letter.attempts} attempt(s): "
              f"{letter.error[:160]}")  # fmt: skip
    dead = sum(1 for x in letters if x.status == "dead")
    print(f"\n{len(letters)} dead letter(s), {dead} not replayed yet.")
    return 0


async def _replay(bootstrap: str, consumer: str, event_ids: list[str], replay_all: bool) -> int:
    from sl_platform.outbox import KafkaPublisher

    letters = [
        x
        for x in collapse(await _read_topics(bootstrap, [Topics.dead_letters(consumer)]))
        if x.status == "dead" and (replay_all or x.event_id in event_ids)
    ]
    missing = set(event_ids) - {x.event_id for x in letters}
    if missing:
        print(f"Not found (or already replayed): {', '.join(sorted(missing))}", file=sys.stderr)
    if not letters:
        print("Nothing to replay.")
        return 1 if missing else 0
    publisher = KafkaPublisher(bootstrap)
    try:
        await publisher.start()
    except Exception as exc:
        raise KafkaUnavailableError(bootstrap) from exc
    try:
        now = datetime.now(UTC).isoformat()
        for letter in letters:
            await publisher.send(
                Topics.retry(consumer), letter.key, letter.value, {"replayed-from": "dlq"}
            )
            await publisher.send(
                Topics.dead_letters(consumer),
                letter.key,
                letter.value,
                {
                    f"{H}consumer": consumer,
                    f"{H}event-id": letter.event_id,
                    f"{H}status": "replayed",
                    f"{H}replayed-at": now,
                },
            )
            print(f"replayed {letter.event_type} {letter.event_id} -> {Topics.retry(consumer)}")
    finally:
        await publisher.stop()
    return 0


class KafkaUnavailableError(RuntimeError):
    def __init__(self, bootstrap: str) -> None:
        super().__init__(f"Kafka isn't reachable at {bootstrap}. Start it: ./dev.sh infra up")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dlq", description="Inspect and replay dead letters.")
    parser.add_argument(
        "--bootstrap",
        default=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092"),
    )
    sub = parser.add_subparsers(dest="action", required=True)
    listing = sub.add_parser("list", help="show dead letters")
    listing.add_argument("consumer", nargs="?")
    replay = sub.add_parser("replay", help="send dead letters back to their consumer")
    replay.add_argument("consumer")
    replay.add_argument("event_ids", nargs="*")
    replay.add_argument("--all", action="store_true", help="every letter not replayed yet")
    args = parser.parse_args(argv)
    if args.action == "replay" and not args.all and not args.event_ids:
        parser.error("name event ids to replay, or pass --all")
    try:
        if args.action == "list":
            return asyncio.run(_list(args.bootstrap, args.consumer))
        return asyncio.run(_replay(args.bootstrap, args.consumer, args.event_ids, args.all))
    except KafkaUnavailableError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
