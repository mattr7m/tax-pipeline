# Secure Tax Form Processing Pipeline

> [!NOTE]
> **This is not tax advice or professional tax software.** This is a personal
> automation project. The authors assume no liability for errors, omissions, or
> any consequences arising from its use. Always review all output with a
> qualified tax professional before filing. **Use entirely at your own risk.**

A hybrid local/cloud workflow for processing tax forms with maximum privacy.

## Architecture

```
Raw PDFs → Local LLM (extraction) → Sanitizer → LLM Processing → Local Assembly → Filled PDFs
```

**Sensitive data (SSNs, account numbers) never leaves your machine.**

Supports two LLM backends for the processing step:
- **Claude API** (`--backend claude`): Fast, high quality, requires API key
- **Local LLM** (`--backend local`): 100% offline, uses llama.cpp server

## Prerequisites

### Local Tools
```bash
# macOS
brew install ollama tesseract poppler age

# Linux
sudo apt-get install tesseract-ocr poppler-utils
# Install ollama from https://ollama.ai
# Install age from https://github.com/FiloSottile/age
```

### Python Environment
```bash
cd tax-processor
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Pull Local LLM for Extraction
```bash
ollama pull llama3.2
# Or for better performance with more RAM:
ollama pull llama3.2:70b
```

### Backend Setup

#### Option A: Claude API (--backend claude)
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

#### Option B: Local LLM (--backend local)

For AMD Ryzen AI MAX+ 395 with 128GB unified memory, we recommend:

```bash
# Install llama.cpp with ROCm support
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -B build -DGGML_HIP=ON -DAMDGPU_TARGETS="gfx1151"
cmake --build build --config Release -j16

# Download Qwen3-235B-A22B (best quality for your hardware)
pip install huggingface_hub hf_transfer
huggingface-cli download unsloth/Qwen3-235B-A22B-Instruct-2507-GGUF \
  --include "UD-Q2_K_XL/*" --local-dir ./models

# Start the server
./build/bin/llama-server \
  --model ./models/UD-Q2_K_XL/*.gguf \
  --n-gpu-layers 99 --flash-attn \
  --ctx-size 32768 --threads 16 \
  --host 0.0.0.0 --port 8080
```

Alternative models for different hardware:
- **Qwen2.5-72B-Instruct** (Q4_K_M, ~45GB) - Best speed/quality balance
- **Llama-4-Scout-109B** (Q4_K_XL, ~58GB) - Good with vision
- **DeepSeek-R1-70B** (Q4_K_M, ~42GB) - Strong reasoning

## Directory Structure

```
tax-processor/
├── config.yaml              # Configuration
├── requirements.txt         # Python dependencies
├── scripts/
│   ├── extract.py          # PDF → structured data (local)
│   ├── sanitize.py         # Remove sensitive data
│   ├── process.py          # LLM tax logic (Claude or local)
│   ├── assemble.py         # Re-inject data & fill forms
│   ├── orchestrate.py      # Run full pipeline
│   └── prepare_knowledge.py # Extract IRS instructions from PDF
├── data/
│   ├── raw/                # Your original PDFs (gitignored)
│   │   ├── 2024/
│   │   │   ├── sources/    # Prior year W-2s, 1099s (optional)
│   │   │   └── filed/      # Prior year filed return (for context)
│   │   │       └── 1040-filed.pdf
│   │   └── 2025/
│   │       ├── sources/    # Current year input documents
│   │       │   ├── w2-employer1.pdf
│   │       │   ├── 1099-int-bank.pdf
│   │       │   └── 1098-mortgage.pdf
│   │       └── filed/      # (Empty until you file)
│   ├── extracted/          # Structured JSON from extraction
│   │   ├── 2025-sources.json
│   │   └── 2024-filed.json
│   ├── sanitized/          # Cleaned data for LLM
│   ├── vault/              # Encrypted sensitive data
│   └── output/             # Final filled forms
├── tax-knowledge/          # Tax reference data (per year)
│   └── 2025/
│       ├── tax-tables.json
│       ├── form-1040-fields.json
│       └── form-1040-instructions.md
└── templates/
    └── blank-forms/        # Blank IRS forms for current year
```

### Document Organization

Each year has the same structure with `sources/` and `filed/` subdirectories:

| Directory | Contents | Purpose |
|-----------|----------|---------|
| `{year}/sources/` | W-2s, 1099s, 1098s | Input documents for that tax year |
| `{year}/filed/` | Completed 1040, schedules | Filed return (for reference/context) |

### Year Rollover Workflow

When a new tax year begins:

```bash
# After filing 2025 taxes, move your filed return:
cp ~/Downloads/1040-signed.pdf data/raw/2025/filed/

# Start 2026:
mkdir -p data/raw/2026/sources
mkdir -p data/raw/2026/filed

# Add 2026 documents to data/raw/2026/sources/
# The system will automatically use 2025/filed/ as prior year context
```

The orchestrator automatically:
1. Extracts from `{year}/sources/` as input documents
2. Extracts from `{year-1}/filed/` as prior year context (if present)
3. Passes both to the LLM with appropriate roles

## Usage

### Container

Pre-built images are available from GHCR:

```bash
podman pull ghcr.io/mattr7m/tax-pipeline:latest
```

Run with a local data directory mounted in:

```bash
podman run -it --rm \
  -e TAX_YEAR=2025 \
  -v ./data:/data/taxes/data:Z \
  -p 8000:8000 \
  ghcr.io/mattr7m/tax-pipeline:latest
```

To use the Claude API from inside the container:

```bash
podman run -it --rm \
  -e TAX_YEAR=2025 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -v ./data:/data/taxes/data:Z \
  -p 8000:8000 \
  ghcr.io/mattr7m/tax-pipeline:latest
```

To reach Ollama or a local LLM server running on the host:

```bash
podman run -it --rm \
  --network=host \
  -e TAX_YEAR=2025 \
  -v ./data:/data/taxes/data:Z \
  ghcr.io/mattr7m/tax-pipeline:latest
```

Build the image locally:

```bash
podman build -t tax-pipeline -f Containerfile .
```

### Full Pipeline (Claude API + Ollama extraction - default)
```bash
python scripts/orchestrate.py --year 2025
```

### Full Pipeline (100% Local - both extraction and processing)
```bash
python scripts/orchestrate.py --year 2025 --backend local --extraction-backend local
```

### Mixed Mode (Local extraction, Claude processing)
```bash
python scripts/orchestrate.py --year 2025 --backend claude --extraction-backend local
```

### Step by Step
```bash
# 1. Extract data from PDFs (choose backend)
python scripts/extract.py --input data/raw/2025 --output data/extracted/2025.json \
    --extraction-backend ollama  # or --extraction-backend local

# 2. Sanitize sensitive data
python scripts/sanitize.py --input data/extracted/2025.json \
    --output data/sanitized/2025.json \
    --vault data/vault/2025.age

# 3. Process with LLM (choose backend)
python scripts/process.py --input data/sanitized/2025.json \
    --output data/instructions/2025.json \
    --backend local  # or --backend claude

# 4. Assemble final forms
python scripts/assemble.py --instructions data/instructions/2025.json \
    --vault data/vault/2025.age \
    --templates templates/blank-forms \
    --output data/output/2025
```

## Backend Options

### Extraction Backends (--extraction-backend)

| Backend | Model | Speed | Use Case |
|---------|-------|-------|----------|
| `ollama` | Llama 3.2 (8B) | ~50 tok/s | Default, fast for simple docs |
| `local` | Qwen3-235B | ~10 tok/s | Complex/ambiguous documents |

### Processing Backends (--backend)

| Backend | Model | Speed | Use Case |
|---------|-------|-------|----------|
| `claude` | Claude Sonnet | ~80 tok/s | Best quality, requires API key |
| `local` | Qwen3-235B | ~10 tok/s | 100% offline, no API needed |

### Recommended Configurations

```bash
# Fast + High Quality (requires API key)
--extraction-backend ollama --backend claude

# 100% Offline (no external services)
--extraction-backend local --backend local

# Balanced (fast extraction, quality processing)
--extraction-backend ollama --backend local
```

## Security Notes

- Raw PDFs and vault files are gitignored
- Vault is encrypted with `age` (modern GPG alternative)
- Only sanitized data (no SSNs, account numbers) goes to any LLM
- All sensitive processing happens locally

## Tax Knowledge Base

When using `--backend local`, the system automatically loads current-year
tax knowledge to provide accurate information to the LLM. This is **critical**
for local LLMs since they may not have current tax information in their
training data.

The knowledge base is **not checked into the repo** — generate it locally
using `prepare_knowledge.py`, which extracts structured data from IRS
instruction PDFs:

```bash
# Generate Form 1040 knowledge from IRS instructions PDF
python scripts/prepare_knowledge.py --pdf ~/Downloads/i1040gi.pdf --form 1040 --year 2025 --backend claude

# Generate Schedule A knowledge
python scripts/prepare_knowledge.py --pdf ~/Downloads/i1040sca.pdf --form schedule-a --year 2025 --backend claude

# Use local LLM instead of Claude API
python scripts/prepare_knowledge.py --pdf ~/Downloads/i1040gi.pdf --form 1040 --year 2025 --backend local
```

This produces the following structure:

```
tax-knowledge/
└── 2025/
    ├── tax-tables.json             # Brackets, deductions, limits
    ├── form-1040-fields.json       # PDF field → line mappings
    ├── form-1040-instructions.md   # Line-by-line IRS guidance
    ├── schedule-a-fields.json      # Schedule A mappings
    └── tax-rules-summary.md        # Key rules reference
```

The knowledge base includes:

- Tax brackets and rates
- Standard deduction amounts
- Contribution limits (401k, IRA, HSA)
- SALT cap and other deduction limits
- PDF field ID to IRS line mappings
- Calculation rules

### Adding a New Tax Year

```bash
# Option 1: Generate fresh from IRS instruction PDFs (recommended)
python scripts/prepare_knowledge.py --pdf ~/Downloads/i1040gi-2026.pdf --form 1040 --year 2026 --backend claude

# Option 2: Copy previous year and update values manually
cp -r tax-knowledge/2025 tax-knowledge/2026
# Update values based on IRS Revenue Procedure announcements (Oct/Nov each year)
```

## License

MIT - Use at your own risk. Not tax advice.
