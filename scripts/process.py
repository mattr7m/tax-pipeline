#!/usr/bin/env python3
"""
process.py - Send sanitized data to LLM for tax processing

Supports two backends:
  --backend claude  : Use Claude API (default, requires ANTHROPIC_API_KEY)
  --backend local   : Use local LLM via llama.cpp server (OpenAI-compatible API)

When using --backend local, the script automatically loads tax knowledge
(tables, form field mappings, rules) to provide the LLM with current-year
tax information it may not have in its training data.

Only sanitized data (no SSNs, account numbers) is sent to either backend.
"""

import json
import re
import sys
from pathlib import Path
from typing import Optional
import click
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
import yaml
import requests

# Local imports
from tax_knowledge import TaxKnowledgeBase, load_knowledge_for_processing

console = Console()


def load_config() -> dict:
    """Load configuration from config.yaml"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


SYSTEM_PROMPT_BASE = """You are a tax preparation assistant. You help process tax data and determine:
1. Which IRS forms are needed
2. How to map extracted data to form fields
3. Tax calculations and logic
4. Potential issues or missing information

IMPORTANT RULES:
- The data you receive has been sanitized - SSNs and account numbers are replaced with tokens like [SSN_REDACTED_1]
- Keep these tokens in your output - they will be replaced with real values locally
- Use the EXACT PDF field IDs provided in the form field mappings (e.g., "f1_25" for Line 1a wages)
- Perform calculations using the tax tables and rates provided
- Flag any calculations that need verification
- Note any missing information that might be needed

Output your response as structured JSON with the following format:
{
    "tax_year": "2025",
    "filing_status": "...",
    "forms_needed": ["1040", "Schedule A", ...],
    "form_instructions": {
        "1040": {
            "fields": {
                "f1_01": {"value": "...", "source": "...", "line": "First name"},
                "f1_25": {"value": 95000, "source": "W-2 Box 1", "line": "1a"},
                ...
            },
            "calculations": [
                {"description": "...", "formula": "...", "result": ...}
            ]
        }
    },
    "warnings": ["...", ...],
    "missing_info": ["...", ...],
    "summary": {
        "total_income": ...,
        "total_deductions": ...,
        "taxable_income": ...,
        "total_tax": ...,
        "total_withheld": ...,
        "refund_or_owed": ...
    }
}"""


def build_system_prompt(tax_knowledge_context: str = "") -> str:
    """Build the system prompt, optionally including tax knowledge."""
    if not tax_knowledge_context:
        return SYSTEM_PROMPT_BASE
    
    return f"""{SYSTEM_PROMPT_BASE}

---

# TAX YEAR REFERENCE DATA

The following tax tables, form field mappings, and rules are for the CURRENT tax year.
Use these exact values for calculations and the exact field IDs for form filling.

{tax_knowledge_context}
"""


def build_user_prompt(
    sanitized_data: dict,
    prior_filed_context: Optional[dict],
    prior_sources_context: Optional[dict] = None,
    forms_needed: Optional[list] = None
) -> str:
    """Build the user prompt for tax processing."""

    doc_role = sanitized_data.get("document_role", "source_document")

    user_prompt = f"""Process the following tax data and provide form filling instructions.

## Current Year Source Documents (Sanitized)
These are the input documents (W-2s, 1099s, 1098s, etc.) for the current tax year:

```json
{json.dumps(sanitized_data, indent=2)}
```

"""

    if prior_filed_context:
        prior_role = prior_filed_context.get("document_role", "filed_return")
        user_prompt += f"""## Prior Year Filed Return (For Reference)
This is last year's filed tax return. Use it to:
- Carry forward relevant information (address, filing status, etc.)
- Identify recurring items and any significant changes
- Ensure consistency in how items are reported

```json
{json.dumps(prior_filed_context, indent=2)}
```

"""

    if prior_sources_context:
        user_prompt += f"""## Prior Year Source Documents (For Reference)
These are last year's W-2s, 1099s, and other input documents. Use them to:
- Compare income sources year-over-year (new employers, closed accounts, etc.)
- Identify missing documents (e.g., a 1099 received last year but not this year)
- Verify continuity of recurring income streams

```json
{json.dumps(prior_sources_context, indent=2)}
```

"""

    if forms_needed:
        user_prompt += f"""## Forms to Prepare
Based on the extracted data, prepare these forms: {', '.join(forms_needed)}

"""
    
    user_prompt += """## Instructions
1. Determine filing status based on source documents and prior year context
2. Map each data point from source documents to specific PDF form fields
3. Use the exact field IDs from the reference data (e.g., "f1_25" for Line 1a wages)
4. Perform all necessary calculations using the provided tax tables
5. Flag any discrepancies between current and prior year
6. Note any issues or missing information

IMPORTANT:
- Use current year source documents as the PRIMARY data for this year's return
- Use prior year filed return only for REFERENCE (carry-forward info, consistency checks)
- Use prior year source documents only for REFERENCE (year-over-year comparison, missing document detection)
- Use the exact PDF field IDs from the form field mappings provided

Return your analysis as structured JSON."""

    return user_prompt


def process_with_claude(
    sanitized_data: dict,
    prior_year_context: Optional[dict],
    config: dict,
    tax_knowledge_context: str = "",
    forms_needed: Optional[list] = None,
    prior_sources_context: Optional[dict] = None
) -> dict:
    """
    Send sanitized data to Claude API for tax processing.
    """
    try:
        import anthropic
    except ImportError:
        console.print("[red]Error: anthropic package not installed.[/red]")
        console.print("Install with: pip install anthropic")
        sys.exit(1)
    
    api_config = config.get("claude_api", {})
    
    client = anthropic.Anthropic()  # Uses ANTHROPIC_API_KEY env var
    
    system_prompt = build_system_prompt(tax_knowledge_context)
    user_prompt = build_user_prompt(sanitized_data, prior_year_context, prior_sources_context, forms_needed)

    console.print("[dim]Sending sanitized data to Claude API...[/dim]")
    
    message = client.messages.create(
        model=api_config.get("model", "claude-sonnet-4-20250514"),
        max_tokens=api_config.get("max_tokens", 8192),
        system=system_prompt,
        messages=[
            {"role": "user", "content": user_prompt}
        ]
    )
    
    # Extract response
    response_text = message.content[0].text
    
    return parse_json_response(response_text)


def process_with_local_llm(
    sanitized_data: dict,
    prior_year_context: Optional[dict],
    config: dict,
    tax_knowledge_context: str = "",
    forms_needed: Optional[list] = None,
    prior_sources_context: Optional[dict] = None
) -> dict:
    """
    Send sanitized data to local LLM server (llama.cpp with OpenAI-compatible API).
    
    Tax knowledge context is critical here since the local model may not have
    up-to-date tax information in its training data.
    """
    llm_config = config.get("local_llm_server", {})
    base_url = llm_config.get("base_url", "http://localhost:8080/v1")
    model_name = llm_config.get("model", "local-model")
    timeout = llm_config.get("timeout", 300)
    
    system_prompt = build_system_prompt(tax_knowledge_context)
    user_prompt = build_user_prompt(sanitized_data, prior_year_context, prior_sources_context, forms_needed)

    console.print(f"[dim]Sending sanitized data to local LLM at {base_url}...[/dim]")
    console.print(f"[dim]Model: {model_name} | This may take a few minutes...[/dim]")
    
    if tax_knowledge_context:
        context_tokens = len(tax_knowledge_context) // 4
        console.print(f"[dim]Tax knowledge context: ~{context_tokens:,} tokens[/dim]")
    
    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            json={
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": llm_config.get("temperature", 0.2),
                "max_tokens": llm_config.get("max_tokens", 8192),
            },
            timeout=timeout
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        console.print("[red]Error: Could not connect to local LLM server.[/red]")
        console.print(f"Make sure llama.cpp server is running at {base_url}")
        console.print("\nStart it with:")
        console.print("  ./llama-server --model <your-model.gguf> --port 8080")
        sys.exit(1)
    except requests.exceptions.Timeout:
        console.print("[red]Error: Request timed out.[/red]")
        console.print("Large models may need more time. Try increasing timeout in config.yaml")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        console.print(f"[red]Error: HTTP {e.response.status_code}[/red]")
        console.print(e.response.text)
        sys.exit(1)
    
    result = response.json()
    
    # Handle different response formats
    if "choices" in result:
        # OpenAI-compatible format
        response_text = result["choices"][0]["message"]["content"]
    elif "content" in result:
        # Direct content format
        response_text = result["content"]
    else:
        console.print(f"[yellow]Unexpected response format: {list(result.keys())}[/yellow]")
        response_text = str(result)
    
    return parse_json_response(response_text)


def parse_json_response(response_text: str) -> dict:
    """Parse JSON from LLM response, handling markdown code blocks."""
    try:
        # Handle markdown code blocks
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            # Try to find JSON block
            parts = response_text.split("```")
            for part in parts[1::2]:  # Check odd-indexed parts (inside code blocks)
                part = part.strip()
                if part.startswith("{"):
                    response_text = part
                    break
        
        return json.loads(response_text.strip())
    except json.JSONDecodeError as e:
        console.print(f"[yellow]Warning: Could not parse response as JSON[/yellow]")
        console.print(f"[dim]Error: {e}[/dim]")
        return {
            "raw_response": response_text,
            "parse_error": str(e)
        }


def display_results(results: dict):
    """Display processing results in a readable format."""
    
    # Check for parse errors
    if "parse_error" in results:
        console.print("[yellow]⚠️  Response could not be parsed as JSON[/yellow]")
        console.print("\n[bold]Raw response:[/bold]")
        console.print(results.get("raw_response", "")[:2000])
        return
    
    # Summary panel
    if "summary" in results:
        summary = results["summary"]
        try:
            summary_text = f"""
**Total Income:** ${summary.get('total_income', 0):,.2f}
**Total Deductions:** ${summary.get('total_deductions', 0):,.2f}
**Taxable Income:** ${summary.get('taxable_income', 0):,.2f}
**Total Tax:** ${summary.get('total_tax', 0):,.2f}
**Total Withheld:** ${summary.get('total_withheld', 0):,.2f}
**Refund/Owed:** ${summary.get('refund_or_owed', 0):,.2f}
"""
        except (TypeError, ValueError):
            summary_text = f"```\n{json.dumps(summary, indent=2)}\n```"
        
        console.print(Panel(Markdown(summary_text), title="Tax Summary"))
    
    # Forms needed
    if "forms_needed" in results:
        console.print("\n[bold]Forms Required:[/bold]")
        for form in results["forms_needed"]:
            console.print(f"  • {form}")
    
    # Warnings
    if results.get("warnings"):
        console.print("\n[bold yellow]⚠️  Warnings:[/bold yellow]")
        for warning in results["warnings"]:
            console.print(f"  • {warning}")
    
    # Missing info
    if results.get("missing_info"):
        console.print("\n[bold red]❓ Missing Information:[/bold red]")
        for item in results["missing_info"]:
            console.print(f"  • {item}")


def verify_sanitized(data: dict) -> bool:
    """Verify that data has been sanitized (no raw SSNs)."""
    data_str = json.dumps(data)
    ssn_pattern = r'\b\d{3}-\d{2}-\d{4}\b'
    
    matches = re.findall(ssn_pattern, data_str)
    if matches:
        console.print("[bold red]ERROR: Data appears to contain SSNs![/bold red]")
        console.print(f"Found {len(matches)} potential SSN(s)")
        console.print("Run sanitize.py first before processing.")
        return False
    
    return True


def determine_tax_year(sanitized_data: dict) -> int:
    """Determine tax year from sanitized data."""
    # Check explicit tax_year field
    if "tax_year" in sanitized_data:
        try:
            return int(sanitized_data["tax_year"])
        except (TypeError, ValueError):
            pass
    
    # Check documents
    for doc in sanitized_data.get("documents", []):
        if "tax_year" in doc:
            try:
                return int(doc["tax_year"])
            except (TypeError, ValueError):
                pass
    
    # Default to current year
    from datetime import datetime
    return datetime.now().year


@click.command()
@click.option('--input', '-i', 'input_path', required=True,
              type=click.Path(exists=True), help='Sanitized source documents JSON')
@click.option('--output', '-o', 'output_path', required=True,
              type=click.Path(), help='Output instructions JSON')
@click.option('--prior-filed', '-p', 'prior_filed_path',
              type=click.Path(exists=True), help='Prior year filed return (sanitized) for context')
@click.option('--prior-sources', '-s', 'prior_sources_path',
              type=click.Path(exists=True), help='Prior year source documents (sanitized) for year-over-year comparison')
@click.option('--backend', '-b', 'backend',
              type=click.Choice(['claude', 'local'], case_sensitive=False),
              default='claude',
              help='LLM backend: "claude" for Claude API, "local" for local llama.cpp server')
@click.option('--tax-year', '-y', 'tax_year', type=int, default=None,
              help='Tax year (auto-detected from data if not specified)')
@click.option('--no-knowledge', is_flag=True,
              help='Skip loading tax knowledge base (not recommended for local backend)')
def main(
    input_path: str,
    output_path: str,
    prior_filed_path: Optional[str],
    prior_sources_path: Optional[str],
    backend: str,
    tax_year: Optional[int],
    no_knowledge: bool
):
    """
    Process sanitized tax data with LLM.
    
    Supports two backends:
    
    \b
    --backend claude : Use Claude API (requires ANTHROPIC_API_KEY env var)
    --backend local  : Use local LLM via llama.cpp server
    
    Input types:
    
    \b
    --input         : Sanitized source documents (W-2s, 1099s, etc.)
    --prior-filed   : Sanitized prior year filed return (for context)
    --prior-sources : Sanitized prior year source documents (for year-over-year comparison)
    
    When using --backend local, tax knowledge (tables, form mappings, rules)
    is automatically loaded to provide current-year context to the LLM.
    
    Only sanitized data (no SSNs or account numbers) is sent to the LLM.
    """
    backend_display = "Claude API" if backend == "claude" else "Local LLM (llama.cpp)"
    
    console.print(f"[bold blue]Tax Processor ({backend_display})[/bold blue]")
    console.print("Processing sanitized data - no sensitive info transmitted\n")
    
    config = load_config()
    input_path = Path(input_path)
    output_path = Path(output_path)
    
    # Load sanitized source documents
    with open(input_path) as f:
        sanitized_data = json.load(f)
    
    # Display what we're processing
    doc_role = sanitized_data.get("document_role", "unknown")
    doc_count = len(sanitized_data.get("documents", []))
    console.print(f"[dim]Input: {doc_count} documents (role: {doc_role})[/dim]")
    
    # Load prior year filed return if provided
    prior_filed_context = None
    if prior_filed_path:
        with open(prior_filed_path) as f:
            prior_filed_context = json.load(f)
        prior_role = prior_filed_context.get("document_role", "unknown")
        prior_count = len(prior_filed_context.get("documents", []))
        console.print(f"[dim]Prior filed: {prior_count} documents (role: {prior_role})[/dim]")

    # Load prior year source documents if provided
    prior_sources_context = None
    if prior_sources_path:
        with open(prior_sources_path) as f:
            prior_sources_context = json.load(f)
        prior_src_role = prior_sources_context.get("document_role", "unknown")
        prior_src_count = len(prior_sources_context.get("documents", []))
        console.print(f"[dim]Prior sources: {prior_src_count} documents (role: {prior_src_role})[/dim]")

    # Verify data is sanitized
    if not verify_sanitized(sanitized_data):
        sys.exit(1)
    if prior_filed_context and not verify_sanitized(prior_filed_context):
        sys.exit(1)
    if prior_sources_context and not verify_sanitized(prior_sources_context):
        sys.exit(1)
    
    console.print("[green]✓ Data verified as sanitized[/green]")
    
    # Determine tax year
    if tax_year is None:
        tax_year = determine_tax_year(sanitized_data)
    console.print(f"[dim]Tax year: {tax_year}[/dim]")
    
    # Load tax knowledge (especially important for local backend)
    tax_knowledge_context = ""
    forms_needed = None
    
    if not no_knowledge:
        knowledge_dir = Path(__file__).parent.parent / "tax-knowledge"
        
        if backend == "local":
            console.print("\n[cyan]Loading tax knowledge base (required for local LLM)...[/cyan]")
        else:
            console.print("\n[dim]Loading tax knowledge base...[/dim]")
        
        tax_knowledge_context, forms_needed = load_knowledge_for_processing(
            tax_year,
            sanitized_data,
            knowledge_dir,
            max_context_tokens=12000 if backend == "local" else 4000
        )
        
        if not tax_knowledge_context and backend == "local":
            console.print("[yellow]⚠️  No tax knowledge found for this year.[/yellow]")
            console.print("[yellow]   Local LLM may not have accurate tax information.[/yellow]")
            console.print("[yellow]   Consider adding knowledge to tax-knowledge/{tax_year}/[/yellow]")
    else:
        console.print("[yellow]Skipping tax knowledge base (--no-knowledge flag)[/yellow]")
    
    console.print("")
    
    # Process with selected backend
    try:
        if backend == "claude":
            results = process_with_claude(
                sanitized_data,
                prior_filed_context,
                config,
                tax_knowledge_context,
                forms_needed,
                prior_sources_context
            )
        else:
            results = process_with_local_llm(
                sanitized_data,
                prior_filed_context,
                config,
                tax_knowledge_context,
                forms_needed,
                prior_sources_context
            )
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    
    # Save results
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    
    console.print(f"[green]Instructions written to {output_path}[/green]\n")

    # Update dashboard — mark processing as complete and instructions as present
    try:
        from dashboard import load_state as _load_state, save_state as _save_state, regenerate_html
        project_root = Path(__file__).parent.parent
        _state = _load_state(project_root)
        _state.setdefault("status", {})["processing_complete"] = True
        for entry in _state.get("output", {}).get("current_instructions", []):
            entry.pop("placeholder", None)
        _save_state(project_root, _state)
        regenerate_html(project_root)
    except Exception:
        pass  # Dashboard update is non-critical

    # Display summary
    display_results(results)


if __name__ == "__main__":
    main()
