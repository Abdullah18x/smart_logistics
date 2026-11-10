"""The Kafka worker wires the platform worker with Notification's consumer."""

import notification.worker as worker_module


def test_worker_main_runs_the_platform_worker(monkeypatch):
    calls = []

    async def fake_run_worker(settings, database, processors=()):
        calls.append((settings, database, list(processors)))

    monkeypatch.setattr(worker_module, "run_worker", fake_run_worker)
    worker_module.main()
    settings, database, processors = calls[0]
    assert settings.service_name == "notification"
    assert database is worker_module.database
    assert {t for p in processors for t in p.topics} == {"shipment.events", "courier.events"}
