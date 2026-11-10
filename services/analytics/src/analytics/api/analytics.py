import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query, status

from analytics import jobs
from analytics.api import deps
from analytics.api.deps import AdminPrincipal, ReaderPrincipal, ReportsDep, StaffPrincipal
from analytics.config import settings
from analytics.models import IssueKind, Metric
from analytics.schemas import (
    CourierStats,
    CourierUtilisation,
    FailedEvents,
    IssuePage,
    IssueRead,
    Period,
    Point,
    RecomputeAccepted,
    RecomputeRequest,
    Series,
    Summary,
    WarehouseThroughput,
)
from analytics.services.reports import Counters, Reports, Scoped
from sl_platform.auth import Principal
from sl_platform.errors import DomainError, PermissionDeniedError
from sl_platform.schemas import Problem

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])

MAX_DAYS = 366
DEFAULT_DAYS = 30

From = Annotated[
    date | None, Query(alias="from", description="First day (UTC), default 29 days ago.")
]
To = Annotated[date | None, Query(alias="to", description="Last day (UTC), default today.")]
WarehouseFilter = Annotated[uuid.UUID | None, Query(description="Only this warehouse.")]


class InvalidQueryError(DomainError):
    """The query asks for something the metrics cannot answer."""

    status_code = 422
    code = "invalid_query"


def period(start: date | None, end: date | None) -> Period:
    end = end or datetime.now(UTC).date()
    start = start or end - timedelta(days=DEFAULT_DAYS - 1)
    if start > end:
        raise InvalidQueryError("'from' must not be after 'to'.")
    if (end - start).days + 1 > MAX_DAYS:
        raise InvalidQueryError(f"A period covers at most {MAX_DAYS} days.")
    return Period(start=start, end=end)


def scope(principal: Principal, warehouse_id: uuid.UUID | None) -> Scoped:
    """The warehouses this request covers; None means all of them."""
    if warehouse_id is not None:
        if not principal.may_touch_warehouse(warehouse_id):
            raise PermissionDeniedError("That warehouse is outside your scope.")
        return [warehouse_id]
    return principal.scoped_warehouse_ids


def throughput(wid: uuid.UUID, code: str | None, c: Counters, days: int) -> WarehouseThroughput:
    return WarehouseThroughput(
        warehouse_id=wid,
        warehouse_code=code,
        created=c[Metric.CREATED],
        dispatched=c[Metric.DISPATCHED],
        delivered=c[Metric.DELIVERED],
        failed_deliveries=c[Metric.DELIVERY_FAILED],
        returns=c[Metric.RETURNED],
        dispatch_failures=c[Metric.DISPATCH_FAILED],
        average_delivery_hours=c.avg_delivery_hours,
        dispatched_per_day=round(c[Metric.DISPATCHED] / days, 2),
    )


async def warehouse_rows(
    reports: Reports, p: Period, warehouses: Scoped
) -> list[WarehouseThroughput]:
    counters = await reports.warehouse_counters(p.start, p.end, warehouses)
    codes = await reports.warehouse_codes(list(counters))
    days = (p.end - p.start).days + 1
    rows = [throughput(wid, codes.get(wid), c, days) for wid, c in counters.items()]
    return sorted(rows, key=lambda r: (-r.dispatched, r.warehouse_code or ""))


ERRORS = {403: {"model": Problem}, 422: {"model": Problem}}


@router.get("/summary", response_model=Summary, responses=ERRORS, summary="All nine metrics")
async def summary(
    principal: ReaderPrincipal,
    reports: ReportsDep,
    start: From = None,
    end: To = None,
    warehouse_id: WarehouseFilter = None,
) -> Summary:
    """Shipments created, dispatched, delivered, failed deliveries, average delivery
    time, courier utilisation, warehouse throughput, return rate and failed events —
    for a period of up to a year. Counts come from daily rollups that move as events
    arrive; utilisation is the courier fleet's load right now."""
    p = period(start, end)
    warehouses = scope(principal, warehouse_id)
    pipeline_wide = principal.sees_all_warehouses and warehouse_id is None

    async def compute() -> Summary:
        return await build_summary(reports, p, warehouses, pipeline_wide=pipeline_wide)

    if deps.cache is None:
        return await compute()
    scope_key = "all" if warehouses is None else ",".join(sorted(map(str, warehouses)))
    return await deps.cache.get_or_set(
        f"summary:{p.start}:{p.end}:{scope_key}:{int(pipeline_wide)}",
        settings.summary_cache_seconds,
        compute,
        dump=lambda s: s.model_dump(mode="json", by_alias=True),
        load=Summary.model_validate,
    )


async def build_summary(
    reports: Reports, p: Period, warehouses: Scoped, *, pipeline_wide: bool
) -> Summary:
    totals = await reports.totals(p.start, p.end, warehouses)
    load = await reports.utilisation(warehouses)
    failed_events = None
    if pipeline_wide:
        counts = await reports.issue_counts(p.start, p.end)
        failed_events = FailedEvents(
            dead_letters=counts[IssueKind.DEAD_LETTER.value],
            dispatch_failures=counts[IssueKind.DISPATCH_FAILED.value],
        )
    return Summary(
        period=p,
        total_shipments=totals[Metric.CREATED],
        dispatched=totals[Metric.DISPATCHED],
        delivered=totals[Metric.DELIVERED],
        failed_deliveries=totals[Metric.DELIVERY_FAILED],
        failed_delivery_rate=totals.failed_delivery_rate,
        average_delivery_hours=totals.avg_delivery_hours,
        returns=totals[Metric.RETURNED],
        return_rate=totals.return_rate,
        cancelled=totals[Metric.CANCELLED],
        courier_utilisation=CourierUtilisation(
            active_couriers=load.active_couriers,
            busy_couriers=load.busy_couriers,
            active_assignments=load.active_assignments,
            capacity=load.capacity,
            utilisation=load.ratio,
        ),
        warehouse_throughput=await warehouse_rows(reports, p, warehouses),
        failed_events=failed_events,
    )


SERIES_METRICS = [m for m in Metric if m not in (Metric.DELIVERY_SECONDS, Metric.DELIVERED_TIMED)]


@router.get("/timeseries", response_model=Series, responses=ERRORS, summary="One metric per day")
async def timeseries(
    principal: ReaderPrincipal,
    reports: ReportsDep,
    metric: Annotated[Metric, Query(description="e.g. dispatched, delivered, returned.")] = (
        Metric.DISPATCHED
    ),
    start: From = None,
    end: To = None,
    warehouse_id: WarehouseFilter = None,
) -> Series:
    """A count per day, zero-filled — e.g. shipments dispatched over time."""
    if metric not in SERIES_METRICS:
        raise InvalidQueryError("That counter is internal; /summary has the average.")
    p = period(start, end)
    points = await reports.series(metric, p.start, p.end, scope(principal, warehouse_id))
    return Series(
        metric=metric.value,
        period=p,
        points=[Point(day=d, value=v) for d, v in points],
        total=sum(v for _, v in points),
    )


@router.get(
    "/warehouses",
    response_model=list[WarehouseThroughput],
    responses=ERRORS,
    summary="Throughput per warehouse",
)
async def warehouses(
    principal: ReaderPrincipal, reports: ReportsDep, start: From = None, end: To = None
) -> list[WarehouseThroughput]:
    p = period(start, end)
    return await warehouse_rows(reports, p, scope(principal, None))


@router.get("/couriers", response_model=list[CourierStats], responses=ERRORS, summary="Per courier")
async def couriers(
    principal: ReaderPrincipal, reports: ReportsDep, start: From = None, end: To = None
) -> list[CourierStats]:
    """Assignments and outcomes in the period, plus each courier's load right now.
    Warehouse operators see the couriers based at their warehouses."""
    p = period(start, end)
    rows = await reports.courier_rows(p.start, p.end, scope(principal, None))
    out = []
    for courier, cid, c in rows:
        ratio = None
        if courier and courier.max_active_shipments:
            ratio = round(courier.active_assignments / courier.max_active_shipments, 4)
        out.append(
            CourierStats(
                courier_id=cid,
                code=courier.code if courier else None,
                full_name=courier.full_name if courier else None,
                status=courier.status if courier else None,
                assigned=c[Metric.ASSIGNED],
                delivered=c[Metric.DELIVERED],
                failed_deliveries=c[Metric.DELIVERY_FAILED],
                active_assignments=courier.active_assignments if courier else None,
                max_active_shipments=courier.max_active_shipments if courier else None,
                utilisation=ratio,
            )
        )
    return out


@router.get("/issues", response_model=IssuePage, responses=ERRORS, summary="Failed events")
async def issues(
    _staff: StaffPrincipal,
    reports: ReportsDep,
    start: From = None,
    end: To = None,
    kind: IssueKind | None = None,
    source: Annotated[
        str | None, Query(max_length=80, description="Consumer or warehouse code.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> IssuePage:
    """Dead letters (with the consumer, reason and error) and failed dispatches,
    newest first. Replay dead letters with ``./dev.sh dlq replay``; replayed ones
    show ``status: replayed``."""
    p = period(start, end)
    rows, total = await reports.issues(
        p.start, p.end, kind=kind, source=source, limit=limit, offset=offset
    )
    return IssuePage(
        items=[IssueRead.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
        counts=await reports.issue_counts(p.start, p.end),
    )


@router.post(
    "/recompute",
    response_model=RecomputeAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses=ERRORS,
    summary="Rebuild the rollups of a period",
)
async def recompute(_admin: AdminPrincipal, body: RecomputeRequest) -> RecomputeAccepted:
    """Queue a rebuild of the daily counters from the milestone facts — the same job
    that runs nightly for the last two days. Use it after replaying old events."""
    p = period(body.start, body.end)
    await jobs.Holder.queue_recompute(p.start, p.end)
    return RecomputeAccepted(period=p)
