"""Category lists the scrape rotation cycles through — see
app/workers/tasks/scrape.py and app/repositories/job_repository.py::
get_least_recently_scraped_category. Passed as the adapter's single search keyword
(`?category=` on DOU, `?primary_keyword=` on Djinni).

DOU_CATEGORIES matches the exact category names from DOU's own filter sidebar
verbatim — verified live that the category name doubles as the working query value
(e.g. `?category=Artist` returns real Artist-category postings, not an error/empty
feed). Omits "Військова справа" (military affairs) — not a fit for any forseeable
user of this app.

DJINNI_CATEGORIES is a curated best-effort list of Djinni's `primary_keyword`
values — spot-verified `Python` and `Design` live, not exhaustively verified for
every entry. Djinni doesn't expose as clean a category list as DOU; if a category
here turns out to return zero/wrong results, drop it rather than guessing harder.
"""

DOU_CATEGORIES = [
    ".NET",
    "Account Manager",
    "AI/ML",
    "Analyst",
    "Android",
    "Animator",
    "Architect",
    "Artist",
    "Assistant",
    "Big Data",
    "Blockchain",
    "C++",
    "C-level",
    "Copywriter",
    "Data Engineer",
    "Data Science",
    "DBA",
    "Design",
    "DevOps",
    "Embedded",
    "Engineering Manager",
    "Erlang",
    "ERP/CRM",
    "Finance",
    "Flutter",
    "Front End",
    "Golang",
    "Hardware",
    "HR",
    "iOS/macOS",
    "Java",
    "Legal",
    "Marketing",
    "No-code",
    "Node.js",
    "Office Manager",
    "Other",
    "PHP",
    "Procurement",
    "Product Manager",
    "Project Manager",
    "Python",
    "QA",
    "React Native",
    "Ruby",
    "Rust",
    "Sales",
    "Salesforce",
    "SAP",
    "Scala",
    "Scrum Master",
    "Security",
    "SEO",
    "Support",
    "SysAdmin",
    "Technical Writer",
    "Unity",
    "Unreal Engine",
]

DJINNI_CATEGORIES = [
    "Python",
    "JavaScript",
    "TypeScript",
    "Java",
    "PHP",
    "Ruby",
    "C++",
    "C#",
    "Golang",
    "Rust",
    "iOS",
    "Android",
    "Flutter",
    "React Native",
    "QA",
    "DevOps",
    "Data Science",
    "Data Engineer",
    "Design",
    "Project Manager",
    "Product Manager",
    "Business Analyst",
    "Sales",
    "HR",
]

CATEGORIES_BY_SOURCE: dict[str, list[str]] = {
    "dou": DOU_CATEGORIES,
    "djinni": DJINNI_CATEGORIES,
}
