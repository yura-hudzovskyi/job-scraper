"""Parsing helpers shared by all source adapters: salary text, seniority guess, and
HTML-to-text conversion. Kept deterministic and dependency-light — real requirement/
skill extraction is an LLM concern deferred to Phase 4 (see docs/roadmap.md).
"""

import re

from bs4 import BeautifulSoup

from app.domain.jobs.models import SalaryRange

_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "₴": "UAH", "£": "GBP"}
_SYMBOL_CLASS = "".join(re.escape(s) for s in _CURRENCY_SYMBOLS)

_RANGE_RE = re.compile(rf"[{_SYMBOL_CLASS}]\s*[\d.,]+\s*[-–—]\s*[{_SYMBOL_CLASS}]?\s*[\d.,]+")
_UP_TO_RE = re.compile(rf"(?:до|up to)\s*[{_SYMBOL_CLASS}]\s*[\d.,]+", re.IGNORECASE)
_FROM_RE = re.compile(rf"(?:від|from)\s*[{_SYMBOL_CLASS}]\s*[\d.,]+", re.IGNORECASE)
_SINGLE_RE = re.compile(rf"[{_SYMBOL_CLASS}]\s*[\d.,]+")
_SYMBOL_RE = re.compile(f"[{_SYMBOL_CLASS}]")
_NUMBER_RE = re.compile(r"[\d.,]+")


def _currency_of(span: str) -> str | None:
    match = _SYMBOL_RE.search(span)
    return _CURRENCY_SYMBOLS.get(match.group(0)) if match else None


def _to_number(raw: str) -> float:
    return float(raw.replace(",", "").replace(" ", ""))


def parse_salary_range(text: str | None) -> SalaryRange | None:
    """Parse free-text salary strings such as "$2000-3000", "до $1000" (up to),
    "від $1500" (from), or a bare "$2500" into a SalaryRange. None if no amount found."""
    if not text:
        return None
    text = text.strip()

    if match := _RANGE_RE.search(text):
        span = match.group(0)
        numbers = _NUMBER_RE.findall(span)
        return SalaryRange(
            min=_to_number(numbers[0]),
            max=_to_number(numbers[-1]),
            currency=_currency_of(span),
        )

    if match := _UP_TO_RE.search(text):
        span = match.group(0)
        return SalaryRange(
            min=None, max=_to_number(_NUMBER_RE.findall(span)[-1]), currency=_currency_of(span)
        )

    if match := _FROM_RE.search(text):
        span = match.group(0)
        return SalaryRange(
            min=_to_number(_NUMBER_RE.findall(span)[-1]), max=None, currency=_currency_of(span)
        )

    if match := _SINGLE_RE.search(text):
        span = match.group(0)
        value = _to_number(_NUMBER_RE.findall(span)[-1])
        return SalaryRange(min=value, max=value, currency=_currency_of(span))

    return None


_SENIORITY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("senior", ["senior", "sr.", "lead", "principal", "staff"]),
    ("middle", ["middle", "mid-level", "mid level"]),
    ("junior", ["junior", "jr.", "trainee", "intern"]),
]


def guess_seniority(title: str) -> str | None:
    """Best-effort seniority guess from a job title only (no LLM)."""
    lowered = title.lower()
    for level, keywords in _SENIORITY_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return level
    return None


def html_to_text(html: str | None) -> str:
    """Strip tags and collapse whitespace, keeping one line break per block element."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    lines = (line.strip() for line in soup.get_text(separator="\n").splitlines())
    return "\n".join(line for line in lines if line)
