"""Vocabulary endpoint.

Exists so the frontend never hardcodes a status list. One source of truth for the
enums means adding a status is a backend change, not a backend change plus a
forgotten dropdown somewhere in React.
"""

from fastapi import APIRouter

from app.enums import (
    ACTIVE_STATUSES,
    MANUAL_EVENT_TYPES,
    STAGE_ORDER,
    STATUSES_REQUIRING_SUBMITTED_CV,
    TERMINAL_STATUSES,
    ApplicationChannel,
    ApplicationStatus,
    EventSource,
    EventType,
)

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/enums")
def get_enums() -> dict[str, object]:
    return {
        "statuses": [
            {
                "value": member.value,
                "is_terminal": member in TERMINAL_STATUSES,
                "is_active": member in ACTIVE_STATUSES,
                # null for terminal statuses: an ending is not a stage.
                "stage_order": STAGE_ORDER.get(member),
                # True means moving to (or creating in) this status needs a CV.
                "requires_submitted_cv": member in STATUSES_REQUIRING_SUBMITTED_CV,
            }
            for member in ApplicationStatus
        ],
        "event_types": [
            {
                "value": member.value,
                # False means only the service layer may produce it.
                "manually_addable": member in MANUAL_EVENT_TYPES,
            }
            for member in EventType
        ],
        "application_channels": [member.value for member in ApplicationChannel],
        "event_sources": [member.value for member in EventSource],
    }
