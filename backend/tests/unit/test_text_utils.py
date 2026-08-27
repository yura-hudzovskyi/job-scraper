from app.integrations.sources.text_utils import guess_seniority, html_to_text, parse_salary_range


def test_parses_en_dash_range() -> None:
    result = parse_salary_range("$2000–3000")
    assert result is not None
    assert (result.min, result.max, result.currency) == (2000, 3000, "USD")


def test_parses_hyphen_range() -> None:
    result = parse_salary_range("$800-1000")
    assert result is not None
    assert (result.min, result.max, result.currency) == (800, 1000, "USD")


def test_parses_up_to() -> None:
    result = parse_salary_range("до $1000")
    assert result is not None
    assert (result.min, result.max, result.currency) == (None, 1000, "USD")


def test_parses_from() -> None:
    result = parse_salary_range("від $1500")
    assert result is not None
    assert (result.min, result.max, result.currency) == (1500, None, "USD")


def test_parses_single_amount() -> None:
    result = parse_salary_range("$2500")
    assert result is not None
    assert (result.min, result.max, result.currency) == (2500, 2500, "USD")


def test_returns_none_for_undisclosed_salary() -> None:
    assert parse_salary_range(None) is None
    assert parse_salary_range("") is None
    assert parse_salary_range("Salary not disclosed") is None


def test_guess_seniority_from_title() -> None:
    assert guess_seniority("Senior Automation QA Engineer (Python)") == "senior"
    assert guess_seniority("Lead Python Developer") == "senior"
    assert guess_seniority("Junior Motion Designer/AI Creator") == "junior"
    assert guess_seniority("Hardware Engineer Intern") == "junior"
    assert guess_seniority("AI Engineer") is None


def test_html_to_text_strips_tags_and_keeps_breaks() -> None:
    html = "<p>Hello<br>World</p><ul><li>One</li><li>Two</li></ul>"
    assert html_to_text(html) == "Hello\nWorld\nOne\nTwo"


def test_html_to_text_handles_empty_input() -> None:
    assert html_to_text(None) == ""
    assert html_to_text("") == ""
