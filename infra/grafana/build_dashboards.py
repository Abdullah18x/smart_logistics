"""Generate the Grafana dashboards in ``infra/grafana/dashboards``.

    uv run python infra/grafana/build_dashboards.py

The JSON is generated, not hand-edited: panels are described here in a few
lines each, so a reviewer reads queries rather than Grafana's layout JSON.
Grafana loads the files at start-up (provisioning) and shows them read-only.

* api-health        RED per service and route, against the NFR latency targets
* pipeline-health   outbox, consumers, end-to-end lag, dead letters, Celery, Temporal
* business-kpis     the nine analytics metrics, read from Analytics' database
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "dashboards"
PROM = {"type": "prometheus", "uid": "prometheus"}
PG = {"type": "grafana-postgresql-datasource", "uid": "analytics"}


class Board:
    def __init__(
        self, uid: str, title: str, description: str, *, variables=(), refresh="10s", since="now-1h"
    ):
        self.uid, self.title, self.description, self.since = uid, title, description, since
        self.variables, self.refresh = list(variables), refresh
        self.panels: list[dict] = []
        self.x = self.y = self.row_h = 0

    def _place(self, w: int, h: int) -> dict:
        if self.x + w > 24:
            self.x, self.y = 0, self.y + self.row_h
            self.row_h = 0
        pos = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.row_h = max(self.row_h, h)
        return pos

    def row(self, title: str) -> None:
        if self.x:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0
        self.panels.append(
            {
                "type": "row",
                "title": title,
                "collapsed": False,
                "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1},
                "id": len(self.panels) + 1,
            }
        )
        self.y += 1

    def panel(
        self,
        kind: str,
        title: str,
        targets: list[dict],
        *,
        w=12,
        h=8,
        unit=None,
        description="",
        thresholds=None,
        ds=PROM,
        options=None,
        overrides=None,
        decimals=None,
        stack=False,
    ):
        defaults: dict = {"unit": unit} if unit else {}
        if decimals is not None:
            defaults["decimals"] = decimals
        if thresholds:
            defaults["thresholds"] = {
                "mode": "absolute",
                "steps": [
                    {"color": "green", "value": None},
                    *({"color": c, "value": v} for v, c in thresholds),
                ],
            }
            if kind == "timeseries":
                defaults["custom"] = {"thresholdsStyle": {"mode": "line"}}
        if stack and kind == "timeseries":
            defaults.setdefault("custom", {})["stacking"] = {"mode": "normal"}
            defaults["custom"]["fillOpacity"] = 40
        for i, t in enumerate(targets):
            t.setdefault("refId", chr(65 + i))
            t["datasource"] = ds
        self.panels.append(
            {
                "id": len(self.panels) + 1,
                "type": kind,
                "title": title,
                "description": description,
                "datasource": ds,
                "targets": targets,
                "gridPos": self._place(w, h),
                "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
                "options": options or {},
            }
        )

    def json(self) -> dict:
        return {
            "uid": self.uid,
            "title": self.title,
            "description": self.description,
            "tags": ["smartlogistics"],
            "timezone": "utc",
            "schemaVersion": 39,
            "refresh": self.refresh,
            "time": {"from": self.since, "to": "now"},
            "templating": {"list": self.variables},
            "panels": self.panels,
            "editable": False,
            "graphTooltip": 1,
        }


def q(expr: str, legend: str = "") -> dict:
    return {"expr": expr, "legendFormat": legend}


def sql(raw: str, fmt: str = "table") -> dict:
    return {"rawSql": " ".join(raw.split()), "format": fmt, "rawQuery": True, "editorMode": "code"}


SERVICE_VAR = {
    "name": "service",
    "label": "Service",
    "type": "query",
    "datasource": PROM,
    "query": {"query": 'label_values(up{job="api"}, service)', "refId": "service"},
    "definition": 'label_values(up{job="api"}, service)',
    "includeAll": True,
    "multi": True,
    "current": {"text": "All", "value": "$__all"},
    "refresh": 2,
    "sort": 1,
}
S = 'service=~"$service"'


def api_health() -> Board:
    b = Board(
        "sl-api-health",
        "API health",
        "Rate, errors and duration per service and route. Latency targets (NFR-2): "
        "p95 < 150 ms for reads, < 300 ms for writes.",
        variables=[SERVICE_VAR],
    )
    b.row("Overview")
    b.panel(
        "stat",
        "Targets up",
        [q(f"sum(up{{{S}}})", "up"), q(f"count(up{{{S}}})", "total")],
        w=6,
        h=4,
        options={"reduceOptions": {"calcs": ["lastNotNull"]}},
    )
    b.panel(
        "stat",
        "Requests / s",
        [q(f"sum(rate(http_requests_total{{{S}}}[5m]))")],
        w=6,
        h=4,
        unit="reqps",
        decimals=1,
    )
    b.panel(
        "stat",
        "5xx ratio",
        [
            q(
                f'sum(rate(http_requests_total{{{S},status=~"5.."}}[5m])) '
                f"/ clamp_min(sum(rate(http_requests_total{{{S}}}[5m])), 1e-9)"
            )
        ],
        w=6,
        h=4,
        unit="percentunit",
        thresholds=[(0.01, "orange"), (0.05, "red")],
    )
    b.panel("stat", "In flight", [q(f"sum(http_requests_in_flight{{{S}}})")], w=6, h=4)
    b.row("Rate and errors")
    b.panel(
        "timeseries",
        "Requests / s by service",
        [q(f"sum by (service) (rate(http_requests_total{{{S}}}[5m]))", "{{service}}")],
        unit="reqps",
    )
    b.panel(
        "timeseries",
        "Errors / s by service and status",
        [
            q(
                f'sum by (service, status) (rate(http_requests_total{{{S},status=~"[45].."}}[5m]))',
                "{{service}} {{status}}",
            )
        ],
        unit="reqps",
    )
    b.row("Duration")
    for title, method, target in (
        ("Reads (GET) p95", '="GET"', 0.15),
        ("Writes p95", '!="GET"', 0.3),
    ):
        b.panel(
            "timeseries",
            title,
            [
                q(
                    "histogram_quantile(0.95, sum by (service, le) (rate("
                    f"http_request_duration_seconds_bucket{{{S},method{method}}}[5m])))",
                    "{{service}}",
                )
            ],
            unit="s",
            thresholds=[(target, "red")],
            description=f"Red line: the {int(target * 1000)} ms target.",
        )
    b.panel(
        "table",
        "Slowest routes (p95, last 15 min)",
        [
            {
                **q(
                    "topk(15, histogram_quantile(0.95, sum by (service, method, route, le) "
                    f"(rate(http_request_duration_seconds_bucket{{{S}}}[15m]))))"
                ),
                "format": "table",
                "instant": True,
            }
        ],
        w=24,
        h=9,
        unit="s",
        options={"sortBy": [{"displayName": "Value", "desc": True}]},
    )
    return b


def pipeline_health() -> Board:
    b = Board(
        "sl-pipeline-health",
        "Event pipeline",
        "From a committed change to every consumer's effect: outbox → Kafka → consumers, "
        "dead letters, Celery jobs and Temporal workflows.",
    )
    b.row("Outbox (producers)")
    b.panel(
        "timeseries",
        "Oldest unpublished event age",
        [q("max by (service) (outbox_oldest_pending_age_seconds)", "{{service}}")],
        unit="s",
        w=8,
        thresholds=[(60, "red")],
        description="Grows when a relay is stuck or Kafka is down.",
    )
    b.panel(
        "timeseries",
        "Pending events",
        [q("sum by (service) (outbox_pending_events)", "{{service}}")],
        w=8,
    )
    b.panel(
        "timeseries",
        "Published / s by topic",
        [
            q("sum by (topic) (rate(outbox_published_total[5m]))", "{{topic}}"),
            q("sum by (topic) (rate(outbox_publish_failures_total[5m]))", "{{topic}} failures"),
        ],
        w=8,
        unit="ops",
    )
    b.row("Consumers")
    b.panel(
        "timeseries",
        "Events / s by consumer and outcome",
        [
            q(
                "sum by (consumer, outcome) (rate(events_consumed_total[5m]))",
                "{{consumer}} {{outcome}}",
            )
        ],
        unit="ops",
        w=12,
    )
    b.panel(
        "timeseries",
        "End-to-end lag p95 (commit → consumer effect)",
        [
            q(
                "histogram_quantile(0.95, sum by (consumer, le) "
                "(rate(event_end_to_end_seconds_bucket[5m])))",
                "{{consumer}}",
            )
        ],
        unit="s",
        w=12,
        thresholds=[(5, "red")],
        description="Target: p95 under 5 s.",
    )
    b.panel(
        "timeseries",
        "Consumer lag (messages behind)",
        [q("sum by (consumer) (kafka_consumer_lag_messages)", "{{consumer}}")],
        w=12,
    )
    b.panel(
        "timeseries",
        "Handler duration p95",
        [
            q(
                "histogram_quantile(0.95, sum by (consumer, le) "
                "(rate(event_handle_duration_seconds_bucket[5m])))",
                "{{consumer}}",
            )
        ],
        unit="s",
        w=12,
    )
    b.row("Failed events")
    b.panel(
        "timeseries",
        "Dead letters (per 10 min)",
        [
            q(
                "sum by (consumer, reason) (increase(dead_letters_total[10m]))",
                "{{consumer}} {{reason}}",
            )
        ],
        w=12,
        stack=True,
        description="Inspect and replay with ./dev.sh dlq list | replay.",
    )
    b.panel(
        "timeseries",
        "Handled without a registry check (per 10 min)",
        [
            q(
                "sum by (consumer, reason) (increase(events_unvalidated_total[10m]))",
                "{{consumer}} {{reason}}",
            )
        ],
        w=12,
    )
    b.row("Background jobs and workflows")
    b.panel(
        "timeseries",
        "Celery tasks / min by outcome",
        [q("sum by (task, outcome) (rate(celery_tasks_total[5m])) * 60", "{{task}} {{outcome}}")],
        w=8,
    )
    b.panel(
        "timeseries",
        "Celery task duration p95",
        [
            q(
                "histogram_quantile(0.95, sum by (task, le) "
                "(rate(celery_task_duration_seconds_bucket[5m])))",
                "{{task}}",
            )
        ],
        unit="s",
        w=8,
    )
    b.panel(
        "timeseries",
        "Notifications / min",
        [
            q(
                "sum by (channel, outcome) (rate(notifications_total[5m])) * 60",
                "{{channel}} {{outcome}}",
            )
        ],
        w=8,
    )
    b.panel(
        "timeseries",
        "Temporal workflows / min",
        [
            q(
                'sum by (__name__) (rate({__name__=~"temporal_workflow_(completed|failed|'
                'canceled)(_total)?"}[5m])) * 60',
                "{{__name__}}",
            )
        ],
        w=12,
    )
    b.panel(
        "timeseries",
        "Temporal activity failures / min",
        [
            q(
                'sum by (activity_type) (rate({__name__=~"temporal_activity_execution_failed'
                '(_total)?"}[5m])) * 60',
                "{{activity_type}}",
            )
        ],
        w=12,
    )
    return b


RANGE = "day BETWEEN $__timeFrom()::date AND $__timeTo()::date"


def total(metric: str) -> str:
    return (
        f"SELECT COALESCE(SUM(value), 0) AS value FROM daily_rollups "
        f"WHERE scope = 'warehouse' AND metric = '{metric}' AND {RANGE}"
    )


def business_kpis() -> Board:
    b = Board(
        "sl-business-kpis",
        "Business KPIs",
        "The nine analytics metrics for the selected period, read from Analytics' "
        "daily rollups (UTC days). Same numbers as GET /api/v1/analytics/summary.",
        refresh="1m",
        since="now-30d",
    )
    b.row("Period totals")
    for title, metric in (
        ("Shipments created", "created"),
        ("Dispatched", "dispatched"),
        ("Delivered", "delivered"),
        ("Failed deliveries", "delivery_failed"),
        ("Returns", "returned"),
        ("Dispatch failures", "dispatch_failed"),
    ):
        b.panel("stat", title, [sql(total(metric))], ds=PG, w=4, h=4)
    b.panel(
        "stat",
        "Average delivery time",
        [
            sql(
                "SELECT SUM(value) FILTER (WHERE metric = 'delivery_seconds') "
                "/ NULLIF(SUM(value) FILTER (WHERE metric = 'delivered_timed'), 0) AS value "
                f"FROM daily_rollups WHERE scope = 'warehouse' AND {RANGE}"
            )
        ],
        ds=PG,
        w=6,
        h=4,
        unit="s",
    )
    b.panel(
        "stat",
        "Return rate (per dispatched)",
        [
            sql(
                "SELECT SUM(value) FILTER (WHERE metric = 'returned')::float "
                "/ NULLIF(SUM(value) FILTER (WHERE metric = 'dispatched'), 0) AS value "
                f"FROM daily_rollups WHERE scope = 'warehouse' AND {RANGE}"
            )
        ],
        ds=PG,
        w=6,
        h=4,
        unit="percentunit",
        decimals=1,
    )
    b.panel(
        "gauge",
        "Courier utilisation (now)",
        [
            sql(
                "SELECT SUM(active_assignments)::float "
                "/ NULLIF(SUM(max_active_shipments), 0) AS value "
                "FROM couriers WHERE status = 'active' AND NOT deleted"
            )
        ],
        ds=PG,
        w=6,
        h=4,
        unit="percentunit",
        thresholds=[(0.7, "orange"), (0.9, "red")],
    )
    b.panel(
        "stat",
        "Failed events (dead letters)",
        [
            sql(
                "SELECT COUNT(*) AS value FROM pipeline_issues WHERE kind = 'dead_letter' "
                "AND $__timeFilter(occurred_at)"
            )
        ],
        ds=PG,
        w=6,
        h=4,
        thresholds=[(1, "red")],
    )
    b.row("Over time")
    b.panel(
        "timeseries",
        "Shipments per day",
        [
            sql(
                "SELECT day::timestamptz AS time, metric, SUM(value) AS value FROM daily_rollups "
                "WHERE scope = 'warehouse' AND metric IN ('created', 'dispatched', 'delivered', "
                f"'delivery_failed', 'returned') AND {RANGE} GROUP BY 1, 2 ORDER BY 1",
                "time_series",
            )
        ],
        ds=PG,
        w=24,
        h=9,
        options={"legend": {"displayMode": "table", "placement": "right", "calcs": ["sum"]}},
    )
    b.row("Warehouses and couriers")
    b.panel(
        "table",
        "Warehouse throughput",
        [
            sql(
                "SELECT COALESCE(w.code, r.scope_id::text) AS warehouse, "
                "SUM(value) FILTER (WHERE metric = 'created') AS created, "
                "SUM(value) FILTER (WHERE metric = 'dispatched') AS dispatched, "
                "SUM(value) FILTER (WHERE metric = 'delivered') AS delivered, "
                "SUM(value) FILTER (WHERE metric = 'delivery_failed') AS failed, "
                "SUM(value) FILTER (WHERE metric = 'returned') AS returns, "
                "ROUND(SUM(value) FILTER (WHERE metric = 'delivery_seconds')::numeric / 3600 "
                "/ NULLIF(SUM(value) FILTER (WHERE metric = 'delivered_timed'), 0), 1) "
                "AS avg_hours "
                "FROM daily_rollups r LEFT JOIN (SELECT warehouse_id, MAX(warehouse_code) AS code "
                "FROM shipments GROUP BY 1) w ON w.warehouse_id = r.scope_id "
                f"WHERE scope = 'warehouse' AND {RANGE} "
                "GROUP BY 1 ORDER BY dispatched DESC NULLS LAST"
            )
        ],
        ds=PG,
        w=12,
        h=9,
    )
    b.panel(
        "table",
        "Couriers",
        [
            sql(
                "SELECT c.code, c.full_name, c.status, c.active_assignments AS active, "
                "c.max_active_shipments AS capacity, "
                "ROUND(c.active_assignments::numeric / NULLIF(c.max_active_shipments, 0), 2) "
                "AS utilisation, COALESCE(SUM(r.value) FILTER (WHERE r.metric = 'assigned'), 0) "
                "AS assigned, COALESCE(SUM(r.value) FILTER (WHERE r.metric = 'delivered'), 0) "
                "AS delivered FROM couriers c LEFT JOIN daily_rollups r ON r.scope = 'courier' "
                f"AND r.scope_id = c.id AND r.{RANGE} WHERE NOT c.deleted "
                "GROUP BY c.id ORDER BY assigned DESC, c.code"
            )
        ],
        ds=PG,
        w=12,
        h=9,
    )
    b.row("Pipeline issues")
    b.panel(
        "table",
        "Latest issues",
        [
            sql(
                "SELECT occurred_at AS time, kind, source, reason, event_type, status, detail "
                "FROM pipeline_issues WHERE $__timeFilter(occurred_at) "
                "ORDER BY occurred_at DESC LIMIT 100"
            )
        ],
        ds=PG,
        w=24,
        h=8,
    )
    return b


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, build in (
        ("api-health", api_health),
        ("pipeline-health", pipeline_health),
        ("business-kpis", business_kpis),
    ):
        board = build()
        (OUT / f"{name}.json").write_text(json.dumps(board.json(), indent=2) + "\n")
        print(f"wrote {OUT / f'{name}.json'} ({len(board.panels)} panels)")


if __name__ == "__main__":
    main()
