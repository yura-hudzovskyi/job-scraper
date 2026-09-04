"""Language detection over the four languages this corpus actually contains.

The cases that matter most are the ones where it must *decline*: a short string,
a stack list, a Cyrillic text with no distinguishing letters. A detector that
guesses on those puts a wrong `language_code` on the revision, and Phase 6 picks
its text search configuration from that column.
"""

from app.domain.documents.language import (
    MIN_LETTERS,
    detect_language,
    profile,
)

UKRAINIAN = (
    "Ми шукаємо досвідченого інженера, який має досвід роботи з розподіленими "
    "системами та вміє працювати в команді"
)
RUSSIAN = (
    "Мы ищем опытного инженера, который имеет опыт работы с распределёнными "
    "системами и умеет работать в команде"
)
ENGLISH = (
    "We are looking for an experienced engineer who has worked with distributed "
    "systems and enjoys working in a team"
)
POLISH = (
    "Poszukujemy doświadczonego inżyniera, który pracował z systemami "
    "rozproszonymi i lubi pracę w zespole"
)


def test_it_identifies_each_language_in_the_corpus() -> None:
    assert detect_language(UKRAINIAN) == "uk"
    assert detect_language(RUSSIAN) == "ru"
    assert detect_language(ENGLISH) == "en"
    assert detect_language(POLISH) == "pl"


def test_ukrainian_and_russian_are_told_apart_by_their_exclusive_letters() -> None:
    """The pair this has to get right: same script, and a wrong answer sends the
    document to the wrong text search configuration."""
    assert detect_language(UKRAINIAN) != detect_language(RUSSIAN)


def test_a_mixed_document_resolves_to_the_script_carrying_most_of_the_text() -> None:
    """A Ukrainian CV listing English framework names is Ukrainian. The English
    is not lost — it is still in the text and still indexed."""
    mixed = UKRAINIAN + " Python Django PostgreSQL Docker Kubernetes"

    assert detect_language(mixed) == "uk"


def test_an_english_vacancy_quoting_a_polish_city_is_still_english() -> None:
    """One diacritic must not flip the answer. Measured: a quoted Polish name is
    under 1% of the letters, real Polish is 4.6%."""
    assert detect_language(ENGLISH + " Our office is in Gdańsk.") == "en"


def test_a_ukrainian_vacancy_quoting_one_russian_letter_is_still_ukrainian() -> None:
    """Both marker sets clear nothing on their own here — Ukrainian is at 9.5%
    and the stray letter at 1%, so the noise floor decides it."""
    assert detect_language(UKRAINIAN + " ё") == "uk"


def test_it_declines_on_text_too_short_to_profile() -> None:
    assert detect_language("Python") is None
    assert detect_language("") is None
    assert detect_language("   \n  ") is None


def test_it_declines_on_a_bare_technology_list() -> None:
    """"Python 3.12, AWS, Docker" is not evidence of English — it is evidence of
    nothing, and claiming a language here would mislabel a large share of short
    documents."""
    assert detect_language("Python 3.12, AWS, Docker, k8s") is None


def test_it_declines_on_digits_and_punctuation_alone() -> None:
    assert detect_language("2024 -- 100% (50/50) $5,000 +++ ... 12345678901234567890") is None


def test_it_declines_on_cyrillic_with_no_distinguishing_letters() -> None:
    """Text using only letters common to both languages is genuinely ambiguous,
    and None is the honest answer rather than a coin flip."""
    ambiguous = "Компанія та команда працювала над проектом та отримала результат" * 2
    ambiguous = ambiguous.replace("і", "а").replace("ї", "а").replace("є", "а")

    assert detect_language(ambiguous) is None


def test_the_short_text_threshold_is_a_named_constant() -> None:
    just_under = "а" * (MIN_LETTERS - 1)
    assert detect_language(just_under) is None


def test_detection_is_deterministic() -> None:
    """A re-parse of the same document must not detect differently — the reason
    this is character profiling rather than a seeded statistical library."""
    assert [detect_language(UKRAINIAN) for _ in range(5)] == ["uk"] * 5


# --- the profile behind the answer -------------------------------------------


def test_the_profile_counts_scripts_and_markers() -> None:
    counts = profile(UKRAINIAN)

    assert counts.cyrillic > counts.latin
    assert counts.ukrainian_markers > 0
    assert counts.russian_markers == 0


def test_the_profile_ignores_digits_and_punctuation() -> None:
    counts = profile("abc 123 !!! def")

    assert counts.letters == 6
    assert counts.latin == 6


def test_emoji_are_not_letters() -> None:
    counts = profile("Remote 🌍 team 🚀")

    assert counts.letters == len("Remoteteam")
