from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.audit import log_audit
from app.db.models import User
from app.db.session import get_db
from app.services.registration import get_or_create_registration_setting

router = APIRouter()


class RegistrationStatusResponse(BaseModel):
    is_registration_open: bool
    updated_at: datetime
    updated_by_user_id: int | None


def _update_registration_state(
    db: Session, *, is_open: bool, actor: User
) -> RegistrationStatusResponse:
    setting = get_or_create_registration_setting(db)
    previous_state = setting.is_registration_open

    setting.is_registration_open = is_open
    setting.updated_at = datetime.now(UTC)
    setting.updated_by_user_id = actor.id

    action = "REGISTRATION_OPENED" if is_open else "REGISTRATION_CLOSED"
    log_audit(
        db,
        actor_user_id=actor.id,
        action=action,
        entity_type="registration_window",
        entity_id=str(setting.id),
        before_data={"is_registration_open": previous_state},
        after_data={"is_registration_open": setting.is_registration_open},
    )
    db.commit()
    db.refresh(setting)

    return RegistrationStatusResponse(
        is_registration_open=setting.is_registration_open,
        updated_at=setting.updated_at,
        updated_by_user_id=setting.updated_by_user_id,
    )


@router.get("/status", response_model=RegistrationStatusResponse)
def get_registration_status(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> RegistrationStatusResponse:
    setting = get_or_create_registration_setting(db)
    db.commit()
    db.refresh(setting)
    return RegistrationStatusResponse(
        is_registration_open=setting.is_registration_open,
        updated_at=setting.updated_at,
        updated_by_user_id=setting.updated_by_user_id,
    )


@router.post("/open", response_model=RegistrationStatusResponse)
def open_registration(
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> RegistrationStatusResponse:
    return _update_registration_state(db, is_open=True, actor=current_user)


@router.post("/close", response_model=RegistrationStatusResponse)
def close_registration(
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> RegistrationStatusResponse:
    return _update_registration_state(db, is_open=False, actor=current_user)
