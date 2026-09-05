FROM python:3.13-slim

WORKDIR /app

# CPU-only torch, and this index-url is not a preference. The default aarch64
# wheel from PyPI is 454 MB and drags the whole CUDA stack (nvidia-*, triton,
# cuda-toolkit) onto a machine with no GPU: about 8 GB of image for nothing.
# The CPU wheel is 159 MB and pulls no CUDA package at all (spec 17.5).
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py && pip install --no-cache-dir .

# The weights are baked in rather than downloaded at boot. Spec 17.4 wants a
# self-hosted model re-creatable byte-for-byte from a pinned revision; an image
# built from that revision *is* that guarantee, and it also means a restart
# needs no network and no volume that could be wiped. Costs ~1.2 GB of image,
# paid once per model change rather than once per container start.
#
# These must stay equal to the defaults in app/config.py: the build downloads
# what the ARGs say, and the process loads what the settings say. /info reports
# the settings, so a mismatch shows up as a service that redownloads at startup.
ENV HF_HOME=/opt/hf
ENV ML_EXTRACTOR_MODEL_ID=fastino/gliner2-multi-v1
ENV ML_EXTRACTOR_REVISION=c6296e25603e4d31f68ef8a9f4edb73421d1e45a
RUN python -c "import os, gliner2; gliner2.GLiNER2.from_pretrained(os.environ['ML_EXTRACTOR_MODEL_ID'], revision=os.environ['ML_EXTRACTOR_REVISION'])" \
    && find /opt/hf \( -name '*.jpg' -o -name '*.png' \) -delete

COPY . .
RUN pip install --no-cache-dir --no-deps .

# One worker. The model is loaded per process, so a second worker is a second
# 4 GB copy of the same weights answering the same queue.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "1"]
