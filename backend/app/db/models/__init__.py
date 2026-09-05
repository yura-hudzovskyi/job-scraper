"""Importing this package registers every ORM model on Base.metadata — required
before Alembic autogenerate or Base.metadata.create_all() will see them."""

from app.db.models.candidate import CvDocumentModel, UserPreferenceModel
from app.db.models.document import (
    DocumentBlockModel,
    DocumentRevisionModel,
    DocumentRevisionTransitionModel,
)
from app.db.models.embedding import DocumentEmbeddingModel
from app.db.models.evaluation import EvaluationPairModel
from app.db.models.job import (
    CanonicalJobModel,
    JobSourceRecordModel,
    RawJobModel,
    ScrapeRunModel,
)
from app.db.models.match import JobMatchModel
from app.db.models.notification import (
    NotificationDeliveryModel,
    NotificationModel,
    NotificationSettingsModel,
    TelegramIntegrationModel,
)
from app.db.models.outbox import OutboxEventModel
from app.db.models.pipeline import PipelineRunModel
from app.db.models.profile import ProfileRevisionModel
from app.db.models.registry import ModelRegistryModel
from app.db.models.settings import PipelineConfigModel
from app.db.models.taxonomy import (
    ProfileConceptMentionModel,
    TaxonomyConceptModel,
    TaxonomyRelationModel,
    TaxonomyVersionModel,
    UnmappedMentionModel,
)
from app.db.models.user import UserModel

__all__ = [
    "CanonicalJobModel",
    "CvDocumentModel",
    "DocumentBlockModel",
    "DocumentEmbeddingModel",
    "DocumentRevisionModel",
    "DocumentRevisionTransitionModel",
    "EvaluationPairModel",
    "JobMatchModel",
    "JobSourceRecordModel",
    "ModelRegistryModel",
    "NotificationDeliveryModel",
    "NotificationModel",
    "NotificationSettingsModel",
    "OutboxEventModel",
    "PipelineConfigModel",
    "PipelineRunModel",
    "ProfileConceptMentionModel",
    "ProfileRevisionModel",
    "RawJobModel",
    "ScrapeRunModel",
    "TaxonomyConceptModel",
    "TaxonomyRelationModel",
    "TaxonomyVersionModel",
    "TelegramIntegrationModel",
    "UnmappedMentionModel",
    "UserModel",
    "UserPreferenceModel",
]
