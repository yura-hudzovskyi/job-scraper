FROM python:3.13-slim

WORKDIR /app

# Install dependencies from pyproject.toml alone first, so this layer's cache key
# depends only on dependency changes, not every code change. setuptools needs *some*
# app/ package to resolve [tool.setuptools.packages.find] against — a stub is enough,
# it gets overwritten by the real COPY . . below and reinstalled with --no-deps.
#
# torch first, from PyTorch's own CPU-only index, in its own command — the default
# PyPI build pulls several GB of unused nvidia-* CUDA runtime libs on this GPU-less
# Ampere VM. --extra-index-url PyPI is needed here too (the cpu index's own mirrored
# typing-extensions has a metadata bug that otherwise breaks resolution) but verified
# it still correctly picks the +cpu build, not PyPI's CUDA one. Once torch is
# installed here, sentence-transformers' own requirement below is already satisfied
# and pip won't replace it.
COPY pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py \
    && pip install --no-cache-dir \
       --index-url https://download.pytorch.org/whl/cpu \
       --extra-index-url https://pypi.org/simple \
       torch \
    && pip install --no-cache-dir ".[dev,matching,llm]"

COPY . .
RUN pip install --no-cache-dir --no-deps .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
