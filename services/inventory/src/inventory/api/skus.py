import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from inventory.api.deps import AdminPrincipal, AnyPrincipal, CatalogDep
from inventory.db import SessionDep
from inventory.schemas import SkuCreate, SkuRead, SkuUpdate
from sl_platform.schemas import Page, Problem

router = APIRouter(prefix="/api/v1/skus", tags=["Catalog"])


@router.get("", response_model=Page[SkuRead], summary="List SKUs")
async def list_skus(
    _principal: AnyPrincipal,
    catalog: CatalogDep,
    search: Annotated[str | None, Query(max_length=200)] = None,
    active_only: bool = True,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[SkuRead]:
    rows, total = await catalog.list_skus(
        search=search, active_only=active_only, limit=limit, offset=offset
    )
    return Page[SkuRead](
        items=[SkuRead.model_validate(s) for s in rows], total=total, limit=limit, offset=offset
    )


@router.get(
    "/{sku_id}", response_model=SkuRead, responses={404: {"model": Problem}}, summary="Fetch a SKU"
)
async def get_sku(sku_id: uuid.UUID, _principal: AnyPrincipal, catalog: CatalogDep) -> SkuRead:
    return SkuRead.model_validate(await catalog.get_sku(sku_id))


@router.post(
    "", response_model=SkuRead, status_code=status.HTTP_201_CREATED, summary="Create a SKU"
)
async def create_sku(
    payload: SkuCreate, _admin: AdminPrincipal, catalog: CatalogDep, session: SessionDep
) -> SkuRead:
    result = SkuRead.model_validate(await catalog.create_sku(payload))
    await session.commit()
    return result


@router.patch("/{sku_id}", response_model=SkuRead, summary="Update a SKU")
async def update_sku(
    sku_id: uuid.UUID,
    payload: SkuUpdate,
    _admin: AdminPrincipal,
    catalog: CatalogDep,
    session: SessionDep,
) -> SkuRead:
    result = SkuRead.model_validate(await catalog.update_sku(sku_id, payload))
    await session.commit()
    return result
