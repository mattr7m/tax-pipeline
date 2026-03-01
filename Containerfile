# Containerfile — Fedora-based tax processor
#
# Build:
#   podman build -t tax-processor -f Containerfile .
#
# Run (interactive):
#   podman run -it --rm \
#     -e TAX_YEAR=2025 \
#     -v ./data:/data/taxes/data:Z \
#     -p 8000:8000 \
#     tax-processor
#
# With Claude API:
#   podman run -it --rm \
#     -e TAX_YEAR=2025 \
#     -e ANTHROPIC_API_KEY=sk-ant-... \
#     -v ./data:/data/taxes/data:Z \
#     -p 8000:8000 \
#     tax-processor
#
# With host network (for Ollama / local LLM on host):
#   podman run -it --rm \
#     --network=host \
#     -e TAX_YEAR=2025 \
#     -v ./data:/data/taxes/data:Z \
#     tax-processor

FROM registry.fedoraproject.org/fedora:41

# System dependencies:
#   tesseract + eng langpack — OCR for PDF text extraction (pytesseract)
#   poppler-utils — pdftoppm/pdfinfo for pdf2image
#   age — encryption tool for vault files
RUN dnf install -y --setopt=install_weak_deps=False \
        python3 \
        python3-pip \
        tesseract \
        tesseract-langpack-eng \
        poppler-utils \
        age \
    && dnf clean all \
    && rm -rf /var/cache/dnf

WORKDIR /data/taxes

# Python dependencies — separate COPY for layer caching
# pyage is excluded: it depends on Pyro4 which is broken on Python 3.13;
# the codebase uses the age CLI tool directly (installed above), not pyage.
COPY requirements.txt .
RUN grep -v pyage requirements.txt > /tmp/requirements-container.txt \
    && pip install --no-cache-dir -r /tmp/requirements-container.txt \
    && pip install --no-cache-dir 'markdown-it-py>=3.0.0' \
    && rm /tmp/requirements-container.txt

# Project code (filtered by .containerignore)
COPY . .
RUN chmod +x scripts/entrypoint.sh

ENV TAX_YEAR=""
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

ENTRYPOINT ["scripts/entrypoint.sh"]
