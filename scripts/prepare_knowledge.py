#!/usr/bin/env python3
"""
prepare_knowledge.py - Generate tax knowledge files from IRS PDFs

Subcommands:
  instructions  - Extract form instructions into structured markdown
  tax-tables    - Extract tax brackets, deductions, limits into JSON
  form-fields   - Map PDF field IDs to IRS line numbers
  rules-summary - Generate condensed tax rules reference
  all           - Run all generators in dependency order

Supports two backends:
  --backend claude : Use Claude API (default, best quality)
  --backend local  : Use local LLM via llama.cpp server

Usage:
    python scripts/prepare_knowledge.py instructions \\
        --pdf ~/Downloads/i1040gi.pdf --form 1040 --year 2025
    python scripts/prepare_knowledge.py tax-tables \\
        --pdf ~/Downloads/i1040gi.pdf --year 2025
    python scripts/prepare_knowledge.py all \\
        --instructions-pdf ~/Downloads/i1040gi.pdf \\
        --form-pdf data/templates/blank-forms/f1040.pdf \\
        --form 1040 --year 2025
"""

import json
import sys
from pathlib import Path
from typing import Optional
import click
import requests

# Make rich optional
try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn
    console = Console()
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    class DummyConsole:
        def print(self, msg, **kwargs):
            # Strip rich formatting
            import re
            clean = re.sub(r'\[/?[^\]]+\]', '', str(msg))
            print(clean)
    console = DummyConsole()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load configuration, delegating to config_loader."""
    try:
        from config_loader import load_config as _load
        return _load()
    except (ImportError, FileNotFoundError):
        return {}


def resolve_knowledge_output_dir(config: dict, tax_year: int) -> Path:
    """Resolve the tax-knowledge output directory for a given year."""
    tax_knowledge_base = config.get("paths", {}).get("tax_knowledge", "data/tax-knowledge")
    knowledge_dir = Path(__file__).parent.parent / tax_knowledge_base / str(tax_year)
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    return knowledge_dir


def parse_json_response(response_text: str) -> dict:
    """Parse JSON from LLM response, handling markdown code blocks."""
    try:
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            parts = response_text.split("```")
            for part in parts[1::2]:
                part = part.strip()
                if part.startswith("{"):
                    response_text = part
                    break
        return json.loads(response_text.strip())
    except json.JSONDecodeError as e:
        console.print(f"[yellow]Warning: Could not parse response as JSON[/yellow]")
        console.print(f"[dim]Error: {e}[/dim]")
        return {"raw_response": response_text, "parse_error": str(e)}


def send_to_claude(system_prompt: str, user_prompt: str, config: dict) -> str:
    """Send a prompt to Claude API and return the response text."""
    try:
        import anthropic
    except ImportError:
        console.print("[red]Error: anthropic package not installed.[/red]")
        console.print("Install with: pip install anthropic")
        sys.exit(1)

    api_config = config.get("claude_api", {})
    client = anthropic.Anthropic()

    message = client.messages.create(
        model=api_config.get("model", "claude-sonnet-4-20250514"),
        max_tokens=api_config.get("max_tokens", 8192),
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text


def send_to_local_llm(system_prompt: str, user_prompt: str, config: dict) -> str:
    """Send a prompt to local LLM server and return the response text."""
    llm_config = config.get("local_llm_server", {})
    base_url = llm_config.get("base_url", "http://localhost:8080/v1")
    timeout = llm_config.get("timeout", 300)

    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            json={
                "model": llm_config.get("model", "local-model"),
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
                "max_tokens": llm_config.get("max_tokens", 8192),
            },
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        console.print("[red]Error: Could not connect to local LLM server.[/red]")
        console.print(f"Make sure llama.cpp server is running at {base_url}")
        sys.exit(1)
    except requests.exceptions.Timeout:
        console.print("[yellow]Warning: Request timed out[/yellow]")
        return '{"error": "timeout"}'

    result = response.json()
    if "choices" in result:
        return result["choices"][0]["message"]["content"]
    elif "content" in result:
        return result["content"]
    else:
        return str(result)


def send_to_llm(system_prompt: str, user_prompt: str, backend: str, config: dict) -> str:
    """Route a prompt to the selected backend."""
    if backend == "claude":
        return send_to_claude(system_prompt, user_prompt, config)
    else:
        return send_to_local_llm(system_prompt, user_prompt, config)


def update_dashboard(output_path, tax_year: int):
    """Non-critical dashboard update after generating a knowledge file."""
    try:
        from dashboard import update_phase, regenerate_html as _regen_html
        project_root = Path(__file__).parent.parent
        knowledge_dir_name = Path(output_path).parent.name
        try:
            knowledge_year = int(knowledge_dir_name)
        except ValueError:
            knowledge_year = tax_year
        category = "current_knowledge" if knowledge_year == tax_year else "prior_knowledge"
        update_phase(project_root, "extracted_input", category, [output_path])
        _regen_html(project_root)
    except Exception:
        pass


def validate_tax_tables(data: dict) -> list[str]:
    """Validate tax tables JSON has required keys. Returns list of warnings."""
    warnings = []
    required = ["standard_deductions", "tax_brackets"]
    optional = ["tax_year", "retirement_contributions", "deductions", "credits"]
    for key in required:
        if key not in data:
            warnings.append(f"Missing required key: {key}")
    for key in optional:
        if key not in data:
            warnings.append(f"Missing optional key: {key}")
    return warnings


def extract_pdf_form_field_names(pdf_path: Path) -> list[dict]:
    """Extract form widget field names and types from a PDF using PyMuPDF."""
    try:
        import fitz
    except ImportError:
        console.print("[red]Error: PyMuPDF (fitz) not installed.[/red]")
        console.print("Install with: pip install PyMuPDF")
        sys.exit(1)

    fields = []
    doc = fitz.open(pdf_path)
    for page_num, page in enumerate(doc):
        for widget in page.widgets():
            if widget.field_name:
                fields.append({
                    "field_name": widget.field_name,
                    "field_type": widget.field_type_string,
                    "page": page_num + 1,
                })
    doc.close()
    return fields


# ---------------------------------------------------------------------------
# Existing helpers (unchanged)
# ---------------------------------------------------------------------------

def extract_text_from_pdf(pdf_path: Path, max_pages: Optional[int] = None) -> list[dict]:
    """
    Extract text from PDF, page by page.

    Returns:
        List of dicts with 'page' and 'text' keys
    """
    try:
        import pdfplumber
    except ImportError:
        console.print("[red]Error: pdfplumber not installed.[/red]")
        console.print("Install with: pip install pdfplumber")
        sys.exit(1)

    pages = []

    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        if max_pages:
            total_pages = min(total_pages, max_pages)

        console.print(f"[dim]Extracting text from {total_pages} pages...[/dim]")

        for i in range(total_pages):
            page = pdf.pages[i]
            text = page.extract_text() or ""

            # Clean up the text
            # Remove IRS header/footer boilerplate
            lines = text.split('\n')
            cleaned_lines = []
            for line in lines:
                # Skip common boilerplate
                if any(skip in line for skip in [
                    'Page', 'of 126', 'Fileid:',
                    'MUST be removed before printing',
                    'prints on all proofs'
                ]):
                    continue
                cleaned_lines.append(line)

            cleaned_text = '\n'.join(cleaned_lines).strip()

            if cleaned_text:
                pages.append({
                    'page': i + 1,
                    'text': cleaned_text
                })

    return pages


def chunk_pages(pages: list[dict], chunk_size: int = 10) -> list[str]:
    """
    Combine pages into chunks for processing.

    Args:
        pages: List of page dicts
        chunk_size: Number of pages per chunk

    Returns:
        List of text chunks
    """
    chunks = []
    current_chunk = []

    for page in pages:
        current_chunk.append(f"[Page {page['page']}]\n{page['text']}")

        if len(current_chunk) >= chunk_size:
            chunks.append('\n\n'.join(current_chunk))
            current_chunk = []

    if current_chunk:
        chunks.append('\n\n'.join(current_chunk))

    return chunks


def identify_sections(pages: list[dict]) -> dict:
    """
    Identify key sections in the document based on content.

    Returns dict mapping section names to page ranges.
    """
    sections = {}
    current_section = None
    section_start = 0

    section_keywords = {
        'filing_requirements': ['Filing Requirements', 'Do You Have To File'],
        'filing_status': ['Filing Status', 'Single', 'Married Filing'],
        'income': ['Income', 'Line 1', 'Wages', 'Interest', 'Dividends'],
        'adjustments': ['Adjustments', 'Adjusted Gross Income'],
        'deductions': ['Deductions', 'Standard Deduction', 'Itemized'],
        'tax_credits': ['Tax and Credits', 'Child Tax Credit'],
        'payments': ['Payments', 'Withholding', 'Estimated Tax'],
        'refund': ['Refund', 'Amount You Owe', 'Direct Deposit'],
    }

    for page in pages:
        text = page['text'].lower()
        for section_name, keywords in section_keywords.items():
            if any(kw.lower() in text for kw in keywords):
                if current_section != section_name:
                    if current_section:
                        sections[current_section] = (section_start, page['page'] - 1)
                    current_section = section_name
                    section_start = page['page']

    if current_section:
        sections[current_section] = (section_start, pages[-1]['page'])

    return sections


def create_final_document(
    extracted_sections: list[str],
    form_name: str,
    tax_year: int,
    pdf_path: Path
) -> str:
    """Combine extracted sections into final markdown document."""

    header = f"""# Form {form_name} Instructions - Tax Year {tax_year}

> **Source:** {pdf_path.name}
> **Generated:** Extracted using LLM from official IRS instructions
> **Note:** Always verify against official IRS publications for accuracy

---

"""

    # Combine all sections
    body = "\n\n---\n\n".join(extracted_sections)

    footer = """

---

## References

- [IRS Form 1040 Instructions](https://www.irs.gov/instructions/i1040)
- [Publication 17 - Your Federal Income Tax](https://www.irs.gov/publications/p17)
- [IRS Forms and Publications](https://www.irs.gov/forms-instructions)

> **Disclaimer:** This is an extracted summary of IRS instructions.
> For official guidance, refer to the actual IRS publications.
"""

    return header + body + footer


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """You are a tax documentation expert. Your task is to extract and restructure IRS form instructions into a clear, concise markdown format.

IMPORTANT GUIDELINES:
1. Focus on LINE-BY-LINE INSTRUCTIONS - what goes on each line, what to include/exclude
2. Preserve specific dollar amounts, percentages, thresholds, and dates
3. Include important exceptions, special cases, and cross-references
4. Use clear markdown formatting with headers, bullet points, and tables where appropriate
5. Be concise but complete - remove verbose IRS language while keeping essential information
6. Keep references to other forms/schedules/publications when mentioned
7. Group related instructions together logically

OUTPUT FORMAT:
Use this structure for each line or section:

## Line X: [Description]

**What to enter:** [Brief explanation]

**Include:**
- [Item 1]
- [Item 2]

**Do NOT include:**
- [Exclusion 1]
- [Exclusion 2]

**Special cases:**
- [Special case 1]
- [Special case 2]

**References:** Form XXXX, Pub. XXX

---

For non-line-specific content (like filing requirements, definitions), use appropriate headers and organize logically."""


TAX_TABLES_SYSTEM_PROMPT = """You are a tax data extraction expert. Extract tax tables, brackets, deductions, limits, and credit amounts from IRS instructions into structured JSON.

You MUST output valid JSON matching this exact schema:

{
  "tax_year": <int>,
  "standard_deductions": {
    "single": <number>,
    "married_filing_jointly": <number>,
    "married_filing_separately": <number>,
    "head_of_household": <number>
  },
  "tax_brackets": {
    "single": [
      {"min": <number>, "max": <number or null for top bracket>, "rate": <decimal>, "base_tax": <number>}
    ],
    "married_filing_jointly": [...],
    "married_filing_separately": [...],
    "head_of_household": [...]
  },
  "retirement_contributions": {
    "401k_limit": <number>,
    "401k_catch_up_50_plus": <number>,
    "ira_limit": <number>,
    "ira_catch_up_50_plus": <number>
  },
  "deductions": {
    "salt_cap": <number>,
    "mortgage_interest_debt_limit": <number>,
    "medical_expense_agi_threshold": <decimal>
  },
  "credits": {
    "child_tax_credit": <number>,
    "earned_income_credit_max_3_plus_children": <number>
  }
}

IMPORTANT:
- Extract EXACT dollar amounts and percentages from the source text
- Use decimal rates (0.10 not 10%)
- For the top tax bracket, use null for "max"
- base_tax is the cumulative tax from lower brackets
- Only output the JSON object, no other text"""


FORM_FIELDS_SYSTEM_PROMPT = """You are a tax form field mapping expert. Given a list of PDF form field names extracted from an IRS form, map each field to its corresponding IRS line number, type, and description.

You MUST output valid JSON matching this schema:

{
  "form_id": "<form name>",
  "form_name": "<full form title>",
  "tax_year": <int>,
  "field_mappings": {
    "<category>": {
      "<field_name>": {
        "line": "<IRS line number>",
        "type": "<currency|text|checkbox|number>",
        "description": "<what this field contains>"
      }
    }
  },
  "calculation_rules": {
    "<line>": "<calculation description>"
  }
}

Categories should be logical groupings like: "personal_info", "income", "adjustments", "deductions", "tax_and_credits", "payments", "refund".

IMPORTANT:
- Map EVERY field to its correct IRS line number
- Use the exact field_name from the input (e.g., "f1_25")
- Types: "currency" for dollar amounts, "text" for names/addresses, "checkbox" for yes/no, "number" for non-dollar numbers
- calculation_rules should describe how computed lines are derived
- Only output the JSON object, no other text"""


RULES_SUMMARY_SYSTEM_PROMPT = """You are a tax rules expert. Create a concise markdown summary of key tax rules, requirements, and thresholds from IRS instructions.

The summary should cover:
1. **Filing Requirements** - income thresholds for filing, age-based rules
2. **Standard Deductions** - amounts by filing status, additional amounts for age/blindness
3. **Key Credits** - Child Tax Credit, Earned Income Credit, education credits with phase-outs
4. **SALT Cap** - state and local tax deduction limit
5. **Retirement Contribution Limits** - 401(k), IRA, catch-up contributions
6. **Key Thresholds** - AMT exemptions, capital gains rates, Medicare surtax
7. **Important Deadlines** - filing dates, extension dates

FORMAT:
Use clear markdown with headers, bullet points, and tables. Keep it concise — this is a quick reference, not the full instructions. Include specific dollar amounts and percentages.

Output ONLY the markdown content."""


# ---------------------------------------------------------------------------
# Chunk extractors (existing, kept for instructions subcommand)
# ---------------------------------------------------------------------------

def extract_chunk_with_claude(chunk: str, chunk_num: int, total_chunks: int, form_name: str, config: dict) -> str:
    """Process a chunk using Claude API."""
    try:
        import anthropic
    except ImportError:
        console.print("[red]Error: anthropic package not installed.[/red]")
        console.print("Install with: pip install anthropic")
        sys.exit(1)

    api_config = config.get("claude_api", {})
    client = anthropic.Anthropic()

    user_prompt = f"""Extract the IRS instructions from this section of the Form {form_name} instructions.

This is chunk {chunk_num} of {total_chunks}.

Convert the content into structured markdown following the guidelines in the system prompt.
Focus on extracting actionable instructions - what goes where, with specific rules and exceptions.

SOURCE TEXT:
---
{chunk}
---

Output the structured markdown:"""

    message = client.messages.create(
        model=api_config.get("model", "claude-sonnet-4-20250514"),
        max_tokens=api_config.get("max_tokens", 8192),
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_prompt}
        ]
    )

    return message.content[0].text


def extract_chunk_with_local_llm(chunk: str, chunk_num: int, total_chunks: int, form_name: str, config: dict) -> str:
    """Process a chunk using local LLM server."""
    llm_config = config.get("local_llm_server", {})
    base_url = llm_config.get("base_url", "http://localhost:8080/v1")
    timeout = llm_config.get("timeout", 300)

    user_prompt = f"""Extract the IRS instructions from this section of the Form {form_name} instructions.

This is chunk {chunk_num} of {total_chunks}.

Convert the content into structured markdown following the guidelines in the system prompt.
Focus on extracting actionable instructions - what goes where, with specific rules and exceptions.

SOURCE TEXT:
---
{chunk}
---

Output the structured markdown:"""

    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            json={
                "model": llm_config.get("model", "local-model"),
                "messages": [
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.1,
                "max_tokens": llm_config.get("max_tokens", 8192),
            },
            timeout=timeout
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        console.print("[red]Error: Could not connect to local LLM server.[/red]")
        console.print(f"Make sure llama.cpp server is running at {base_url}")
        sys.exit(1)
    except requests.exceptions.Timeout:
        console.print(f"[yellow]Warning: Chunk {chunk_num} timed out[/yellow]")
        return f"[Chunk {chunk_num} extraction timed out]"

    result = response.json()

    if "choices" in result:
        return result["choices"][0]["message"]["content"]
    elif "content" in result:
        return result["content"]
    else:
        return str(result)


# ---------------------------------------------------------------------------
# CLI group and subcommands
# ---------------------------------------------------------------------------

@click.group()
def cli():
    """Generate tax knowledge files from IRS PDFs.

    Subcommands produce files consumed by tax_knowledge.py for the local LLM
    backend.  Run 'all' to generate every file type in dependency order.
    """
    pass


@cli.command()
@click.option('--pdf', '-p', 'pdf_path', required=True,
              type=click.Path(exists=True), help='Path to IRS instruction PDF')
@click.option('--form', '-f', 'form_name', required=True,
              help='Form name (e.g., "1040", "schedule-a")')
@click.option('--year', '-y', 'tax_year', required=True, type=int,
              help='Tax year')
@click.option('--backend', '-b', 'backend',
              type=click.Choice(['claude', 'local'], case_sensitive=False),
              default='claude',
              help='LLM backend for extraction')
@click.option('--output', '-o', 'output_path', type=click.Path(),
              help='Output path (default: tax-knowledge/{year}/form-{name}-instructions.md)')
@click.option('--max-pages', type=int, default=None,
              help='Maximum pages to process (for testing)')
@click.option('--chunk-size', type=int, default=8,
              help='Pages per chunk (default: 8)')
@click.option('--start-page', type=int, default=1,
              help='Starting page number (1-indexed)')
@click.option('--end-page', type=int, default=None,
              help='Ending page number (1-indexed)')
def instructions(
    pdf_path: str,
    form_name: str,
    tax_year: int,
    backend: str,
    output_path: Optional[str],
    max_pages: Optional[int],
    chunk_size: int,
    start_page: int,
    end_page: Optional[int]
):
    """Extract IRS form instructions from PDF into structured markdown.

    \b
    Examples:
      python scripts/prepare_knowledge.py instructions \\
          --pdf ~/Downloads/i1040gi.pdf --form 1040 --year 2025
      python scripts/prepare_knowledge.py instructions \\
          --pdf ~/Downloads/i1040gi.pdf --form 1040 --year 2025 \\
          --start-page 12 --end-page 67
    """
    backend_display = "Claude API" if backend == "claude" else "Local LLM"

    console.print(f"[bold blue]IRS Instructions Extractor ({backend_display})[/bold blue]")
    console.print(f"Form: {form_name} | Year: {tax_year}\n")

    config = load_config()
    pdf_path = Path(pdf_path)

    # Determine output path
    if output_path is None:
        knowledge_dir = resolve_knowledge_output_dir(config, tax_year)
        output_path = knowledge_dir / f"form-{form_name.lower()}-instructions.md"
    else:
        output_path = Path(output_path)

    # Extract text from PDF
    console.print(f"[cyan]Step 1: Extracting text from {pdf_path.name}...[/cyan]")
    all_pages = extract_text_from_pdf(pdf_path, max_pages)

    # Filter to requested page range
    if start_page > 1 or end_page is not None:
        end_idx = end_page if end_page else len(all_pages)
        all_pages = [p for p in all_pages if start_page <= p['page'] <= end_idx]
        console.print(f"[dim]Filtered to pages {start_page}-{end_idx}[/dim]")

    console.print(f"[green]Extracted {len(all_pages)} pages[/green]")

    # Chunk the pages
    console.print(f"\n[cyan]Step 2: Processing in chunks of {chunk_size} pages...[/cyan]")
    chunks = chunk_pages(all_pages, chunk_size)
    console.print(f"[dim]Created {len(chunks)} chunks to process[/dim]")

    # Process each chunk
    extracted_sections = []

    extract_fn = extract_chunk_with_claude if backend == "claude" else extract_chunk_with_local_llm

    for i, chunk in enumerate(chunks):
        console.print(f"Processing chunk {i+1}/{len(chunks)}...")

        try:
            extracted = extract_fn(chunk, i+1, len(chunks), form_name, config)
            extracted_sections.append(extracted)
            console.print(f"  Chunk {i+1} complete ({len(extracted)} chars)")
        except Exception as e:
            console.print(f"  Chunk {i+1} failed: {e}")
            extracted_sections.append(f"[Chunk {i+1} extraction failed: {e}]")

    # Combine into final document
    console.print(f"\n[cyan]Step 3: Creating final document...[/cyan]")
    final_doc = create_final_document(extracted_sections, form_name, tax_year, pdf_path)

    # Save output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        f.write(final_doc)

    console.print(f"[green]Saved to {output_path}[/green]")
    console.print(f"[dim]  Total size: {len(final_doc):,} characters (~{len(final_doc)//4:,} tokens)[/dim]")

    update_dashboard(output_path, tax_year)

    console.print(f"\n[bold green]Done![/bold green]")
    console.print(f"\nTo use these instructions, the tax_knowledge.py loader will")
    console.print(f"automatically include them when processing Form {form_name}.")


@cli.command("tax-tables")
@click.option('--pdf', '-p', 'pdf_path', required=True,
              type=click.Path(exists=True), help='Path to IRS instruction PDF')
@click.option('--year', '-y', 'tax_year', required=True, type=int,
              help='Tax year')
@click.option('--backend', '-b', 'backend',
              type=click.Choice(['claude', 'local'], case_sensitive=False),
              default='claude', help='LLM backend')
@click.option('--output', '-o', 'output_path', type=click.Path(),
              help='Output path (default: tax-knowledge/{year}/tax-tables.json)')
def tax_tables(pdf_path: str, tax_year: int, backend: str, output_path: Optional[str]):
    """Extract tax brackets, deductions, and limits into JSON.

    \b
    Example:
      python scripts/prepare_knowledge.py tax-tables \\
          --pdf ~/Downloads/i1040gi.pdf --year 2025
    """
    backend_display = "Claude API" if backend == "claude" else "Local LLM"
    console.print(f"[bold blue]Tax Tables Extractor ({backend_display})[/bold blue]")
    console.print(f"Year: {tax_year}\n")

    config = load_config()
    pdf_path = Path(pdf_path)

    if output_path is None:
        knowledge_dir = resolve_knowledge_output_dir(config, tax_year)
        output_path = knowledge_dir / "tax-tables.json"
    else:
        output_path = Path(output_path)

    # Extract text and filter to tax-table-relevant pages
    console.print("[cyan]Extracting text from PDF...[/cyan]")
    pages = extract_text_from_pdf(pdf_path)

    table_keywords = [
        "standard deduction", "tax rate", "tax table", "tax bracket",
        "tax computation", "contribution limit", "filing requirement",
        "earned income credit", "child tax credit",
    ]
    relevant_pages = [
        p for p in pages
        if any(kw in p["text"].lower() for kw in table_keywords)
    ]
    if not relevant_pages:
        relevant_pages = pages  # fall back to all pages

    console.print(f"[dim]Found {len(relevant_pages)} relevant pages[/dim]")

    combined_text = "\n\n".join(
        f"[Page {p['page']}]\n{p['text']}" for p in relevant_pages
    )

    user_prompt = f"""Extract all tax tables, brackets, standard deductions, contribution limits, and credit amounts for tax year {tax_year} from the following IRS instruction text.

SOURCE TEXT:
---
{combined_text}
---

Output the JSON object:"""

    console.print("[cyan]Sending to LLM for extraction...[/cyan]")
    response_text = send_to_llm(TAX_TABLES_SYSTEM_PROMPT, user_prompt, backend, config)
    data = parse_json_response(response_text)

    if "parse_error" in data:
        console.print("[red]Failed to parse LLM response as JSON[/red]")
        console.print(f"[dim]{data.get('raw_response', '')[:500]}[/dim]")
        sys.exit(1)

    # Ensure tax_year is set
    data.setdefault("tax_year", tax_year)

    # Validate
    warnings = validate_tax_tables(data)
    for w in warnings:
        console.print(f"[yellow]Warning: {w}[/yellow]")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

    console.print(f"[green]Saved to {output_path}[/green]")
    update_dashboard(output_path, tax_year)
    console.print("[bold green]Done![/bold green]")


@cli.command("form-fields")
@click.option('--form-pdf', '-p', 'form_pdf_path', required=True,
              type=click.Path(exists=True),
              help='Path to blank form PDF template')
@click.option('--form', '-f', 'form_name', required=True,
              help='Form name (e.g., "1040", "schedule-a")')
@click.option('--year', '-y', 'tax_year', required=True, type=int,
              help='Tax year')
@click.option('--backend', '-b', 'backend',
              type=click.Choice(['claude', 'local'], case_sensitive=False),
              default='claude', help='LLM backend')
@click.option('--output', '-o', 'output_path', type=click.Path(),
              help='Output path (default: tax-knowledge/{year}/form-{name}-fields.json)')
def form_fields(form_pdf_path: str, form_name: str, tax_year: int, backend: str, output_path: Optional[str]):
    """Map PDF field IDs to IRS line numbers.

    \b
    Example:
      python scripts/prepare_knowledge.py form-fields \\
          --form-pdf data/templates/blank-forms/f1040.pdf \\
          --form 1040 --year 2025
    """
    backend_display = "Claude API" if backend == "claude" else "Local LLM"
    console.print(f"[bold blue]Form Fields Mapper ({backend_display})[/bold blue]")
    console.print(f"Form: {form_name} | Year: {tax_year}\n")

    config = load_config()
    form_pdf_path = Path(form_pdf_path)

    if output_path is None:
        knowledge_dir = resolve_knowledge_output_dir(config, tax_year)
        output_path = knowledge_dir / f"form-{form_name.lower()}-fields.json"
    else:
        output_path = Path(output_path)

    # Extract field names from the PDF
    console.print("[cyan]Extracting form field names from PDF...[/cyan]")
    fields = extract_pdf_form_field_names(form_pdf_path)
    console.print(f"[dim]Found {len(fields)} form fields[/dim]")

    if not fields:
        console.print("[yellow]No form fields found in PDF[/yellow]")
        sys.exit(1)

    field_list_text = json.dumps(fields, indent=2)

    # Load existing instructions as extra context if available
    instructions_context = ""
    instructions_path = output_path.parent / f"form-{form_name.lower()}-instructions.md"
    if instructions_path.exists():
        instructions_text = instructions_path.read_text()
        # Truncate to avoid oversized prompts
        if len(instructions_text) > 20000:
            instructions_text = instructions_text[:20000] + "\n... (truncated)"
        instructions_context = f"""

FORM INSTRUCTIONS (for context):
---
{instructions_text}
---"""

    user_prompt = f"""Map the following PDF form fields from IRS Form {form_name} (tax year {tax_year}) to their IRS line numbers.

PDF FORM FIELDS:
---
{field_list_text}
---
{instructions_context}

Output the JSON object:"""

    console.print("[cyan]Sending to LLM for mapping...[/cyan]")
    response_text = send_to_llm(FORM_FIELDS_SYSTEM_PROMPT, user_prompt, backend, config)
    data = parse_json_response(response_text)

    if "parse_error" in data:
        console.print("[red]Failed to parse LLM response as JSON[/red]")
        console.print(f"[dim]{data.get('raw_response', '')[:500]}[/dim]")
        sys.exit(1)

    # Ensure required keys
    data.setdefault("form_id", form_name)
    data.setdefault("tax_year", tax_year)

    if "field_mappings" not in data:
        console.print("[yellow]Warning: Missing 'field_mappings' in response[/yellow]")
    if "calculation_rules" not in data:
        console.print("[yellow]Warning: Missing 'calculation_rules' in response[/yellow]")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

    console.print(f"[green]Saved to {output_path}[/green]")
    update_dashboard(output_path, tax_year)
    console.print("[bold green]Done![/bold green]")


@cli.command("rules-summary")
@click.option('--pdf', '-p', 'pdf_path', required=True,
              type=click.Path(exists=True), help='Path to IRS instruction PDF')
@click.option('--year', '-y', 'tax_year', required=True, type=int,
              help='Tax year')
@click.option('--backend', '-b', 'backend',
              type=click.Choice(['claude', 'local'], case_sensitive=False),
              default='claude', help='LLM backend')
@click.option('--output', '-o', 'output_path', type=click.Path(),
              help='Output path (default: tax-knowledge/{year}/tax-rules-summary.md)')
def rules_summary(pdf_path: str, tax_year: int, backend: str, output_path: Optional[str]):
    """Generate a condensed tax rules reference in markdown.

    \b
    Example:
      python scripts/prepare_knowledge.py rules-summary \\
          --pdf ~/Downloads/i1040gi.pdf --year 2025
    """
    backend_display = "Claude API" if backend == "claude" else "Local LLM"
    console.print(f"[bold blue]Rules Summary Generator ({backend_display})[/bold blue]")
    console.print(f"Year: {tax_year}\n")

    config = load_config()
    pdf_path = Path(pdf_path)

    if output_path is None:
        knowledge_dir = resolve_knowledge_output_dir(config, tax_year)
        output_path = knowledge_dir / "tax-rules-summary.md"
    else:
        output_path = Path(output_path)

    # Extract text from PDF
    console.print("[cyan]Extracting text from PDF...[/cyan]")
    pages = extract_text_from_pdf(pdf_path)
    combined_text = "\n\n".join(
        f"[Page {p['page']}]\n{p['text']}" for p in pages
    )

    # Truncate if extremely long
    if len(combined_text) > 80000:
        combined_text = combined_text[:80000] + "\n... (truncated)"

    # Load existing tax-tables.json as structured context if available
    tables_context = ""
    tables_path = output_path.parent / "tax-tables.json"
    if tables_path.exists():
        tables_text = tables_path.read_text()
        tables_context = f"""

ALREADY EXTRACTED TAX TABLES (use these exact numbers):
---
{tables_text}
---"""

    user_prompt = f"""Create a concise tax rules summary for tax year {tax_year} from the following IRS instruction text.
{tables_context}

SOURCE TEXT:
---
{combined_text}
---

Output the markdown:"""

    console.print("[cyan]Sending to LLM for summarization...[/cyan]")
    response_text = send_to_llm(RULES_SUMMARY_SYSTEM_PROMPT, user_prompt, backend, config)

    # Write markdown directly (no JSON parsing)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(response_text)

    console.print(f"[green]Saved to {output_path}[/green]")
    console.print(f"[dim]  Size: {len(response_text):,} characters[/dim]")
    update_dashboard(output_path, tax_year)
    console.print("[bold green]Done![/bold green]")


@cli.command("all")
@click.option('--instructions-pdf', '-i', 'instructions_pdf', required=True,
              type=click.Path(exists=True),
              help='Path to IRS instruction PDF (e.g. i1040gi.pdf)')
@click.option('--form-pdf', '-p', 'form_pdf', required=True,
              type=click.Path(exists=True),
              help='Path to blank form PDF template (e.g. f1040.pdf)')
@click.option('--form', '-f', 'form_name', required=True,
              help='Form name (e.g., "1040")')
@click.option('--year', '-y', 'tax_year', required=True, type=int,
              help='Tax year')
@click.option('--backend', '-b', 'backend',
              type=click.Choice(['claude', 'local'], case_sensitive=False),
              default='claude', help='LLM backend')
def generate_all(instructions_pdf: str, form_pdf: str, form_name: str, tax_year: int, backend: str):
    """Run all generators in dependency order.

    \b
    Order: tax-tables -> instructions -> form-fields -> rules-summary

    \b
    Example:
      python scripts/prepare_knowledge.py all \\
          --instructions-pdf ~/Downloads/i1040gi.pdf \\
          --form-pdf data/templates/blank-forms/f1040.pdf \\
          --form 1040 --year 2025
    """
    console.print(f"[bold blue]Generate All Tax Knowledge[/bold blue]")
    console.print(f"Form: {form_name} | Year: {tax_year}\n")

    ctx = click.get_current_context()

    steps = [
        ("tax-tables", tax_tables, {
            "pdf_path": instructions_pdf,
            "tax_year": tax_year,
            "backend": backend,
        }),
        ("instructions", instructions, {
            "pdf_path": instructions_pdf,
            "form_name": form_name,
            "tax_year": tax_year,
            "backend": backend,
        }),
        ("form-fields", form_fields, {
            "form_pdf_path": form_pdf,
            "form_name": form_name,
            "tax_year": tax_year,
            "backend": backend,
        }),
        ("rules-summary", rules_summary, {
            "pdf_path": instructions_pdf,
            "tax_year": tax_year,
            "backend": backend,
        }),
    ]

    for step_num, (name, cmd, kwargs) in enumerate(steps, 1):
        console.print(f"\n{'='*60}")
        console.print(f"[bold]Step {step_num}/4: {name}[/bold]")
        console.print(f"{'='*60}\n")
        ctx.invoke(cmd, **kwargs)

    console.print(f"\n{'='*60}")
    console.print(f"[bold green]All knowledge files generated for {form_name} {tax_year}![/bold green]")
    console.print(f"{'='*60}")


# Keep 'main' as an alias so `from prepare_knowledge import main` still works
# (e.g. in orchestrate.py or other callers)
main = cli


if __name__ == "__main__":
    cli()
