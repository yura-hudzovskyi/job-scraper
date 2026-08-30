"""Pure selection logic for which category to scrape next. Framework-free and
DB-free by design, so it's testable without a database — see
app/repositories/job_repository.py::get_least_recently_scraped_category, which
feeds this the real scrape-history data.
"""

from datetime import datetime


def pick_next_category(categories: list[str], last_scraped: dict[str, datetime]) -> str:
    """Picks whichever category was scraped longest ago. A category absent from
    last_scraped (never run at all) always wins over one that has been, regardless
    of how long ago the others ran."""
    never_scraped = [category for category in categories if category not in last_scraped]
    if never_scraped:
        return never_scraped[0]
    return min(categories, key=lambda category: last_scraped[category])
