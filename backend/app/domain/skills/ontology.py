"""Versioned skill ontology: canonical ids, aliases, and the few relations that
are safe to assert. See docs/ai-pipeline-v3.md (E1).

Why a hand-maintained list next to embedding-based matching
(app/domain/matching/skill_matching.py): embeddings answer "are these two names
about the same thing" well enough to *score*, but they can't give a skill a
stable label to store as evidence, and they can't express direction — having
TypeScript is real evidence for JavaScript, the reverse is not true. Gap lists
and evidence need both, so aliases and directed relations live here while
similarity stays where it is.

The ontology sharpens what it knows about; it never gates what may be extracted.
An unknown skill flows through the pipeline under its own cleaned-up name, and
adding it here later only improves how it is grouped and explained.

TAXONOMY_VERSION is recorded on every match (see
app/domain/matching/provenance.py). Bump it whenever an id, alias or relation
changes, so a stored result keeps saying which vocabulary produced it.
"""

from dataclasses import dataclass

TAXONOMY_VERSION = "1"


@dataclass(frozen=True)
class Skill:
    id: str
    display: str
    aliases: tuple[str, ...] = ()
    # Directed: holding this skill is real evidence for these. Never read the
    # other way round — "React" does not imply "TypeScript".
    implies: tuple[str, ...] = ()
    # Adjacent but not interchangeable: useful for transferable-skill reasoning,
    # never a match.
    related: tuple[str, ...] = ()
    category: str = "other"


SKILLS: tuple[Skill, ...] = (
    # --- languages ---
    Skill("python", "Python", aliases=("py",), category="language"),
    Skill("javascript", "JavaScript", aliases=("js", "ecmascript", "es6"), category="language"),
    Skill(
        "typescript",
        "TypeScript",
        aliases=("ts",),
        implies=("javascript",),
        category="language",
    ),
    Skill("java", "Java", category="language"),
    Skill("kotlin", "Kotlin", related=("java",), category="language"),
    Skill("swift", "Swift", category="language"),
    Skill("csharp", "C#", aliases=("c sharp", "c#"), category="language"),
    Skill("cpp", "C++", aliases=("c++", "cpp"), category="language"),
    Skill("c", "C", category="language"),
    Skill("go", "Go", aliases=("golang",), category="language"),
    Skill("rust", "Rust", category="language"),
    Skill("php", "PHP", category="language"),
    Skill("ruby", "Ruby", category="language"),
    Skill("scala", "Scala", related=("java",), category="language"),
    Skill("elixir", "Elixir", category="language"),
    Skill("sql", "SQL", category="language"),
    Skill("bash", "Bash", aliases=("shell", "shell scripting"), category="language"),
    # --- frontend ---
    Skill("react", "React", aliases=("react js", "reactjs"), implies=("javascript",), category="frontend"),
    Skill("angular", "Angular", aliases=("angularjs", "angular 2+"), implies=("typescript",), category="frontend"),
    Skill("vue", "Vue", aliases=("vue js", "vuejs"), implies=("javascript",), category="frontend"),
    Skill("svelte", "Svelte", implies=("javascript",), category="frontend"),
    Skill("nextjs", "Next.js", aliases=("next js",), implies=("react",), category="frontend"),
    Skill("redux", "Redux", implies=("react",), category="frontend"),
    Skill("html", "HTML", aliases=("html5",), category="frontend"),
    Skill("css", "CSS", aliases=("css3",), category="frontend"),
    Skill("sass", "Sass", aliases=("scss",), implies=("css",), category="frontend"),
    Skill("tailwind", "Tailwind CSS", aliases=("tailwindcss",), implies=("css",), category="frontend"),
    Skill("webpack", "Webpack", category="frontend"),
    Skill("vite", "Vite", category="frontend"),
    # --- backend / runtimes ---
    Skill("nodejs", "Node.js", aliases=("node", "node js"), implies=("javascript",), category="backend"),
    Skill("express", "Express", aliases=("express js",), implies=("nodejs",), category="backend"),
    Skill("nestjs", "NestJS", aliases=("nest js",), implies=("nodejs", "typescript"), category="backend"),
    Skill("django", "Django", implies=("python",), category="backend"),
    Skill("flask", "Flask", implies=("python",), related=("fastapi",), category="backend"),
    Skill("fastapi", "FastAPI", implies=("python",), related=("flask",), category="backend"),
    Skill("celery", "Celery", implies=("python",), category="backend"),
    Skill("spring", "Spring", aliases=("spring boot",), implies=("java",), category="backend"),
    Skill("laravel", "Laravel", implies=("php",), category="backend"),
    Skill("symfony", "Symfony", implies=("php",), category="backend"),
    Skill("rails", "Ruby on Rails", aliases=("ruby on rails", "ror"), implies=("ruby",), category="backend"),
    Skill("dotnet", ".NET", aliases=(".net", "dot net", "dotnet core"), implies=("csharp",), category="backend"),
    Skill("aspnet", "ASP.NET", aliases=("asp net",), implies=("dotnet", "csharp"), category="backend"),
    # --- data stores and streaming ---
    Skill("postgresql", "PostgreSQL", aliases=("postgres", "pg", "psql"), implies=("sql",), related=("mysql", "mssql"), category="datastore"),
    Skill("mysql", "MySQL", aliases=("mariadb",), implies=("sql",), related=("postgresql",), category="datastore"),
    Skill("mssql", "MS SQL Server", aliases=("sql server", "microsoft sql server", "t sql"), implies=("sql",), related=("postgresql",), category="datastore"),
    Skill("oracledb", "Oracle DB", aliases=("oracle database", "pl sql"), implies=("sql",), category="datastore"),
    Skill("mongodb", "MongoDB", aliases=("mongo",), category="datastore"),
    Skill("redis", "Redis", category="datastore"),
    Skill("elasticsearch", "Elasticsearch", aliases=("elastic search", "opensearch"), category="datastore"),
    Skill("clickhouse", "ClickHouse", implies=("sql",), category="datastore"),
    Skill("kafka", "Kafka", aliases=("apache kafka",), related=("rabbitmq",), category="datastore"),
    Skill("rabbitmq", "RabbitMQ", related=("kafka",), category="datastore"),
    # --- cloud and infrastructure ---
    Skill("aws", "AWS", aliases=("amazon web services",), category="cloud"),
    Skill("gcp", "Google Cloud", aliases=("google cloud platform",), category="cloud"),
    Skill("azure", "Azure", aliases=("microsoft azure",), category="cloud"),
    Skill("docker", "Docker", related=("kubernetes",), category="infra"),
    Skill("kubernetes", "Kubernetes", aliases=("k8s",), related=("docker",), category="infra"),
    Skill("terraform", "Terraform", related=("ansible",), category="infra"),
    Skill("ansible", "Ansible", related=("terraform",), category="infra"),
    Skill("jenkins", "Jenkins", related=("githubactions", "gitlabci"), category="infra"),
    Skill("githubactions", "GitHub Actions", aliases=("github ci",), related=("gitlabci",), category="infra"),
    Skill("gitlabci", "GitLab CI", aliases=("gitlab ci cd",), related=("githubactions",), category="infra"),
    Skill("linux", "Linux", aliases=("unix",), category="infra"),
    Skill("nginx", "Nginx", category="infra"),
    Skill("grafana", "Grafana", related=("prometheus",), category="infra"),
    Skill("prometheus", "Prometheus", related=("grafana",), category="infra"),
    # --- interfaces and architecture ---
    Skill("restapi", "REST API", aliases=("rest", "restful api"), category="practice"),
    Skill("graphql", "GraphQL", category="practice"),
    Skill("grpc", "gRPC", category="practice"),
    Skill("websockets", "WebSockets", aliases=("web sockets",), category="practice"),
    Skill("microservices", "Microservices", category="practice"),
    Skill("cicd", "CI/CD", aliases=("ci cd", "continuous integration"), category="practice"),
    Skill("tdd", "TDD", aliases=("test driven development",), category="practice"),
    Skill("agile", "Agile", category="practice"),
    Skill("scrum", "Scrum", implies=("agile",), category="practice"),
    Skill("git", "Git", category="tooling"),
    Skill("jira", "Jira", category="tooling"),
    Skill("figma", "Figma", category="tooling"),
    # --- testing ---
    Skill("selenium", "Selenium", related=("playwright", "cypress"), category="testing"),
    Skill("playwright", "Playwright", related=("selenium", "cypress"), category="testing"),
    Skill("cypress", "Cypress", related=("selenium", "playwright"), category="testing"),
    Skill("pytest", "pytest", implies=("python",), category="testing"),
    Skill("jest", "Jest", implies=("javascript",), category="testing"),
    Skill("postman", "Postman", category="testing"),
    Skill("jmeter", "JMeter", category="testing"),
    # --- data / ml ---
    Skill("pandas", "pandas", implies=("python",), category="data"),
    Skill("numpy", "NumPy", implies=("python",), category="data"),
    Skill("spark", "Apache Spark", aliases=("pyspark",), category="data"),
    Skill("airflow", "Airflow", aliases=("apache airflow",), implies=("python",), category="data"),
    Skill("pytorch", "PyTorch", implies=("python",), related=("tensorflow",), category="ml"),
    Skill("tensorflow", "TensorFlow", implies=("python",), related=("pytorch",), category="ml"),
    Skill("scikitlearn", "scikit-learn", aliases=("sklearn",), implies=("python",), category="ml"),
    Skill("llm", "LLM", aliases=("large language models", "genai", "generative ai"), category="ml"),
    Skill("nlp", "NLP", aliases=("natural language processing",), category="ml"),
    # --- mobile ---
    Skill("android", "Android", related=("kotlin", "java"), category="mobile"),
    Skill("ios", "iOS", related=("swift",), category="mobile"),
    Skill("reactnative", "React Native", aliases=("react native",), related=("react",), category="mobile"),
    Skill("flutter", "Flutter", related=("dart",), category="mobile"),
    Skill("dart", "Dart", related=("flutter",), category="language"),
)


def _index() -> dict[str, Skill]:
    """Every id, display name and alias points at its Skill, under the same
    lookup key shape the normalizer produces."""
    from app.domain.skills.normalizer import lookup_key

    index: dict[str, Skill] = {}
    for skill in SKILLS:
        for name in (skill.id, skill.display, *skill.aliases):
            index.setdefault(lookup_key(name), skill)
    return index


_BY_KEY: dict[str, Skill] | None = None


def by_key(key: str) -> Skill | None:
    """Look up a skill by an already-normalized key (see normalizer.lookup_key)."""
    global _BY_KEY
    if _BY_KEY is None:
        _BY_KEY = _index()
    return _BY_KEY.get(key)


def by_id(skill_id: str) -> Skill | None:
    return next((skill for skill in SKILLS if skill.id == skill_id), None)
