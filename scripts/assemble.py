#!/usr/bin/env python3
"""
assemble.py - Re-inject sensitive data and fill PDF forms

Takes Claude's instructions and the encrypted vault, combines them,
and fills the actual PDF forms locally.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional
import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from config_loader import load_config

# PDF manipulation
try:
    import fitz  # pymupdf
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

try:
    from fillpdf import fillpdfs
    HAS_FILLPDF = True
except ImportError:
    HAS_FILLPDF = False


console = Console()


def decrypt_vault(vault_path: Path, passphrase: str) -> dict:
    """Decrypt the vault containing sensitive data."""
    try:
        result = subprocess.run(
            ["age", "-d", str(vault_path)],
            env={"AGE_PASSPHRASE": passphrase, **dict(__import__('os').environ)},
            capture_output=True,
            check=True
        )
        return json.loads(result.stdout.decode())
    except FileNotFoundError:
        # Fallback for base64 encoded files
        import base64
        encoded = vault_path.read_text()
        decoded = base64.b64decode(encoded).decode()
        return json.loads(decoded)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Decryption failed: {e.stderr.decode()}[/red]")
        raise


def rehydrate_data(instructions: dict, vault: dict) -> dict:
    """
    Replace redaction tokens with actual values from vault.
    
    Args:
        instructions: Instructions with tokens like [SSN_REDACTED_1]
        vault: Mapping of tokens to real values
        
    Returns:
        Instructions with real values
    """
    def replace_tokens(obj):
        if isinstance(obj, str):
            result = obj
            for token, value in vault.items():
                result = result.replace(token, value)
            return result
        elif isinstance(obj, dict):
            return {k: replace_tokens(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [replace_tokens(item) for item in obj]
        else:
            return obj
    
    return replace_tokens(instructions)


def get_pdf_form_fields(pdf_path: Path) -> dict:
    """Get all form fields from a PDF."""
    if HAS_FILLPDF:
        return fillpdfs.get_form_fields(str(pdf_path))
    elif HAS_PYMUPDF:
        doc = fitz.open(pdf_path)
        fields = {}
        for page in doc:
            for widget in page.widgets():
                if widget.field_name:
                    fields[widget.field_name] = widget.field_value
        doc.close()
        return fields
    else:
        console.print("[red]No PDF library available. Install fillpdf or pymupdf.[/red]")
        return {}


def fill_pdf_form(
    template_path: Path,
    output_path: Path,
    field_data: dict,
    flatten: bool = False
) -> bool:
    """
    Fill a PDF form with provided data.
    
    Args:
        template_path: Path to blank PDF form
        output_path: Path for filled output
        field_data: Dict mapping field names to values
        flatten: Whether to flatten (make non-editable)
        
    Returns:
        True if successful
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    if HAS_FILLPDF:
        try:
            fillpdfs.write_fillable_pdf(
                str(template_path),
                str(output_path),
                field_data,
                flatten=flatten
            )
            return True
        except Exception as e:
            console.print(f"[red]fillpdf error: {e}[/red]")
            return False
    
    elif HAS_PYMUPDF:
        try:
            doc = fitz.open(template_path)
            
            for page in doc:
                for widget in page.widgets():
                    if widget.field_name and widget.field_name in field_data:
                        widget.field_value = str(field_data[widget.field_name])
                        widget.update()
            
            if flatten:
                # Basic flatten - bake in field values
                for page in doc:
                    for widget in page.widgets():
                        widget.field_flags = 1  # Read-only
                        widget.update()
            
            doc.save(str(output_path))
            doc.close()
            return True
        except Exception as e:
            console.print(f"[red]pymupdf error: {e}[/red]")
            return False
    
    else:
        console.print("[red]No PDF library available[/red]")
        return False


def find_template(form_name: str, templates_dir: Path) -> Optional[Path]:
    """Find a template PDF for a given form name."""
    # Common naming patterns
    patterns = [
        f"{form_name.lower()}.pdf",
        f"f{form_name.lower()}.pdf",
        f"form-{form_name.lower()}.pdf",
        f"{form_name.lower().replace(' ', '-')}.pdf",
        f"{form_name.lower().replace(' ', '_')}.pdf",
    ]
    
    for pattern in patterns:
        path = templates_dir / pattern
        if path.exists():
            return path
    
    # Try glob
    matches = list(templates_dir.glob(f"*{form_name.lower()}*.pdf"))
    if matches:
        return matches[0]
    
    return None


def assemble_forms(
    instructions: dict,
    templates_dir: Path,
    output_dir: Path,
    flatten: bool = False
) -> list:
    """
    Fill all required forms based on instructions.
    
    Returns:
        List of generated form paths
    """
    generated = []
    form_instructions = instructions.get("form_instructions", {})
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        
        for form_name, form_data in form_instructions.items():
            task = progress.add_task(f"Filling {form_name}...", total=None)
            
            # Find template
            template_path = find_template(form_name, templates_dir)
            
            if not template_path:
                console.print(f"[yellow]Warning: Template not found for {form_name}[/yellow]")
                progress.remove_task(task)
                continue
            
            # Get field mappings
            fields = form_data.get("fields", {})
            field_data = {
                field_name: field_info.get("value", "")
                for field_name, field_info in fields.items()
                if isinstance(field_info, dict)
            }
            
            # Handle simple field mappings too
            for field_name, field_info in fields.items():
                if not isinstance(field_info, dict):
                    field_data[field_name] = str(field_info)
            
            # Fill form
            output_path = output_dir / f"{form_name.lower().replace(' ', '-')}-filled.pdf"
            
            if fill_pdf_form(template_path, output_path, field_data, flatten):
                generated.append(output_path)
                console.print(f"  [green]✓ Generated {output_path.name}[/green]")
            else:
                console.print(f"  [red]✗ Failed to generate {form_name}[/red]")
            
            progress.remove_task(task)
    
    return generated


def generate_review_document(
    instructions: dict,
    output_path: Path
):
    """Generate a human-readable review document."""
    
    lines = ["# Tax Return Review Document\n"]
    
    # Summary
    if "summary" in instructions:
        summary = instructions["summary"]
        lines.append("## Summary\n")
        lines.append(f"- **Total Income:** ${summary.get('total_income', 'N/A'):,.2f}")
        lines.append(f"- **Total Deductions:** ${summary.get('total_deductions', 'N/A'):,.2f}")
        lines.append(f"- **Taxable Income:** ${summary.get('taxable_income', 'N/A'):,.2f}")
        lines.append(f"- **Total Tax:** ${summary.get('total_tax', 'N/A'):,.2f}")
        lines.append(f"- **Total Withheld:** ${summary.get('total_withheld', 'N/A'):,.2f}")
        
        refund = summary.get('refund_or_owed', 0)
        if refund > 0:
            lines.append(f"- **Expected Refund:** ${refund:,.2f}")
        else:
            lines.append(f"- **Amount Owed:** ${abs(refund):,.2f}")
        lines.append("")
    
    # Forms
    if "forms_needed" in instructions:
        lines.append("## Forms Included\n")
        for form in instructions["forms_needed"]:
            lines.append(f"- {form}")
        lines.append("")
    
    # Warnings
    if instructions.get("warnings"):
        lines.append("## ⚠️ Warnings - Please Review\n")
        for warning in instructions["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")
    
    # Missing info
    if instructions.get("missing_info"):
        lines.append("## ❓ Missing Information\n")
        for item in instructions["missing_info"]:
            lines.append(f"- {item}")
        lines.append("")
    
    # Calculations
    lines.append("## Calculations\n")
    for form_name, form_data in instructions.get("form_instructions", {}).items():
        calcs = form_data.get("calculations", [])
        if calcs:
            lines.append(f"### {form_name}\n")
            for calc in calcs:
                desc = calc.get("description", "")
                formula = calc.get("formula", "")
                result = calc.get("result", "")
                lines.append(f"- {desc}: {formula} = {result}")
            lines.append("")
    
    lines.append("\n---\n")
    lines.append("*Review all entries before filing. This is not tax advice.*\n")
    
    output_path.write_text("\n".join(lines))


@click.command()
@click.option('--instructions', '-i', 'instructions_path', required=True,
              type=click.Path(exists=True), help='Claude instructions JSON')
@click.option('--vault', '-v', 'vault_path', required=True,
              type=click.Path(exists=True), help='Encrypted vault file')
@click.option('--templates', '-t', 'templates_dir', required=True,
              type=click.Path(exists=True), help='Directory with blank form templates')
@click.option('--output', '-o', 'output_dir', required=True,
              type=click.Path(), help='Output directory for filled forms')
@click.option('--flatten/--no-flatten', default=False,
              help='Flatten forms (make non-editable)')
@click.option('--passphrase', '-p', 'passphrase',
              prompt=True, hide_input=True,
              help='Vault decryption passphrase')
def main(
    instructions_path: str,
    vault_path: str,
    templates_dir: str,
    output_dir: str,
    flatten: bool,
    passphrase: str
):
    """
    Assemble final tax forms by combining Claude's instructions with real data.
    
    This step runs entirely locally - sensitive data stays on your machine.
    """
    console.print("[bold blue]Tax Form Assembler (Local)[/bold blue]")
    console.print("Re-injecting sensitive data and filling forms\n")
    
    instructions_path = Path(instructions_path)
    vault_path = Path(vault_path)
    templates_dir = Path(templates_dir)
    output_dir = Path(output_dir)
    
    # Load instructions
    with open(instructions_path) as f:
        instructions = json.load(f)
    
    # Decrypt vault
    console.print("[dim]Decrypting vault...[/dim]")
    try:
        vault = decrypt_vault(vault_path, passphrase)
        console.print(f"[green]✓ Vault decrypted ({len(vault)} items)[/green]")
    except Exception as e:
        console.print(f"[red]Failed to decrypt vault: {e}[/red]")
        sys.exit(1)
    
    # Rehydrate instructions with real data
    console.print("[dim]Restoring sensitive data...[/dim]")
    hydrated_instructions = rehydrate_data(instructions, vault)
    
    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Fill forms
    console.print("\n[bold]Filling PDF forms:[/bold]")
    generated = assemble_forms(
        hydrated_instructions,
        templates_dir,
        output_dir,
        flatten
    )
    
    # Generate review document
    review_path = output_dir / "REVIEW.md"
    generate_review_document(hydrated_instructions, review_path)
    
    # Save hydrated instructions for reference
    debug_path = output_dir / "instructions-complete.json"
    with open(debug_path, 'w') as f:
        json.dump(hydrated_instructions, f, indent=2)
    
    # Summary
    console.print("\n" + "=" * 50)
    console.print("[bold green]Assembly Complete![/bold green]")
    console.print(f"\nGenerated {len(generated)} forms in {output_dir}/")
    
    table = Table(show_header=True)
    table.add_column("File")
    table.add_column("Status")
    
    for path in generated:
        table.add_row(path.name, "[green]✓[/green]")
    
    table.add_row("REVIEW.md", "[blue]Review document[/blue]")
    
    console.print(table)
    
    console.print("\n[bold yellow]⚠️  Please review all forms before filing![/bold yellow]")

    # Update dashboard
    try:
        from dashboard import update_phase, regenerate_html
        project_root = Path(__file__).parent.parent
        update_phase(project_root, "output", "current_filed", generated)
        regenerate_html(project_root)
    except Exception:
        pass  # Dashboard update is non-critical


if __name__ == "__main__":
    main()
