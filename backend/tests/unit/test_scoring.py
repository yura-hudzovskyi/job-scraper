from app.domain.matching.models import Recommendation
from app.domain.matching.scoring import combine, recommend


def test_a_job_the_reranker_never_saw_scores_on_similarity_alone() -> None:
    """Not being reranked must not look like a bad rerank result: the top-K cut
    is about cost, and penalising everything below it would make the score depend
    on how many vacancies happened to be scraped."""
    assert combine(similarity=0.62, relevance=None, rerank_weight=0.7) == 62.0


def test_a_reranked_job_blends_both_signals_by_the_configured_weight() -> None:
    # 0.4*(1-0.75) + 0.8*0.75 = 0.7
    assert combine(similarity=0.4, relevance=0.8, rerank_weight=0.75) == 70.0


def test_weight_zero_ignores_the_reranker_and_weight_one_ignores_similarity() -> None:
    assert combine(similarity=0.3, relevance=0.9, rerank_weight=0.0) == 30.0
    assert combine(similarity=0.3, relevance=0.9, rerank_weight=1.0) == 90.0


def test_out_of_range_provider_values_are_clamped_not_propagated() -> None:
    """A provider returning something outside 0-1 would otherwise produce a score
    above 100 or below 0, which every threshold downstream reads as nonsense."""
    assert combine(similarity=1.4, relevance=None, rerank_weight=0.7) == 100.0
    assert combine(similarity=-0.2, relevance=-3.0, rerank_weight=0.5) == 0.0


def test_recommendation_bands_are_inclusive_at_the_threshold() -> None:
    assert recommend(70.0, apply_threshold=70, consider_threshold=45) is Recommendation.APPLY
    assert recommend(69.9, apply_threshold=70, consider_threshold=45) is Recommendation.CONSIDER
    assert recommend(45.0, apply_threshold=70, consider_threshold=45) is Recommendation.CONSIDER
    assert recommend(44.9, apply_threshold=70, consider_threshold=45) is Recommendation.SKIP
