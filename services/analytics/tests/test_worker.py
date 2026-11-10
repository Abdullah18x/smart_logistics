"""The worker runs the platform worker with the consumer and the dead-letter watcher."""

from celery.schedules import crontab

import analytics.worker as worker_module
from analytics.dead_letters import DeadLetterWatcher


def test_worker_main_runs_the_consumer_and_the_dead_letter_watcher(monkeypatch):
    calls = []

    async def fake_run_worker(settings, database, processors=(), extra=()):
        calls.append((settings, list(processors), list(extra)))

    monkeypatch.setattr(worker_module, "run_worker", fake_run_worker)
    worker_module.main()
    settings, processors, extra = calls[0]
    assert settings.service_name == "analytics"
    assert {t for p in processors for t in p.topics} == {"shipment.events", "courier.events"}
    assert [type(x) for x in extra] == [DeadLetterWatcher]


def test_the_rebuild_runs_nightly():
    from analytics.tasks import celery_app

    schedule = celery_app.conf.beat_schedule["nightly-recompute"]
    assert schedule["task"] == "analytics.recompute_recent"
    assert isinstance(schedule["schedule"], crontab)
    assert celery_app.conf.task_default_queue == "analytics"
