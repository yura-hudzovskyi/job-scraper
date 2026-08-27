"""Importing this package registers every ORM model on Base.metadata — required
before Alembic autogenerate or Base.metadata.create_all() will see them."""

from app.db.models.application import ApplicationModel
from app.db.models.candidate import CvDocumentModel, UserPreferenceModel
from app.db.models.job import (
    CanonicalJobModel,
    JobSourceRecordModel,
    RawJobModel,
    ScrapeRunModel,
)
from app.db.models.match import JobMatchModel
from app.db.models.notification import NotificationDeliveryModel
from app.db.models.user import UserModel

__all__ = [
    "ApplicationModel",
    "CanonicalJobModel",
    "CvDocumentModel",
    "JobMatchModel",
    "JobSourceRecordModel",
    "NotificationDeliveryModel",
    "RawJobModel",
    "ScrapeRunModel",
    "UserModel",
    "UserPreferenceModel",
]
