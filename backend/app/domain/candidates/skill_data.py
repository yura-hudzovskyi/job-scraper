"""Curated skill definitions + transferability weights backing the default
SkillRegistry. Data, not logic — see skills.py for the registry itself.

Extend this list as new vacancies surface skills it doesn't recognize yet; there's no
mechanism (deliberately) for auto-learning new skills from scraped text, since a typo
silently becoming a "known" skill would be worse than an occasional unresolved one.
"""

from app.domain.candidates.skills import SkillDefinition, SkillRegistry, SkillRelation

SKILL_DEFINITIONS: list[SkillDefinition] = [
    # Languages
    SkillDefinition("javascript", "language", aliases=["js", "ecmascript"]),
    SkillDefinition("typescript", "language", aliases=["ts"]),
    SkillDefinition("python", "language", aliases=["py"]),
    SkillDefinition("java", "language"),
    SkillDefinition("kotlin", "language"),
    SkillDefinition("go", "language", aliases=["golang"]),
    SkillDefinition("rust", "language"),
    SkillDefinition("c#", "language", aliases=["csharp", "dotnet", ".net"]),
    SkillDefinition("php", "language"),
    SkillDefinition("ruby", "language"),
    SkillDefinition("sql", "language"),
    # Frontend frameworks
    SkillDefinition("react", "frontend_framework", aliases=["reactjs", "react.js"]),
    SkillDefinition("nextjs", "frontend_framework", aliases=["next.js", "next"]),
    SkillDefinition("vue", "frontend_framework", aliases=["vuejs", "vue.js"]),
    SkillDefinition("nuxt", "frontend_framework", aliases=["nuxtjs", "nuxt.js"]),
    SkillDefinition("angular", "frontend_framework"),
    SkillDefinition("svelte", "frontend_framework"),
    SkillDefinition("redux", "frontend_state"),
    SkillDefinition("tanstack-query", "frontend_state", aliases=["react-query"]),
    SkillDefinition("tailwind", "frontend_styling", aliases=["tailwindcss"]),
    # Backend frameworks
    SkillDefinition("django", "backend_framework"),
    SkillDefinition("fastapi", "backend_framework"),
    SkillDefinition("flask", "backend_framework"),
    SkillDefinition("nestjs", "backend_framework", aliases=["nest.js", "nest"]),
    SkillDefinition("express", "backend_framework", aliases=["expressjs", "express.js"]),
    SkillDefinition("nodejs", "backend_runtime", aliases=["node", "node.js"]),
    SkillDefinition("spring", "backend_framework", aliases=["spring-boot", "springboot"]),
    SkillDefinition("rails", "backend_framework", aliases=["ruby-on-rails", "ror"]),
    SkillDefinition("laravel", "backend_framework"),
    SkillDefinition("aspnet", "backend_framework", aliases=["asp.net", "asp.net-core"]),
    # APIs
    SkillDefinition("rest", "api_style", aliases=["restful", "rest-api"]),
    SkillDefinition("graphql", "api_style"),
    SkillDefinition("grpc", "api_style"),
    SkillDefinition("websockets", "api_style", aliases=["websocket", "ws"]),
    # Databases
    SkillDefinition("postgresql", "database", aliases=["postgres", "psql"]),
    SkillDefinition("mysql", "database", aliases=["mariadb"]),
    SkillDefinition("sqlite", "database"),
    SkillDefinition("mongodb", "database", aliases=["mongo"]),
    SkillDefinition("redis", "cache"),
    SkillDefinition("elasticsearch", "search", aliases=["elastic", "es"]),
    SkillDefinition("dynamodb", "database"),
    # Messaging
    SkillDefinition("celery", "task_queue"),
    SkillDefinition("rabbitmq", "message_broker"),
    SkillDefinition("kafka", "message_broker", aliases=["apache-kafka"]),
    SkillDefinition("sqs", "message_broker", aliases=["aws-sqs"]),
    # DevOps / cloud
    SkillDefinition("docker", "devops"),
    SkillDefinition("kubernetes", "devops", aliases=["k8s"]),
    SkillDefinition("terraform", "devops"),
    SkillDefinition("aws", "cloud", aliases=["amazon-web-services"]),
    SkillDefinition("gcp", "cloud", aliases=["google-cloud", "google-cloud-platform"]),
    SkillDefinition("azure", "cloud", aliases=["microsoft-azure"]),
    SkillDefinition("github-actions", "ci_cd", aliases=["gh-actions"]),
    SkillDefinition("gitlab-ci", "ci_cd"),
    SkillDefinition("jenkins", "ci_cd"),
    # Testing
    SkillDefinition("pytest", "testing"),
    SkillDefinition("jest", "testing"),
    SkillDefinition("vitest", "testing"),
    SkillDefinition("playwright", "testing"),
    SkillDefinition("selenium", "testing"),
    SkillDefinition("cypress", "testing"),
    # AI / ML
    SkillDefinition("openai-api", "ai_ml", aliases=["openai"]),
    SkillDefinition("langchain", "ai_ml"),
    SkillDefinition("embeddings", "ai_ml"),
    SkillDefinition("pytorch", "ai_ml", aliases=["torch"]),
    SkillDefinition("tensorflow", "ai_ml"),
    SkillDefinition("scikit-learn", "ai_ml", aliases=["sklearn"]),
    # ORMs
    SkillDefinition("sqlalchemy", "orm"),
    SkillDefinition("prisma", "orm"),
    SkillDefinition("typeorm", "orm"),
]

# (from, to, weight) — built bidirectionally below, so define each pair once.
_RELATION_PAIRS: list[tuple[str, str, float]] = [
    # Languages
    ("javascript", "typescript", 0.9),
    ("java", "kotlin", 0.7),
    ("python", "go", 0.3),
    ("php", "python", 0.3),
    ("ruby", "python", 0.4),
    # Frontend frameworks — React-family transfers well among themselves
    ("react", "nextjs", 0.85),
    ("react", "vue", 0.5),
    ("react", "svelte", 0.45),
    ("react", "angular", 0.4),
    ("vue", "nuxt", 0.85),
    ("vue", "angular", 0.4),
    ("angular", "svelte", 0.35),
    ("redux", "tanstack-query", 0.3),
    # Backend frameworks — same-language frameworks transfer best
    ("django", "fastapi", 0.7),
    ("django", "flask", 0.75),
    ("flask", "fastapi", 0.75),
    ("django", "nestjs", 0.5),
    ("fastapi", "nestjs", 0.55),
    ("nestjs", "express", 0.7),
    ("express", "fastapi", 0.5),
    ("nodejs", "nestjs", 0.6),
    ("nodejs", "express", 0.6),
    ("django", "rails", 0.5),
    ("django", "laravel", 0.45),
    ("rails", "laravel", 0.5),
    ("spring", "aspnet", 0.4),
    ("django", "spring", 0.35),
    # APIs — REST experience partially transfers to other API styles
    ("rest", "graphql", 0.4),
    ("rest", "grpc", 0.35),
    ("graphql", "grpc", 0.3),
    # Databases — relational engines transfer well to each other
    ("postgresql", "mysql", 0.8),
    ("postgresql", "sqlite", 0.7),
    ("mysql", "sqlite", 0.7),
    ("postgresql", "mongodb", 0.3),
    ("mysql", "mongodb", 0.3),
    ("mongodb", "dynamodb", 0.4),
    ("elasticsearch", "postgresql", 0.2),
    # Messaging
    ("rabbitmq", "kafka", 0.5),
    ("rabbitmq", "sqs", 0.5),
    ("kafka", "sqs", 0.4),
    ("celery", "rabbitmq", 0.4),
    # DevOps / cloud
    ("docker", "kubernetes", 0.6),
    ("aws", "gcp", 0.6),
    ("aws", "azure", 0.6),
    ("gcp", "azure", 0.6),
    ("github-actions", "gitlab-ci", 0.7),
    ("github-actions", "jenkins", 0.5),
    ("gitlab-ci", "jenkins", 0.5),
    ("terraform", "kubernetes", 0.25),
    # Testing
    ("jest", "vitest", 0.85),
    ("playwright", "cypress", 0.7),
    ("playwright", "selenium", 0.6),
    ("cypress", "selenium", 0.6),
    ("pytest", "jest", 0.3),
    # AI/ML
    ("pytorch", "tensorflow", 0.6),
    ("openai-api", "langchain", 0.5),
    ("openai-api", "embeddings", 0.4),
    # ORMs
    ("sqlalchemy", "django", 0.3),
    ("prisma", "typeorm", 0.6),
    ("sqlalchemy", "prisma", 0.3),
]


def _bidirectional_relations() -> list[SkillRelation]:
    relations = []
    for from_skill, to_skill, weight in _RELATION_PAIRS:
        relations.append(SkillRelation(from_skill, to_skill, weight))
        relations.append(SkillRelation(to_skill, from_skill, weight))
    return relations


def build_default_skill_registry() -> SkillRegistry:
    return SkillRegistry(SKILL_DEFINITIONS, _bidirectional_relations())
