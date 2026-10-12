"""The worker process starts and wires the outbox relay plus its consumers.

`python -m identity.worker` is a separate entry point from the API, so the HTTP
tests never exercise it; this catches an empty or mis-wired worker module.
"""

import identity.worker as worker_module


def test_worker_main_runs_the_platform_worker(monkeypatch):
    calls = []

    async def fake_run_worker(settings, database, processors=()):
        calls.append((settings, database, list(processors)))

    monkeypatch.setattr(worker_module, "run_worker", fake_run_worker)
    worker_module.main()

    assert len(calls) == 1
    settings, database, processors = calls[0]
    assert settings.service_name == "identity"
    assert database is worker_module.database
    assert len(processors) == 1
    topics = {topic for processor in processors for topic in processor.topics}
    assert topics == {"warehouse.events"}
