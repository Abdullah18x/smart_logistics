from typing import Annotated

from fastapi import Depends

from sl_platform.auth import Auth, Principal, TokenVerifier
from sl_platform.roles import UserRole
from tracking.config import settings
from tracking.services.journeys import JourneyService
from tracking.store import TrackingStore

store = TrackingStore(settings)
journeys = JourneyService(store, max_clock_skew_seconds=settings.max_clock_skew_seconds)
verifier = TokenVerifier.from_settings(settings)
auth = Auth(verifier)


def get_journeys() -> JourneyService:
    return journeys


JourneysDep = Annotated[JourneyService, Depends(get_journeys)]
AnyPrincipal = Annotated[Principal, Depends(auth)]
#: Journeys are opened and cancelled by the dispatch workflow.
WorkflowPrincipal = Annotated[Principal, Depends(auth.require(UserRole.SYSTEM, UserRole.ADMIN))]
CourierPrincipal = Annotated[Principal, Depends(auth.require(UserRole.COURIER))]
