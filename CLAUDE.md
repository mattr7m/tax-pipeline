# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A privacy-focused tax form processor that uses a hybrid local/cloud LLM pipeline to extract data from tax PDFs, apply tax logic, and fill IRS forms. **Sensitive data (SSNs, EINs, account numbers) never leaves the local machine** — only sanitized data with redaction tokens is sent to any external LLM.

## Commands

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.yaml.example data/config.yaml

# Full pipeline (default: Ollama extraction + Claude processing)
python scripts/orchestrate.py --year 2025

# Full pipeline (100% offline)
python scripts/orchestrate.py --year 2025 --backend local --extraction-backend local

# Individual steps
python scripts/extract.py --input data/raw/2025 --output data/extracted/2025.json --extraction-backend ollama
python scripts/sanitize.py --input data/extracted/2025.json --output data/sanitized/2025.json --vault data/vault/2025.age
python scripts/process.py --input data/sanitized/2025.json --output data/instructions/2025.json --backend claude
python scripts/assemble.py --instructions data/instructions/2025.json --vault data/vault/2025.age --templates data/templates/blank-forms --output data/output/2025

# Prepare tax knowledge from IRS instruction PDFs
python scripts/prepare_knowledge.py --pdf ~/Downloads/i1040gi.pdf --form 1040 --year 2025 --backend claude

# Test tax knowledge loading
python scripts/tax_knowledge.py 2025

# Start local LLM server (llama.cpp)
./scripts/start-local-llm.sh [model-path]
```

## Architecture

The pipeline has 4 sequential steps, each a standalone CLI script (click-based) coordinated by `orchestrate.py`:

1. **Extract** (`extract.py`) — PDF → OCR (Tesseract via PyMuPDF) → LLM structuring → JSON. Backends: `ollama` (Llama 3.2, fast) or `local` (llama.cpp server, better for complex docs).
2. **Sanitize** (`sanitize.py`) — Regex-based detection of SSNs/EINs/account numbers → replaced with `[TYPE_REDACTED_N]` tokens. Originals stored in `age`-encrypted vault (.age file).
3. **Process** (`process.py`) — Sanitized JSON sent to LLM for tax logic, form mapping, and calculations. Backends: `claude` (Anthropic API) or `local` (llama.cpp). When using local backend, `tax_knowledge.py` injects current-year tax tables, field mappings, and rules into the system prompt.
4. **Assemble** (`assemble.py`) — Decrypts vault, rehydrates tokens with real values, fills blank PDF forms (via PyMuPDF or fillpdf), generates `REVIEW.md` summary.

### Key design patterns

- All scripts share `load_config()` and `PROJECT_ROOT` from `scripts/config_loader.py`, which looks for `data/config.yaml` first, then falls back to `config.yaml` / `config.yaml.example`
- LLM responses are parsed with a shared `parse_json_response()` that handles markdown code blocks
- The `Sanitizer` class in `sanitize.py` maintains a vault dict and token counter; `decrypt_vault()` is duplicated in both `sanitize.py` and `assemble.py`
- `tax_knowledge.py` provides `TaxKnowledgeBase` class with lazy-loading and `load_knowledge_for_processing()` convenience function
- `process.py` imports directly from `tax_knowledge.py` (same-directory import)

## Data Flow & Directory Layout

```
data/raw/{year}/*.pdf     → extract  → data/extracted/{year}.json
                          → sanitize → data/sanitized/{year}.json + data/vault/{year}.age
                          → process  → data/instructions/{year}.json
                          → assemble → data/output/{year}/ (filled PDFs + REVIEW.md)
```

Everything under `data/` is gitignored — this includes config, templates, tax knowledge, and all pipeline artifacts. A single volume mount at `data/` covers all persistent state for containers.

## Tax Knowledge Base

`data/tax-knowledge/{year}/` provides current-year data critical for the local LLM backend:
- `tax-tables.json` — brackets, standard deductions, contribution limits, credit amounts
- `form-{name}-fields.json` — PDF field ID → IRS line number mappings (e.g., `f1_25` → Line 1a)
- `form-{name}-instructions.md` — line-by-line IRS guidance (generated via `prepare_knowledge.py`)
- `tax-rules-summary.md` — key rules reference

To add a new tax year: `cp -r data/tax-knowledge/2025 data/tax-knowledge/2026` and update values from IRS Revenue Procedure announcements.

## Configuration

`data/config.yaml` controls: LLM backend settings (model names, URLs, timeouts, temperatures), OCR settings, sensitive data regex patterns (with replacement templates), and document type detection keywords. New document types or sensitive patterns are added here. The tracked seed file is `config.yaml.example` at the project root; copy it to `data/config.yaml` for local dev (the container entrypoint does this automatically).

## Security Constraints

- Never send unsanitized data to external APIs — `process.py` runs `verify_sanitized()` which checks for raw SSN patterns before proceeding
- Vault encryption uses `age` CLI tool; falls back to base64 encoding if `age` is not installed (not secure, only for development)
- Raw PDFs, vault files, and all intermediate data are gitignored
