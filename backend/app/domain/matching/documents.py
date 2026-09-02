"""The exact text the models see — one string per vacancy, one per candidate.

Both sides are rendered the same way and both are capped, for the same two
reasons: an embedding of a 15,000-character page is mostly an embedding of the
company's benefits section, and a reranker charges by what it reads. Labels
("TITLE:", "SKILLS:") stay in the text because modern embedding and rerank models
use them, and because they keep the document readable when the UI shows it — this
is the one place where "what did the model actually see" has to be answerable.

Nothing here invents content: a field that is missing produces no line, rather
than a guess or a placeholder.
"""

import hashlib
import re

from app.domain.candidates.models import UserPreference
from app.domain.jobs.models import NormalizedJob

# Enough to carry the requirements and responsibilities of any real vacancy
# without paying for the marketing copy that follows them.
MAX_JOB_CHARS = 4000
# A CV is the more information-dense of the two and is read once per run, so it
# gets more room.
MAX_CV_CHARS = 8000


def text_hash(text: str) -> str:
    """Identity of a document's text. Short on purpose: it labels a version so
    re-embedding can be skipped, it is not a security boundary."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _collapse(text: str) -> str:
    """Blank-line runs and trailing spaces are pure token cost."""
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def _line(label: str, value: str) -> str:
    value = value.strip()
    return f"{label}: {value}" if value else ""


def _joined(values: list[str], separator: str = ", ") -> str:
    return separator.join(value.strip() for value in values if value and value.strip())


def job_document(job: NormalizedJob) -> str:
    """What the vacancy looks like to the models."""
    location = _joined([*job.location.countries, *job.location.cities])
    salary = ""
    if job.salary and (job.salary.min or job.salary.max):
        bounds = _joined([str(int(v)) for v in (job.salary.min, job.salary.max) if v], "-")
        salary = f"{bounds} {job.salary.currency or ''}".strip()

    lines = [
        _line("TITLE", job.title),
        _line("COMPANY", job.company),
        _line("SENIORITY", job.seniority or ""),
        _line(
            "EXPERIENCE REQUIRED",
            f"{job.required_experience_years:g}+ years" if job.required_experience_years else "",
        ),
        _line("WORK FORMAT", "remote" if job.location.remote else "on-site or hybrid"),
        _line("LOCATION", location),
        _line("COMPENSATION", salary),
        _line("DESCRIPTION", _collapse(job.description)[:MAX_JOB_CHARS]),
    ]
    return "\n".join(line for line in lines if line)


def profile_document(cv_text: str, preferences: UserPreference | None = None) -> str:
    """What the candidate looks like to the models: their CV, plus the short
    statement of what they're after. Preferences go in because "I want a backend
    role, remote" is genuinely part of the query — the parts of them that are
    *constraints* are enforced by the hard filters instead, and are left out here
    so the same fact isn't applied twice."""
    lines = []
    if preferences is not None:
        lines = [
            _line("LOOKING FOR", _joined(preferences.preferred_roles)),
            _line("PREFERRED STACK", _joined(preferences.preferred_stack)),
            _line("WORK FORMAT", _joined(preferences.work_formats)),
        ]
    lines.append(_line("CV", _collapse(cv_text)[:MAX_CV_CHARS]))
    return "\n".join(line for line in lines if line)


# Prepended to the CV when it is used as a rerank query. A reranker's answer
# depends on what it was asked, so this text is part of the pipeline's behaviour
# and belongs next to the documents it is asked about — not buried in a caller.
RERANK_INSTRUCTION = (
    "Rate how well this vacancy fits the candidate below. Weigh required skills, "
    "comparable responsibilities, seniority and years of experience. Penalise "
    "missing must-have requirements and a different profession. Do not reward "
    "keyword overlap on its own."
)


def rerank_query(profile_text: str) -> str:
    return f"{RERANK_INSTRUCTION}\n\n{profile_text}"
