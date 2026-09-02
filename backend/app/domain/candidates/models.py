"""What the candidate is, and what the candidate wants.

`CvDocument` is the CV as uploaded, with its text extracted — that text is the
whole candidate side of matching: it gets embedded and handed to the reranker
verbatim, with nothing derived from it in between.

`UserPreference` is what the candidate wants, edited directly in the UI. It never
feeds the models; it drives the hard filters (app/domain/matching/filters.py) and
one short "what I'm looking for" line in the document. Never merge the two — see
docs/domain-model.md.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class CvDocument:
    id: str
    user_id: str
    filename: str
    raw_text: str
    uploaded_at: datetime


@dataclass(frozen=True)
class UserPreference:
    user_id: str
    desired_salary_usd: int | None = None
    preferred_roles: list[str] = field(default_factory=list)
    preferred_stack: list[str] = field(default_factory=list)
    blocked_stack: list[str] = field(default_factory=list)
    work_formats: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    max_required_experience: float | None = None
    companies_blacklist: list[str] = field(default_factory=list)
