"""Choosing which candidate-vacancy pairs are worth a person's hour.

Spec 20.1 budgets roughly a minute of careful reading per judgement, so a seed
tier of 300 pairs is five hours of somebody's attention. What that attention is
spent on decides what the metrics can see, which makes sampling a measurement
decision rather than a convenience.

Two rules, and both are about what a naive sample would hide.

Take from every score band, not from the top. A sample drawn from what the
ranker already liked can only ever confirm that its favourites are good; it says
nothing about the relevant vacancy sitting at rank 400, and Recall@100 (20.4) is
precisely a question about those. The production distribution makes this
concrete: 1 173 of this user's matches score in the 50s and 11 in the 80s, so
proportional sampling would spend the whole budget in one band.

Take from every language. 36% of the corpus is Ukrainian and the extractor
behaves measurably differently on it (17.6), so a set that is 95% English would
report an average that is really an English average.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# Ten-point bands. Fine enough to separate "the ranker was confident" from "the
# ranker was guessing", coarse enough that a band is not one vacancy.
BAND_WIDTH = 10.0


@dataclass(frozen=True)
class Candidate:
    """One pair the sampler may choose, with what it needs to stratify by."""

    canonical_job_id: str
    job_revision_id: str | None
    score: float
    language: str | None


def band_of(score: float) -> int:
    """Which score band a pair falls in, as the band's lower bound."""
    return int(max(0.0, min(score, 99.999)) // BAND_WIDTH * BAND_WIDTH)


def stratum_of(candidate: Candidate) -> tuple[int, str]:
    return band_of(candidate.score), candidate.language or "unknown"


def stratified_sample(candidates: Sequence[Candidate], size: int) -> list[Candidate]:
    """Spread `size` picks as evenly as the strata allow, best-scoring first inside each.

    Round-robin rather than proportional allocation, for the reason above: the
    bands are wildly uneven and proportional sampling is how a set ends up
    unable to answer the question it was built for. A stratum that runs out
    simply stops contributing, so a thin band donates everything it has and the
    remaining budget goes to bands that still have pairs.

    Deterministic: same input, same sample. An evaluation set that changes when
    it is rebuilt cannot be compared with itself across model versions.
    """
    if size <= 0:
        return []

    strata: dict[tuple[int, str], list[Candidate]] = {}
    for candidate in candidates:
        strata.setdefault(stratum_of(candidate), []).append(candidate)
    for pairs in strata.values():
        # Highest score first inside a band, then by id so ties do not depend on
        # the order the database happened to return rows in.
        pairs.sort(key=lambda c: (-c.score, c.canonical_job_id))

    order = sorted(strata)
    chosen: list[Candidate] = []
    depth = 0
    while len(chosen) < size:
        took_any = False
        for stratum in order:
            pairs = strata[stratum]
            if depth >= len(pairs):
                continue
            chosen.append(pairs[depth])
            took_any = True
            if len(chosen) >= size:
                break
        if not took_any:
            break
        depth += 1
    return chosen


def coverage(candidates: Iterable[Candidate]) -> dict[str, dict[str, int]]:
    """What a sample actually spans, for the report that goes with it.

    A sample is only as good as its coverage, and 20.1 asks for languages and
    occupational families explicitly. Reporting it next to the set is what stops
    "300 judged pairs" from sounding like more than it is.
    """
    bands: dict[str, int] = {}
    languages: dict[str, int] = {}
    for candidate in candidates:
        band = f"{band_of(candidate.score):.0f}-{band_of(candidate.score) + BAND_WIDTH:.0f}"
        bands[band] = bands.get(band, 0) + 1
        language = candidate.language or "unknown"
        languages[language] = languages.get(language, 0) + 1
    return {"score_bands": bands, "languages": languages}
