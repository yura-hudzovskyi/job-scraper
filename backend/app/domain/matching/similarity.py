"""Shared cosine similarity helper — used by both SemanticScorer (profile-vs-job text)
and SkillMatcher (skill-name-vs-skill-name), so there's one implementation.
"""


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = float(sum(x * y for x, y in zip(a, b, strict=True)))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(max(0.0, min(1.0, dot / (norm_a * norm_b))))
