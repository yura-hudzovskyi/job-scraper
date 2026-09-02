"""The role categories a vacancy and a candidate can be classified into — see
docs/ai-pipeline-v3.md (B2).

Deliberately not the source sites' own category lists
(app/integrations/sources/categories.py): those are scrape-time search keywords,
one per site, tuned to each site's filter sidebar. This is the app's own small,
stable vocabulary for "what kind of role is this", extracted once per job as part
of the call that already reads the posting.

A category is a *ranking signal* first and a filter only in the clearest cases:
a classifier that is merely likely-right must never remove a good vacancy on its
own, and many real vacancies are genuinely cross-functional. The gate below
therefore has three outcomes rather than two, and only rules a job out when both
sides are confidently classified into families that share nothing.
"""

import re
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


# Two families, plus "other" which never rules anything out. Inside a family,
# categories are adjacent enough that a mismatch is a ranking signal — a backend
# engineer taking a QA or data role is a career move, not an error. Across
# families it is a different profession.
_ENGINEERING = frozenset(
    {
        JobCategory.BACKEND,
        JobCategory.FRONTEND,
        JobCategory.FULL_STACK,
        JobCategory.MOBILE,
        JobCategory.QA,
        JobCategory.DEVOPS,
        JobCategory.DATA,
        JobCategory.ML_AI,
        JobCategory.SECURITY,
        JobCategory.EMBEDDED,
        JobCategory.GAMEDEV,
    }
)
_NON_ENGINEERING = frozenset(
    {
        JobCategory.DESIGN,
        JobCategory.PRODUCT,
        JobCategory.PROJECT_MANAGEMENT,
        JobCategory.SUPPORT,
        JobCategory.MARKETING,
        JobCategory.SALES,
        JobCategory.HR,
        JobCategory.FINANCE,
    }
)

# Only a confident classification may rule anything out; below this a category is
# a hint that colours ranking and nothing more.
HIGH_CONFIDENCE = 0.7

# Enough to recognise how people actually title themselves. Anything unmatched
# stays unclassified, which the gate treats as "don't know" rather than "no".
_ROLE_PATTERNS: tuple[tuple[str, JobCategory], ...] = (
    (r"full[\s-]?stack", JobCategory.FULL_STACK),
    (r"front[\s-]?end|react|angular|vue", JobCategory.FRONTEND),
    (r"back[\s-]?end|python|java\b|golang|\.net|php|ruby|node", JobCategory.BACKEND),
    (r"android|ios\b|mobile|flutter|react native", JobCategory.MOBILE),
    (r"\bqa\b|test(er|ing)?|quality assurance|automation engineer", JobCategory.QA),
    (r"devops|sre\b|infrastructure|platform engineer|cloud engineer", JobCategory.DEVOPS),
    (r"data (engineer|analyst|scientist)|analytics|bi\b", JobCategory.DATA),
    (r"machine learning|\bml\b|\bai\b|nlp|computer vision", JobCategory.ML_AI),
    (r"security|appsec|pentest", JobCategory.SECURITY),
    (r"embedded|firmware", JobCategory.EMBEDDED),
    (r"game(dev)?|unity|unreal", JobCategory.GAMEDEV),
    (r"design(er)?|ux|ui\b", JobCategory.DESIGN),
    (r"product manager|product owner", JobCategory.PRODUCT),
    (r"project manager|delivery manager|scrum master", JobCategory.PROJECT_MANAGEMENT),
    (r"support|help ?desk", JobCategory.SUPPORT),
    (r"marketing|seo\b|content", JobCategory.MARKETING),
    (r"sales|account manager|business development", JobCategory.SALES),
    (r"\bhr\b|recruit", JobCategory.HR),
    (r"finance|accountant|bookkeep", JobCategory.FINANCE),
)


class CategoryDecision(StrEnum):
    """What a category comparison is allowed to conclude — see
    docs/ai-pipeline-v3.md (B2)."""

    PASS = "pass"
    # Discoverable, ranked lower. Wrong-but-adjacent classifications land here.
    SOFT_MISMATCH = "soft_mismatch"
    # A different profession, confidently on both sides.
    HARD_MISMATCH = "hard_mismatch"


def family(category: JobCategory) -> frozenset[JobCategory] | None:
    if category in _ENGINEERING:
        return _ENGINEERING
    if category in _NON_ENGINEERING:
        return _NON_ENGINEERING
    return None


def category_from_role(role: str) -> JobCategory | None:
    """A candidate's own words ("Senior Backend Engineer", "QA Automation") mapped
    onto the same vocabulary jobs are classified into. Rules rather than a model:
    this reads titles the user typed or a CV stated, where the signal is a handful
    of well-known words."""
    lowered = role.lower()
    for pattern, category in _ROLE_PATTERNS:
        if re.search(pattern, lowered):
            return category
    return None


def candidate_categories(roles: list[str]) -> set[JobCategory]:
    return {
        category for category in (category_from_role(role) for role in roles) if category is not None
    }


def decide(
    job_category: JobCategory | None,
    job_confidence: float | None,
    candidate: set[JobCategory],
) -> CategoryDecision:
    """Compare one vacancy's category against what the candidate is after.

    Missing or low-confidence information always passes: an unclassified job is
    an unknown, and unknowns are not rejections (docs/ai-pipeline-v3.md, B1).
    """
    if job_category is None or not candidate:
        return CategoryDecision.PASS
    if job_category in candidate:
        return CategoryDecision.PASS

    job_family = family(job_category)
    candidate_families = {family(category) for category in candidate}
    if job_family is None or job_family in candidate_families:
        # Adjacent enough to be a career move rather than a mistake.
        return CategoryDecision.SOFT_MISMATCH

    if (job_confidence or 0.0) < HIGH_CONFIDENCE:
        # Different profession, but the classifier isn't sure — that is exactly
        # the case where a hard filter silently loses a good vacancy.
        return CategoryDecision.SOFT_MISMATCH
    return CategoryDecision.HARD_MISMATCH
