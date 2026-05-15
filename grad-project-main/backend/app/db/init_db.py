from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.db.base import Base
from app.db.models import RegistrationSetting, User
from app.db.session import SessionLocal, engine


def _get_or_create_user(
    db: Session, *, username: str, password: str, role: str, full_name: str
) -> User:
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        user = User(
            username=username,
            password_hash=get_password_hash(password),
            full_name=full_name,
            role=role,
            is_active=True,
        )
        db.add(user)
        db.flush()
    return user


def init_db() -> None:
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        admin = _get_or_create_user(
            db,
            username="admin",
            password="Admin123!",
            role="ADMIN",
            full_name="System Administrator",
        )

        registration_setting = db.scalar(select(RegistrationSetting).limit(1))
        if registration_setting is None:
            db.add(
                RegistrationSetting(
                    is_registration_open=False,
                    updated_by_user_id=admin.id,
                )
            )

        db.commit()
