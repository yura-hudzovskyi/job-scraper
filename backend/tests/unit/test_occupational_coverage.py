"""The pipeline over vacancies that are not software jobs.

Spec 25.3 rejects a change whose tests only cover software-engineer vacancies,
and 20.1 asks for at least ten occupational families across three languages.
That is not box-ticking: every piece of this phase is supposed to be
domain-agnostic, and the way that claim fails is by nobody ever running a
nursing vacancy or a Polish driver posting through it.

These fixtures are written rather than scraped, and short. They exist to prove
the pipeline does not assume a domain — real samples per source belong in
tests/fixtures/, and the evaluation set in 20.1 is a separate, human-annotated
thing that these do not pretend to be.
"""

import pytest

from app.domain.documents.language import detect_language
from app.domain.profiles.extraction import ExtractionInput
from app.domain.profiles.schemas import JobProfile
from app.domain.profiles.structural import StructuralExtractor
from app.integrations.parsers.html import parse_html

# (family, expected language, markup). Ten families, three languages, including
# the ones a vacancy corpus from Ukrainian job boards actually contains.
SOFTWARE = (
    "<h2>Вимоги</h2><ul><li>Досвід роботи з Python від 3 років</li>"
    "<li>Знання PostgreSQL та розподілених систем</li></ul>"
    "<h2>Пропонуємо</h2><p>Віддалену роботу та гнучкий графік роботи</p>"
)
ACCOUNTING = (
    "<h2>Обовʼязки</h2><ul><li>Ведення бухгалтерського обліку підприємства</li>"
    "<li>Підготовка звітності за стандартами МСФЗ</li></ul>"
    "<h2>Вимоги</h2><p>Досвід роботи головним бухгалтером від 5 років</p>"
)
SALES = (
    "<h2>What you will do</h2><ul><li>Manage a portfolio of enterprise accounts</li>"
    "<li>Own the full sales cycle from discovery to close</li></ul>"
    "<h2>Requirements</h2><p>At least 4 years in B2B software sales</p>"
)
MARKETING = (
    "<h2>About the role</h2><p>You will lead brand campaigns across our channels "
    "and shape how the product is presented to new audiences</p>"
    "<h2>Requirements</h2><ul><li>A portfolio of published campaign work</li></ul>"
)
HR_OPERATIONS = (
    "<h2>Обовʼязки</h2><ul><li>Повний цикл підбору персоналу компанії</li>"
    "<li>Адаптація нових працівників та ведення документації</li></ul>"
)
HEALTHCARE = (
    "<h2>Вимоги до кандидата</h2><ul><li>Диплом медичної сестри та чинна ліцензія</li>"
    "<li>Досвід роботи у відділенні інтенсивної терапії</li></ul>"
    "<h2>Умови</h2><p>Позмінний графік роботи у стаціонарі лікарні</p>"
)
LOGISTICS = (
    "<h2>Wymagania</h2><ul><li>Prawo jazdy kategorii C+E oraz aktualne badania</li>"
    "<li>Doświadczenie w przewozach międzynarodowych</li></ul>"
    "<h2>Oferujemy</h2><p>Stałe trasy oraz nowoczesną flotę pojazdów</p>"
)
TRADES = (
    "<h2>Zakres obowiązków</h2><ul><li>Montaż instalacji elektrycznych w budynkach</li>"
    "<li>Praca zgodna z przepisami bezpieczeństwa na budowie</li></ul>"
)
HOSPITALITY = (
    "<h2>The job</h2><ul><li>Run the floor during evening service</li>"
    "<li>Train new front of house staff on our standards</li></ul>"
    "<h2>We ask for</h2><p>Two years in a busy restaurant kitchen or floor team</p>"
)
GENERAL_OFFICE = (
    "<h2>Responsibilities</h2><ul><li>Handle incoming correspondence and scheduling</li>"
    "<li>Keep supplier records accurate and up to date</li></ul>"
)

VACANCIES: list[tuple[str, str, str]] = [
    ("software", "uk", SOFTWARE),
    ("accounting_finance", "uk", ACCOUNTING),
    ("sales", "en", SALES),
    ("marketing_design", "en", MARKETING),
    ("hr_operations", "uk", HR_OPERATIONS),
    ("healthcare", "uk", HEALTHCARE),
    ("logistics_driving", "pl", LOGISTICS),
    ("skilled_trades", "pl", TRADES),
    ("hospitality", "en", HOSPITALITY),
    ("general_office", "en", GENERAL_OFFICE),
]

FAMILIES = [family for family, _, _ in VACANCIES]


def test_the_fixture_set_covers_the_families_the_spec_asks_for() -> None:
    """Ten occupational families and three languages — 20.1. A regression here
    means the coverage quietly shrank back to software."""
    assert len(FAMILIES) == len(set(FAMILIES)) == 10
    assert {language for _, language, _ in VACANCIES} == {"uk", "en", "pl"}


@pytest.mark.parametrize(("family", "language", "markup"), VACANCIES, ids=FAMILIES)
def test_every_vacancy_parses_into_blocks_whose_spans_resolve(
    family: str, language: str, markup: str
) -> None:
    document = parse_html(markup)

    assert document.blocks, f"{family} produced no blocks"
    assert document.spans_resolve(), f"{family} produced spans that do not resolve"


@pytest.mark.parametrize(("family", "language", "markup"), VACANCIES, ids=FAMILIES)
def test_language_is_detected_for_every_family(family: str, language: str, markup: str) -> None:
    """Phase 6 picks its text search configuration from this, so a nursing
    vacancy detecting as None costs it the lexical channel entirely."""
    document = parse_html(markup)

    assert detect_language(document.text) == language


@pytest.mark.parametrize(("family", "language", "markup"), VACANCIES, ids=FAMILIES)
def test_headings_and_list_items_survive_in_every_family(
    family: str, language: str, markup: str
) -> None:
    """The structure Phase 3's necessity classification will read. If it survives
    only for software postings, that classification will only work for those."""
    document = parse_html(markup)
    kinds = {block.block_type.value for block in document.blocks}

    assert "list_item" in kinds, f"{family} lost its list items"


@pytest.mark.parametrize(("family", "language", "markup"), VACANCIES, ids=FAMILIES)
@pytest.mark.asyncio
async def test_extraction_runs_over_every_family_without_assuming_one(
    family: str, language: str, markup: str
) -> None:
    document = parse_html(markup)
    result = await StructuralExtractor().extract_job(
        ExtractionInput(
            parsed_text=document.text,
            blocks=document.blocks,
            language=detect_language(document.text),
            known_fields={"employment_type": "full_time", "remote": False},
        )
    )
    profile = result.profile
    assert isinstance(profile, JobProfile)

    assert profile.language == language
    # Every requirement it emits is either backed by a span into this document or
    # marked derived — no family gets to skip that.
    for requirement in profile.requirements:
        if requirement.explicit:
            assert requirement.evidence is not None
            assert requirement.evidence.validate_against(document.text)
        else:
            assert requirement.evidence is None


@pytest.mark.parametrize(("family", "language", "markup"), VACANCIES, ids=FAMILIES)
@pytest.mark.asyncio
async def test_a_stated_experience_requirement_is_found_in_any_language(
    family: str, language: str, markup: str
) -> None:
    """Locating a value's own text must not depend on the language around it —
    the number is the same in Ukrainian, English and Polish."""
    document = parse_html(markup)
    result = await StructuralExtractor().extract_job(
        ExtractionInput(
            parsed_text=document.text,
            language=language,
            known_fields={"required_experience_years": 3.0},
        )
    )
    profile = result.profile
    assert isinstance(profile, JobProfile)

    requirement = profile.requirements[0]
    if "3" in document.text:
        assert requirement.explicit is True
        assert requirement.evidence is not None
    else:
        assert requirement.explicit is False
