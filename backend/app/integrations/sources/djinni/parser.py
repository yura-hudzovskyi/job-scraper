"""Parses Djinni's public search-result and vacancy pages with httpx + BeautifulSoup.

Playwright is a fallback only, for data genuinely unreachable without JS execution.
This adapter must fail gracefully (mark itself degraded) if the source blocks or
disallows scraping — never escalate into anti-bot circumvention. See
docs/source-adapters.md. Tested against fixtures in tests/fixtures/djinni/.
"""


def parse_search_results(html: str) -> list[dict]:
    """Return a list of {external_id, url, title} discovered in a search-results page."""
    raise NotImplementedError


def parse_vacancy_page(html: str) -> dict:
    """Extract the full job payload from a vacancy page."""
    raise NotImplementedError
