"""The role categories a vacancy and a candidate can be classified into — see
docs/ai-pipeline-v3.md (B2).

Deliberately not the source sites' own category lists
(app/integrations/sources/categories.py): those are scrape-time search keywords,
one per site, tuned to each site's filter sidebar. This is the app's own small,
stable vocabulary for "what kind of role is this", extracted once per job as part
of the call that already reads the posting.

A category is a *ranking signal*, not a filter, until phase 4 introduces the
confidence-aware gate: a classifier that is merely likely-right must never remove
a good vacancy on its own.
"""

from enum import StrEnum


class JobCategory(StrEnum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    FULL_STACK = "full_stack"
    MOBILE = "mobile"
    QA = "qa"
    DEVOPS = "devops"
    DATA = "data"
    ML_AI = "ml_ai"
    SECURITY = "security"
    EMBEDDED = "embedded"
    GAMEDEV = "gamedev"
    DESIGN = "design"
    PRODUCT = "product"
    PROJECT_MANAGEMENT = "project_management"
    SUPPORT = "support"
    MARKETING = "marketing"
    SALES = "sales"
    HR = "hr"
    FINANCE = "finance"
    OTHER = "other"
