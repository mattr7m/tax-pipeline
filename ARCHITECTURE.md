# Tax Processor: Technical Summary

A privacy-focused, hybrid local/cloud workflow for automating tax form preparation using LLMs.

## Core Principle

**Sensitive data (SSNs, EINs, account numbers) never leaves your machine.**

The system extracts data locally, sanitizes it, optionally sends only sanitized data to an LLM for tax logic, then reassembles everything locally.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              YOUR MACHINE                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────┐                 │
│  │  Raw PDFs   │      │  Tesseract  │      │  LLM        │                 │
│  │  ─────────  │─────▶│  OCR        │─────▶│  Extraction │                 │
│  │  • W-2s     │      │             │      │             │                 │
│  │  • 1099s    │      │  (image →   │      │  (text →    │                 │
│  │  • 1098s    │      │   text)     │      │   JSON)     │                 │
│  │  • Prior yr │      └─────────────┘      └──────┬──────┘                 │
│  └─────────────┘                                  │                         │
│                                                   ▼                         │
│                                          ┌───────────────┐                  │
│                                          │  Extracted    │                  │
│                                          │  JSON         │                  │
│                                          │  (with SSNs)  │                  │
│                                          └───────┬───────┘                  │
│                                                  │                          │
│                                                  ▼                          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         SANITIZER                                    │   │
│  │                                                                      │   │
│  │   SSN: 123-45-6789  ──▶  [SSN_REDACTED_1]                           │   │
│  │   EIN: 12-3456789   ──▶  [EIN_REDACTED_1]                           │   │
│  │   Acct: 9876543210  ──▶  [ACCT_REDACTED_1]                          │   │
│  │                                                                      │   │
│  │   Original values stored in encrypted vault (never leaves machine)  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                          │                              │                   │
│                          ▼                              ▼                   │
│                 ┌─────────────────┐           ┌─────────────────┐          │
│                 │  Sanitized JSON │           │  Encrypted      │          │
│                 │  (safe to send) │           │  Vault (.age)   │          │
│                 └────────┬────────┘           └────────┬────────┘          │
│                          │                             │                    │
└──────────────────────────┼─────────────────────────────┼────────────────────┘
                           │                             │
         ┌─────────────────┴─────────────────┐           │
         ▼                                   ▼           │
┌─────────────────┐                 ┌─────────────────┐  │
│  Claude API     │       OR        │  Local LLM      │  │
│  (--backend     │                 │  (--backend     │  │
│   claude)       │                 │   local)        │  │
│                 │                 │                 │  │
│  • Tax logic    │                 │  • Qwen3-235B   │  │
│  • Form mapping │                 │  • llama.cpp    │  │
│  • Calculations │                 │  • 100% offline │  │
└────────┬────────┘                 └────────┬────────┘  │
         │                                   │           │
         └─────────────────┬─────────────────┘           │
                           │                             │
                           ▼                             │
                  ┌─────────────────┐                    │
                  │  Tax            │                    │
                  │  Instructions   │                    │
                  │  (JSON)         │                    │
                  └────────┬────────┘                    │
                           │                             │
                           └──────────────┬──────────────┘
                                          │
┌─────────────────────────────────────────┼───────────────────────────────────┐
│                              YOUR MACHINE                                    │
│                                          ▼                                   │
│                                 ┌─────────────────┐                         │
│                                 │    ASSEMBLER    │                         │
│                                 │                 │                         │
│                                 │  Instructions   │                         │
│                                 │       +         │                         │
│                                 │  Decrypted      │                         │
│                                 │  Vault          │                         │
│                                 │       ↓         │                         │
│                                 │  [SSN_REDACTED] │                         │
│                                 │  → 123-45-6789  │                         │
│                                 └────────┬────────┘                         │
│                                          │                                   │
│                                          ▼                                   │
│                                 ┌─────────────────┐                         │
│                                 │  Filled PDF     │                         │
│                                 │  Forms          │                         │
│                                 │  ────────────   │                         │
│                                 │  • 1040         │                         │
│                                 │  • Schedule A   │                         │
│                                 │  • etc.         │                         │
│                                 └─────────────────┘                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Pipeline Steps

### Step 1: Extraction (`extract.py`)

**Purpose:** Convert PDF documents into structured JSON data.

**Process:**
1. Load PDF file using PyMuPDF
2. Extract text (native extraction or Tesseract OCR for scanned docs)
3. Detect document type (W-2, 1099-INT, 1098, etc.) via keyword matching
4. Send text to LLM with extraction prompt
5. LLM returns structured JSON with all fields

**Backend Options:**
| Flag | Model | Speed | Notes |
|------|-------|-------|-------|
| `--extraction-backend ollama` | Llama 3.2 (8B) | ~50 tok/s | Default, fast |
| `--extraction-backend local` | Qwen3-235B | ~10 tok/s | Better for complex docs |

**Input:** PDF files in `data/raw/{year}/`
**Output:** `data/extracted/{year}.json`

**Example extracted data:**
```json
{
  "tax_year": "2025",
  "documents": [
    {
      "document_type": "w2",
      "source_file": "w2-employer.pdf",
      "employer": {
        "name": "Acme Corporation",
        "ein": "12-3456789",
        "address": "123 Main St, San Francisco, CA"
      },
      "employee": {
        "name": "John Smith",
        "ssn": "123-45-6789"
      },
      "wages": 95000,
      "federal_withheld": 14250,
      "social_security_wages": 95000,
      "medicare_wages": 95000
    }
  ],
  "summary": {
    "income": { "wages": 95000 },
    "withholding": { "federal": 14250 }
  }
}
```

---

### Step 2: Sanitization (`sanitize.py`)

**Purpose:** Remove sensitive data before sending to external LLM.

**Process:**
1. Load extracted JSON
2. Apply regex patterns to find SSNs, EINs, account numbers
3. Replace each with a unique token (e.g., `[SSN_REDACTED_1]`)
4. Store mapping (token → real value) in encrypted vault
5. Output sanitized JSON safe for external transmission

**Encryption:** Uses `age` (modern GPG alternative) with passphrase

**Input:** `data/extracted/{year}.json`
**Output:** 
- `data/sanitized/{year}.json` (safe to send externally)
- `data/vault/{year}.age` (encrypted, stays local)

**Example sanitized data:**
```json
{
  "employee": {
    "name": "John Smith",
    "ssn": "[SSN_REDACTED_1]"
  },
  "employer": {
    "ein": "[EIN_REDACTED_1]"
  },
  "wages": 95000
}
```

**Vault contents (encrypted):**
```json
{
  "[SSN_REDACTED_1]": "123-45-6789",
  "[EIN_REDACTED_1]": "12-3456789"
}
```

---

### Step 3: Processing (`process.py`)

**Purpose:** Apply tax logic, determine forms needed, map data to fields.

**Process:**
1. Load sanitized JSON (no sensitive data)
2. Optionally load prior year context
3. Send to LLM with tax preparation prompt
4. LLM determines:
   - Which forms are needed (1040, Schedule A, etc.)
   - How to map data to form fields
   - Tax calculations
   - Warnings and missing information

**Backend Options:**
| Flag | Service | Speed | Privacy |
|------|---------|-------|---------|
| `--backend claude` | Claude API | ~80 tok/s | Sanitized data sent to Anthropic |
| `--backend local` | llama.cpp | ~10 tok/s | 100% local, no external calls |

**Input:** `data/sanitized/{year}.json`
**Output:** `data/instructions/{year}.json`

**Example instructions output:**
```json
{
  "tax_year": "2025",
  "filing_status": "single",
  "forms_needed": ["1040", "Schedule A"],
  "form_instructions": {
    "1040": {
      "fields": {
        "f1_01": { "value": "John", "line": "First name" },
        "f1_02": { "value": "Smith", "line": "Last name" },
        "f1_03": { "value": "[SSN_REDACTED_1]", "line": "SSN" },
        "f1_25": { "value": 95000, "line": "1a - Wages" }
      },
      "calculations": [
        { "description": "Total income", "result": 96247 },
        { "description": "Standard deduction", "result": 14600 },
        { "description": "Taxable income", "result": 81647 }
      ]
    }
  },
  "warnings": [
    "Verify state tax payments - not found in documents"
  ],
  "missing_info": [
    "Charitable contributions"
  ],
  "summary": {
    "total_income": 96247,
    "total_deductions": 14600,
    "taxable_income": 81647,
    "total_tax": 13500,
    "total_withheld": 14250,
    "refund_or_owed": 750
  }
}
```

---

### Step 4: Assembly (`assemble.py`)

**Purpose:** Combine instructions with real data and fill PDF forms.

**Process:**
1. Decrypt vault to recover real SSNs/EINs
2. Replace tokens in instructions with real values
3. Load blank PDF forms from `templates/blank-forms/`
4. Fill form fields using PyMuPDF or fillpdf
5. Generate review document (REVIEW.md)
6. Optionally flatten PDFs (make non-editable)

**Input:** 
- `data/instructions/{year}.json`
- `data/vault/{year}.age`
- `templates/blank-forms/*.pdf`

**Output:** `data/output/{year}/`
- `1040-filled.pdf`
- `schedule-a-filled.pdf`
- `REVIEW.md`
- `instructions-complete.json`

---

## Configuration (`config.yaml`)

```yaml
# Directory paths
paths:
  raw_documents: "data/raw"
  extracted_data: "data/extracted"
  sanitized_data: "data/sanitized"
  vault: "data/vault"
  instructions: "data/instructions"
  output: "data/output"
  blank_forms: "templates/blank-forms"

# Ollama (fast extraction)
ollama:
  model: "llama3.2"
  base_url: "http://localhost:11434"
  temperature: 0.1

# Claude API (processing)
claude_api:
  model: "claude-sonnet-4-20250514"
  max_tokens: 8192
  temperature: 0.2

# Local LLM server (llama.cpp)
local_llm_server:
  base_url: "http://localhost:8080/v1"
  model: "qwen3-235b"
  max_tokens: 8192
  temperature: 0.2
  timeout: 300

# Extraction overrides for local backend
extraction:
  temperature: 0.1
  max_tokens: 4096
  timeout: 180

# Sensitive data patterns
sensitive_patterns:
  ssn:
    pattern: '\b\d{3}-\d{2}-\d{4}\b'
  ein:
    pattern: '\b\d{2}-\d{7}\b'
  account_number:
    pattern: '\b\d{8,17}\b'

# Document type detection
document_types:
  w2:
    keywords: ["Wage and Tax Statement", "W-2", "Form W-2"]
    fields: ["wages", "federal_withheld", "social_security_wages"]
  1099_int:
    keywords: ["1099-INT", "Interest Income"]
    fields: ["interest_income", "federal_withheld"]
  # ... etc
```

---

## Command Line Interface

### Full Pipeline (orchestrate.py)

```bash
# Default: Ollama extraction + Claude processing
python scripts/orchestrate.py --year 2025

# 100% Offline: Local LLM for everything
python scripts/orchestrate.py --year 2025 \
    --extraction-backend local \
    --backend local

# Skip steps (use cached data)
python scripts/orchestrate.py --year 2025 \
    --skip-extract \
    --skip-sanitize

# Non-interactive (for automation)
VAULT_PASSPHRASE="secret" python scripts/orchestrate.py --year 2025 \
    --non-interactive

# Flatten final PDFs
python scripts/orchestrate.py --year 2025 --flatten
```

### Individual Scripts

```bash
# Extract only
python scripts/extract.py \
    --input data/raw/2025 \
    --output data/extracted/2025.json \
    --extraction-backend local

# Sanitize only
python scripts/sanitize.py \
    --input data/extracted/2025.json \
    --output data/sanitized/2025.json \
    --vault data/vault/2025.age

# Process only
python scripts/process.py \
    --input data/sanitized/2025.json \
    --output data/instructions/2025.json \
    --backend local

# Assemble only
python scripts/assemble.py \
    --instructions data/instructions/2025.json \
    --vault data/vault/2025.age \
    --templates templates/blank-forms \
    --output data/output/2025
```

---

## Backend Comparison

### Extraction Backends

| Backend | Command | Model | Speed | Best For |
|---------|---------|-------|-------|----------|
| Ollama | `--extraction-backend ollama` | Llama 3.2 (8B) | ~50 tok/s | Simple, clear documents |
| Local | `--extraction-backend local` | Qwen3-235B | ~10 tok/s | Complex, ambiguous documents |

### Processing Backends

| Backend | Command | Model | Speed | Privacy |
|---------|---------|-------|-------|---------|
| Claude | `--backend claude` | Claude Sonnet | ~80 tok/s | Sanitized data sent to API |
| Local | `--backend local` | Qwen3-235B | ~10 tok/s | 100% on-device |

### Recommended Configurations

| Use Case | Extraction | Processing | Command |
|----------|------------|------------|---------|
| **Fast + Quality** | Ollama | Claude | `--extraction-backend ollama --backend claude` |
| **100% Offline** | Local | Local | `--extraction-backend local --backend local` |
| **Max Privacy** | Local | Local | `--extraction-backend local --backend local` |
| **Budget** | Ollama | Local | `--extraction-backend ollama --backend local` |

---

## Hardware Requirements

### Minimum (Ollama + Claude API)
- 16GB RAM
- Any modern CPU
- Internet connection for Claude API

### Recommended (Full Local with Qwen3-235B)
- **AMD Ryzen AI MAX+ 395** with 128GB unified memory
- Or equivalent with 96GB+ VRAM/unified memory
- ~100GB storage for model files

### Model Sizing Guide

| Model | Quantization | Size | Min Memory |
|-------|--------------|------|------------|
| Llama 3.2 8B | Q4_K_M | ~5GB | 8GB |
| Qwen2.5-72B | Q4_K_M | ~45GB | 64GB |
| Qwen3-235B-A22B | Q2_K_XL | ~90GB | 128GB |

---

## Security Model

### What Stays Local (Never Leaves)
- Original PDF files
- Raw SSNs, EINs, account numbers
- Encrypted vault file
- Final filled forms
- Bank account information

### What May Be Sent Externally (if using Claude API)
- Document structure and amounts (sanitized)
- Names and addresses
- Tax calculations
- Tokens like `[SSN_REDACTED_1]` (not the actual values)

### What Is Never Sent
- Actual SSN digits
- Actual EIN digits
- Bank account/routing numbers
- Any data from the vault

---

## File Structure

```
tax-processor/
├── config.yaml                 # Configuration
├── requirements.txt            # Python dependencies
├── .gitignore                  # Ignores sensitive data dirs
│
├── scripts/
│   ├── extract.py              # PDF → JSON (Step 1)
│   ├── sanitize.py             # Remove sensitive data (Step 2)
│   ├── process.py              # Tax logic via LLM (Step 3)
│   ├── assemble.py             # Fill PDF forms (Step 4)
│   ├── orchestrate.py          # Run full pipeline
│   ├── tax_knowledge.py        # Tax knowledge base loader
│   └── start-local-llm.sh      # Helper to start llama.cpp
│
├── tax-knowledge/              # TAX REFERENCE DATA
│   └── 2025/
│       ├── tax-tables.json     # Brackets, limits, rates
│       ├── form-1040-fields.json   # PDF field → IRS line mapping
│       ├── schedule-a-fields.json  # Schedule A field mapping
│       └── tax-rules-summary.md    # Key rules and instructions
│
├── data/                       # ALL GITIGNORED
│   ├── raw/                    # Your original PDFs
│   │   ├── 2024/               # Prior year (for context)
│   │   └── 2025/               # Current year inputs
│   ├── extracted/              # JSON from extraction
│   ├── sanitized/              # JSON with redacted SSNs
│   ├── vault/                  # Encrypted sensitive data
│   ├── instructions/           # LLM output
│   └── output/                 # Final filled forms
│
└── templates/
    └── blank-forms/            # Blank IRS PDFs for current year
        ├── f1040.pdf
        ├── schedule-a.pdf
        └── ...
```

---

## Tax Knowledge Base

The tax knowledge base provides current-year tax information to the LLM,
which is critical when using the local backend (since the model may not
have up-to-date tax information in its training data).

### Structure

```
tax-knowledge/
└── {year}/
    ├── tax-tables.json         # Tax brackets, standard deductions, limits
    ├── form-{name}-fields.json # PDF field ID → IRS line mappings
    └── tax-rules-summary.md    # Key rules in readable format
```

### tax-tables.json

Contains all numerical values that change annually:

```json
{
  "tax_year": 2025,
  "standard_deductions": {
    "single": 15000,
    "married_filing_jointly": 30000
  },
  "tax_brackets": {
    "single": [
      { "min": 0, "max": 11925, "rate": 0.10 },
      { "min": 11925, "max": 48475, "rate": 0.12 }
    ]
  },
  "retirement_contributions": {
    "401k_limit": 23500,
    "ira_limit": 7000
  },
  "deductions": {
    "salt_cap": 10000,
    "mortgage_interest_debt_limit": 750000
  }
}
```

### form-{name}-fields.json

Maps PDF form field IDs to IRS line numbers:

```json
{
  "form_id": "1040",
  "form_name": "U.S. Individual Income Tax Return",
  "tax_year": 2025,
  "field_mappings": {
    "income": {
      "f1_25": { "line": "1a", "type": "currency", "description": "Wages from W-2" },
      "f1_36": { "line": "2b", "type": "currency", "description": "Taxable interest" }
    }
  },
  "calculation_rules": {
    "line_9": "Sum of lines 1z, 2b, 3b, 4b, 5b, 6b, 7, 8",
    "line_15": "Line 11 minus line 14"
  }
}
```

### tax-rules-summary.md

Human-readable summary of key tax rules:

```markdown
# Tax Year 2025 - Key Rules

## Filing Requirements
| Status | Under 65 | 65+ |
|--------|----------|-----|
| Single | $15,000 | $17,000 |

## Standard Deduction vs Itemizing
- Single: $15,000
- Married Filing Jointly: $30,000

## Key Limits
- SALT Cap: $10,000
- Mortgage Interest: On debt up to $750,000
```

### How It's Used

When `process.py` runs with `--backend local`:

1. **Detect tax year** from sanitized data
2. **Load tax-knowledge/{year}/** files
3. **Determine forms needed** based on document types
4. **Build context** with relevant tables and field mappings
5. **Inject into system prompt** before sending to LLM

This ensures the local LLM has accurate, current-year information for:
- Tax calculations (correct brackets, rates, limits)
- Form field mapping (correct PDF field IDs)
- Rule application (current deduction limits, phase-outs)

### Adding a New Tax Year

To support a new tax year:

```bash
mkdir tax-knowledge/2026

# Copy and update from previous year
cp tax-knowledge/2025/*.json tax-knowledge/2026/
cp tax-knowledge/2025/*.md tax-knowledge/2026/

# Edit each file to update values for new year
# (Tax tables change annually based on IRS announcements)
```

Key sources for updates:
- **Tax tables:** IRS Revenue Procedure (published October/November)
- **Form field mappings:** IRS form PDFs (published December/January)
- **Rules:** IRS Instructions for Form 1040

---

## Dependencies

```
# PDF Processing
pymupdf          # PDF reading/writing
pypdf            # PDF form filling
pytesseract      # OCR wrapper
pdf2image        # PDF to image conversion
Pillow           # Image processing

# LLM Clients
ollama           # Ollama Python client
anthropic        # Claude API client
requests         # HTTP for llama.cpp server

# Data & Config
pyyaml           # Configuration parsing
pydantic         # Data validation

# Encryption
pyage            # Age encryption (vault)

# CLI & Display
click            # Command line interface
rich             # Pretty terminal output
```

---

## Error Handling

The pipeline includes checks for:

1. **Backend availability** - Verifies Ollama/llama.cpp/Claude API before starting
2. **SSN detection** - Refuses to send unsanitized data to external APIs
3. **JSON parsing** - Handles malformed LLM responses gracefully
4. **Vault encryption** - Falls back to basic encoding if `age` not installed
5. **Missing templates** - Reports which form templates are missing

---

## Extending the System

### Adding New Document Types

Edit `config.yaml`:
```yaml
document_types:
  1099_nec:
    keywords: ["1099-NEC", "Nonemployee Compensation"]
    fields: ["nonemployee_compensation", "federal_withheld"]
```

### Adding New Sensitive Patterns

Edit `config.yaml`:
```yaml
sensitive_patterns:
  credit_card:
    pattern: '\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b'
    replacement: "[CC_REDACTED_{id}]"
```

### Using a Different Local Model

1. Download model in GGUF format
2. Update `config.yaml`:
   ```yaml
   local_llm_server:
     model: "your-model-name"
   ```
3. Start server: `./scripts/start-local-llm.sh /path/to/model.gguf`
