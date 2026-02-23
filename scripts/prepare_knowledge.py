#!/usr/bin/env python3
"""
prepare-knowledge.py - Extract IRS form instructions into structured markdown

This script takes an IRS instruction PDF (e.g., i1040gi.pdf) and converts it
into a structured markdown file suitable for injection into LLM prompts.

Supports two backends:
  --backend claude : Use Claude API for extraction (default, best quality)
  --backend local  : Use local LLM via llama.cpp server

Usage:
    python scripts/prepare-knowledge.py \\
        --pdf ~/Downloads/i1040gi.pdf \\
        --form 1040 \\
        --year 2025 \\
        --backend claude
"""

import json
import sys
from pathlib import Path
from typing import Optional
import click
import yaml
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


def load_config() -> dict:
    """Load configuration from config.yaml"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {}


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


@click.command()
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
def main(
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
    """
    Extract IRS form instructions from PDF into structured markdown.
    
    This script processes IRS instruction PDFs (like i1040gi.pdf) and converts
    them into markdown files suitable for use as LLM context.
    
    Examples:
    
    \b
    # Process full 1040 instructions
    python scripts/prepare-knowledge.py \\
        --pdf ~/Downloads/i1040gi.pdf \\
        --form 1040 \\
        --year 2025
    
    \b
    # Process only line instructions (pages 12-67)
    python scripts/prepare-knowledge.py \\
        --pdf ~/Downloads/i1040gi.pdf \\
        --form 1040 \\
        --year 2025 \\
        --start-page 12 \\
        --end-page 67
    
    \b
    # Test with first 10 pages
    python scripts/prepare-knowledge.py \\
        --pdf ~/Downloads/i1040gi.pdf \\
        --form 1040 \\
        --year 2025 \\
        --max-pages 10
    """
    backend_display = "Claude API" if backend == "claude" else "Local LLM"
    
    console.print(f"[bold blue]IRS Instructions Extractor ({backend_display})[/bold blue]")
    console.print(f"Form: {form_name} | Year: {tax_year}\n")
    
    config = load_config()
    pdf_path = Path(pdf_path)
    
    # Determine output path
    if output_path is None:
        knowledge_dir = Path(__file__).parent.parent / "tax-knowledge" / str(tax_year)
        knowledge_dir.mkdir(parents=True, exist_ok=True)
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
    
    console.print(f"[green]✓ Extracted {len(all_pages)} pages[/green]")
    
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
            console.print(f"  ✓ Chunk {i+1} complete ({len(extracted)} chars)")
        except Exception as e:
            console.print(f"  ✗ Chunk {i+1} failed: {e}")
            extracted_sections.append(f"[Chunk {i+1} extraction failed: {e}]")
    
    # Combine into final document
    console.print(f"\n[cyan]Step 3: Creating final document...[/cyan]")
    final_doc = create_final_document(extracted_sections, form_name, tax_year, pdf_path)
    
    # Save output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        f.write(final_doc)
    
    console.print(f"[green]✓ Saved to {output_path}[/green]")
    console.print(f"[dim]  Total size: {len(final_doc):,} characters (~{len(final_doc)//4:,} tokens)[/dim]")
    
    # Summary
    console.print(f"\n[bold green]Done![/bold green]")
    console.print(f"\nTo use these instructions, the tax_knowledge.py loader will")
    console.print(f"automatically include them when processing Form {form_name}.")


if __name__ == "__main__":
    main()
