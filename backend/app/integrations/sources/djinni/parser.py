"""Parses Djinni's public search-result and vacancy pages with httpx + BeautifulSoup.

Playwright is a fallback only, for data genuinely unreachable without JS execution.
This adapter must fail gracefully (mark itself degraded) if the source blocks or
disallows scraping — never escalate into anti-bot circumvention. See
docs/source-adapters.md. Tested against fixtures in tests/fixtures/djinni/.
"""

import re
from typing import Any

from bs4 import BeautifulSoup

_ITEM_ID_RE = re.compile(r"job-item-(\d+)")
_EXPERIENCE_RE = re.compile(r"\d+")

BASE_URL = "https://djinni.co"


def parse_search_results(html: str) -> list[dict[str, Any]]:
    """Return a list of {external_id, url, title} discovered in a search-results page."""
    soup = BeautifulSoup(html, "lxml")
    results = []
    for item in soup.select("div.job-item[id^='job-item-']"):
        id_match = _ITEM_ID_RE.search(str(item.get("id", "")))
        link = item.select_one("a.job_item__header-link")
        title_el = item.select_one("h2.job-item__position")
        if not (id_match and link and title_el):
            continue
        href = str(link.get("href", ""))
        url = href if href.startswith("http") else f"{BASE_URL}{href}"
        results.append(
            {
                "external_id": id_match.group(1),
                "url": url,
                "title": title_el.get_text(strip=True),
            }
        )
    return results


def _classify_sidebar_items(soup: BeautifulSoup) -> dict[str, Any]:
    experience_text = None
    salary_text = None
    remote = False
    countries_text = None

    for li in soup.select("aside .card-body ul.list-unstyled li"):
        text = li.get_text(" ", strip=True)
        if not text:
            continue
        location_span = li.select_one(".location-text")
        if location_span:
            countries_text = location_span.get_text(strip=True)
        elif "віддалено" in text.lower():
            remote = True
        elif "досвід" in text.lower():
            experience_text = text
        elif re.search(r"[$€₴£]", text):
            salary_text = text

    return {
        "experience_text": experience_text,
        "salary_text": salary_text,
        "remote": remote,
        "countries_text": countries_text,
    }


def parse_vacancy_page(html: str) -> dict[str, Any]:
    """Extract the full job payload from a vacancy page."""
    soup = BeautifulSoup(html, "lxml")

    title_el = soup.select_one("h1.fs-2")
    company_el = soup.select_one('a.text-secondary[href*="/jobs/company-"]')
    description_el = soup.select_one(".job-post__description")
    sidebar = _classify_sidebar_items(soup)

    return {
        "title": title_el.get_text(strip=True) if title_el else "",
        "company": company_el.get_text(strip=True) if company_el else "",
        "description_html": str(description_el) if description_el else "",
        **sidebar,
    }


def parse_experience_years(experience_text: str | None) -> float | None:
    """Djinni states minimum experience as free text ("Виключно від 1 року досвіду",
    "Без досвіду") — extract the leading number deterministically."""
    if not experience_text:
        return None
    if "без досвіду" in experience_text.lower():
        return 0.0
    match = _EXPERIENCE_RE.search(experience_text)
    return float(match.group(0)) if match else None
