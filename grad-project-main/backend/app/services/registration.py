from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import RegistrationSetting


def get_or_create_registration_setting(db: Session) -> RegistrationSetting:
    setting = db.scalar(select(RegistrationSetting).limit(1))
    if setting is None:
        setting = RegistrationSetting(is_registration_open=False, updated_by_user_id=None)
        db.add(setting)
        db.flush()
    return setting


def get_registration_setting(db: Session) -> RegistrationSetting:
    return get_or_create_registration_setting(db)


def registration_is_open(db: Session) -> bool:
    setting = db.scalar(select(RegistrationSetting).limit(1))
    if setting is None:
        setting = RegistrationSetting(is_registration_open=False, updated_by_user_id=None)
        db.add(setting)
        db.commit()
        db.refresh(setting)
    return bool(setting.is_registration_open)


def set_registration_open(
    db: Session, is_open: bool, updated_by_user_id: int | None = None
) -> RegistrationSetting:
    setting = db.scalar(select(RegistrationSetting).limit(1))
    if setting is None:
        setting = RegistrationSetting(
            is_registration_open=is_open,
            updated_by_user_id=updated_by_user_id,
        )
        db.add(setting)
    else:
        setting.is_registration_open = is_open
        setting.updated_by_user_id = updated_by_user_id

    db.flush()
    return setting
