"""What the sampler refuses to do, and why each refusal is a measurement choice.

Spec 20.1 budgets a minute per judgement, so a seed tier is five hours of one
person's reading. Every property here is about not spending those five hours
somewhere that cannot answer the question the set exists for.
"""

from app.domain.evaluation.sampling import (
    Candidate,
    band_of,
    coverage,
    stratified_sample,
    stratum_of,
)


def _pool(*specs: tuple[float, str, int]) -> list[Candidate]:
    """(score, language, how many) triples, expanded into distinct candidates."""
    pool: list[Candidate] = []
    for score, language, count in specs:
        for index in range(count):
            pool.append(
                Candidate(
                    canonical_job_id=f"{language}-{score}-{index:04d}",
                    job_revision_id=f"rev-{language}-{score}-{index:04d}",
                    score=score,
                    language=language,
                )
            )
    return pool


# --- bands -------------------------------------------------------------------


def test_scores_fall_into_ten_point_bands() -> None:
    assert band_of(0.0) == 0
    assert band_of(54.9) == 50
    assert band_of(60.0) == 60


def test_a_perfect_score_stays_in_the_top_band_not_past_it() -> None:
    """100 // 10 * 10 is 100, which would be a band with nothing else in it."""
    assert band_of(100.0) == 90


def test_a_missing_language_is_a_stratum_rather_than_a_crash() -> None:
    unknown = Candidate("job", None, 55.0, None)

    assert stratum_of(unknown) == (50, "unknown")


# --- the sample --------------------------------------------------------------


def test_a_thin_band_is_not_drowned_by_a_fat_one() -> None:
    """The production shape: 1 173 matches in the 50s against 11 in the 80s.
    Proportional sampling would spend the whole budget in one band and learn
    nothing about where the ranker is confident."""
    pool = _pool((55.0, "en", 1173), (85.0, "en", 11))

    sample = stratified_sample(pool, 40)

    top_band = [c for c in sample if band_of(c.score) == 80]
    assert len(top_band) == 11  # everything the thin band had
    assert len(sample) == 40


def test_both_languages_are_sampled_even_when_one_is_rare() -> None:
    """36% of the corpus is Ukrainian and the extractor behaves differently on
    it (17.6). A set that is 95% English reports an English average."""
    pool = _pool((55.0, "en", 500), (55.0, "uk", 20))

    sample = stratified_sample(pool, 40)

    assert sum(1 for c in sample if c.language == "uk") == 20


def test_a_sample_larger_than_the_pool_returns_the_pool() -> None:
    pool = _pool((55.0, "en", 3), (75.0, "uk", 2))

    assert len(stratified_sample(pool, 300)) == 5


def test_sampling_the_same_pool_twice_gives_the_same_pairs() -> None:
    """A set that changes when rebuilt cannot be compared with itself across
    model versions, which is the only thing an evaluation set is for."""
    pool = _pool((55.0, "en", 50), (75.0, "uk", 50))

    first = stratified_sample(pool, 30)
    second = stratified_sample(list(reversed(pool)), 30)

    assert [c.canonical_job_id for c in first] == [c.canonical_job_id for c in second]


def test_asking_for_nothing_takes_nothing() -> None:
    assert stratified_sample(_pool((55.0, "en", 10)), 0) == []


def test_an_empty_pool_is_not_an_error() -> None:
    assert stratified_sample([], 300) == []


# --- what the sample says about itself ---------------------------------------


def test_coverage_reports_what_the_set_actually_spans() -> None:
    """ "300 judged pairs" sounds like more than it is until the bands are next
    to it (spec 20.1 asks for languages and families explicitly)."""
    sample = _pool((55.0, "en", 3), (85.0, "uk", 2))

    assert coverage(sample) == {
        "score_bands": {"50-60": 3, "80-90": 2},
        "languages": {"en": 3, "uk": 2},
    }
