FROM python:3.13-slim

WORKDIR /app

# Dependencies from pyproject.toml alone first, so this layer's cache key depends
# only on dependency changes, not on every code change. setuptools needs *some*
# app/ package to resolve [tool.setuptools.packages.find] against — a stub is
# enough, it gets overwritten by the real COPY below and reinstalled --no-deps.
#
# No torch, no sentence-transformers, no model download: embedding and reranking
# are two HTTPS calls to Voyage, so the image is a plain Python web app again.
# dev extras (pytest/ruff/mypy) are deliberately left out — they run locally and
# in CI, not in the deployed container.
COPY pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py && pip install --no-cache-dir .

COPY . .
RUN pip install --no-cache-dir --no-deps .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
