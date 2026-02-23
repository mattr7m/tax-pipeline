#!/usr/bin/env python3
"""
orchestrate.py - Run the complete tax processing pipeline

Coordinates all steps:
1. Extract data from PDFs (local LLM)
2. Sanitize sensitive data
3. Process with Claude API
4. Assemble final forms
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime
import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
import yaml

console = Console()


def load_config() -> dict:
    """Load configuration from config.yaml"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def check_prerequisites(backend: str = "claude", extraction_backend: str = "ollama"):
    """Check that all required tools are available."""
    issues = []
    
    # Check Ollama (if using for extraction)
    if extraction_backend == "ollama":
        try:
            import ollama
            ollama.list()
        except Exception:
            issues.append("Ollama not running - start with: ollama serve")
    
    # Check for API key (only if using Claude backend)
    if backend == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        issues.append("ANTHROPIC_API_KEY not set (required for --backend claude)")
    
    # Check for local LLM server (if using for extraction or processing)
    if backend == "local" or extraction_backend == "local":
        try:
            import requests
            response = requests.get("http://localhost:8080/health", timeout=5)
        except Exception:
            issues.append("Local LLM server not running at localhost:8080 - start llama.cpp server first")
    
    # Check for age (optional but recommended)
    import subprocess
    try:
        subprocess.run(["age", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        console.print("[yellow]Note: 'age' not found - using basic encoding for vault[/yellow]")
    
    # Check for PDF libraries
    try:
        import fitz
    except ImportError:
        try:
            from fillpdf import fillpdfs
        except ImportError:
            issues.append("No PDF library - install pymupdf or fillpdf")
    
    return issues


def run_step(step_name: str, command: list, env: dict = None) -> bool:
    """Run a pipeline step as a subprocess."""
    import subprocess
    
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    
    console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
    console.print(f"[bold cyan]Step: {step_name}[/bold cyan]")
    console.print(f"[bold cyan]{'='*60}[/bold cyan]\n")
    
    try:
        result = subprocess.run(
            command,
            env=full_env,
            cwd=Path(__file__).parent
        )
        return result.returncode == 0
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        return False


@click.command()
@click.option('--year', '-y', required=True, type=int, help='Tax year to process')
@click.option('--skip-extract', is_flag=True, help='Skip extraction (use existing)')
@click.option('--skip-sanitize', is_flag=True, help='Skip sanitization (use existing)')
@click.option('--skip-process', is_flag=True, help='Skip API processing (use existing)')
@click.option('--flatten/--no-flatten', default=False, help='Flatten final PDFs')
@click.option('--non-interactive', is_flag=True, help='Run without prompts')
@click.option('--backend', '-b', 'backend',
              type=click.Choice(['claude', 'local'], case_sensitive=False),
              default='claude',
              help='LLM backend for processing: "claude" for Claude API, "local" for llama.cpp server')
@click.option('--extraction-backend', '-e', 'extraction_backend',
              type=click.Choice(['ollama', 'local'], case_sensitive=False),
              default='ollama',
              help='LLM backend for extraction: "ollama" (fast) or "local" (same as processing)')
def main(
    year: int,
    skip_extract: bool,
    skip_sanitize: bool,
    skip_process: bool,
    flatten: bool,
    non_interactive: bool,
    backend: str,
    extraction_backend: str
):
    """
    Run the complete tax processing pipeline.
    
    This orchestrates all steps from raw PDFs to filled forms.
    """
    backend_display = "Claude API" if backend == "claude" else "Local LLM (llama.cpp)"
    extraction_display = "Ollama" if extraction_backend == "ollama" else "Local LLM (llama.cpp)"
    
    console.print(Panel.fit(
        f"[bold blue]Tax Processing Pipeline - {year}[/bold blue]\n\n"
        f"Extraction Backend: [cyan]{extraction_display}[/cyan]\n"
        f"Processing Backend: [cyan]{backend_display}[/cyan]\n\n"
        "This will:\n"
        f"1. Extract data from your PDFs (via {extraction_display})\n"
        "2. Sanitize sensitive data (SSNs, account numbers)\n"
        f"3. Process with {backend_display} (sanitized data only)\n"
        "4. Assemble final forms (locally)\n",
        title="Welcome"
    ))
    
    # Check prerequisites
    issues = check_prerequisites(backend, extraction_backend)
    if issues:
        console.print("[bold red]Prerequisites not met:[/bold red]")
        for issue in issues:
            console.print(f"  • {issue}")
        sys.exit(1)
    
    config = load_config()
    project_root = Path(__file__).parent.parent
    
    # Set up paths
    paths = {
        "raw_input": project_root / config["paths"]["raw_documents"] / str(year),
        "prior_year_raw": project_root / config["paths"]["raw_documents"] / str(year - 1),
        "extracted": project_root / config["paths"]["extracted_data"] / f"{year}.json",
        "prior_extracted": project_root / config["paths"]["extracted_data"] / f"{year - 1}.json",
        "sanitized": project_root / config["paths"]["sanitized_data"] / f"{year}.json",
        "prior_sanitized": project_root / config["paths"]["sanitized_data"] / f"{year - 1}.json",
        "vault": project_root / config["paths"]["vault"] / f"{year}.age",
        "instructions": project_root / config["paths"]["instructions"] / f"{year}.json",
        "output": project_root / config["paths"]["output"] / str(year),
        "templates": project_root / config["paths"]["blank_forms"],
    }
    
    # Verify input exists
    if not paths["raw_input"].exists():
        console.print(f"[red]Input directory not found: {paths['raw_input']}[/red]")
        console.print("\nCreate this directory and add your tax documents:")
        console.print(f"  mkdir -p {paths['raw_input']}")
        console.print(f"  # Add W-2s, 1099s, etc. as PDFs")
        sys.exit(1)
    
    # Check for prior year data
    has_prior_year = paths["prior_year_raw"].exists() or paths["prior_extracted"].exists()
    if has_prior_year:
        console.print(f"[green]✓ Found prior year ({year-1}) data for context[/green]")
    
    if not non_interactive:
        if not Confirm.ask("\nReady to proceed?"):
            console.print("Cancelled.")
            sys.exit(0)
        
        passphrase = Prompt.ask("Enter vault passphrase", password=True)
        passphrase_confirm = Prompt.ask("Confirm passphrase", password=True)
        
        if passphrase != passphrase_confirm:
            console.print("[red]Passphrases don't match![/red]")
            sys.exit(1)
    else:
        passphrase = os.environ.get("VAULT_PASSPHRASE", "")
        if not passphrase:
            console.print("[red]VAULT_PASSPHRASE env var required for non-interactive mode[/red]")
            sys.exit(1)
    
    # Create directories
    for path in [paths["extracted"], paths["sanitized"], paths["vault"], 
                 paths["instructions"], paths["output"]]:
        path.parent.mkdir(parents=True, exist_ok=True)
    
    # Track overall success
    success = True
    
    # Step 1: Extract (if prior year exists and not extracted, do that first)
    if has_prior_year and not paths["prior_extracted"].exists() and not skip_extract:
        console.print(f"\n[dim]Extracting prior year ({year-1}) for context...[/dim]")
        success = run_step(
            f"Extract Prior Year ({year-1})",
            [
                sys.executable, "extract.py",
                "--input", str(paths["prior_year_raw"]),
                "--output", str(paths["prior_extracted"]),
                "--extraction-backend", extraction_backend
            ]
        )
    
    if not skip_extract:
        cmd = [
            sys.executable, "extract.py",
            "--input", str(paths["raw_input"]),
            "--output", str(paths["extracted"]),
            "--extraction-backend", extraction_backend
        ]
        if paths["prior_extracted"].exists():
            cmd.extend(["--prior-year", str(paths["prior_extracted"])])
        
        extraction_name = "Ollama" if extraction_backend == "ollama" else "Local LLM"
        success = run_step(f"Extract {year} Documents ({extraction_name})", cmd) and success
    
    if not success:
        console.print("[red]Extraction failed. Stopping.[/red]")
        sys.exit(1)
    
    # Step 2: Sanitize
    if not skip_sanitize:
        # Sanitize prior year if needed
        if paths["prior_extracted"].exists() and not paths["prior_sanitized"].exists():
            run_step(
                f"Sanitize Prior Year ({year-1})",
                [
                    sys.executable, "sanitize.py",
                    "--input", str(paths["prior_extracted"]),
                    "--output", str(paths["prior_sanitized"]),
                    "--vault", str(paths["vault"].parent / f"{year-1}.age"),
                    "--passphrase", passphrase
                ]
            )
        
        success = run_step(
            f"Sanitize {year} Data",
            [
                sys.executable, "sanitize.py",
                "--input", str(paths["extracted"]),
                "--output", str(paths["sanitized"]),
                "--vault", str(paths["vault"]),
                "--passphrase", passphrase
            ]
        ) and success
    
    if not success:
        console.print("[red]Sanitization failed. Stopping.[/red]")
        sys.exit(1)
    
    # Step 3: Process with LLM
    if not skip_process:
        cmd = [
            sys.executable, "process.py",
            "--input", str(paths["sanitized"]),
            "--output", str(paths["instructions"]),
            "--backend", backend
        ]
        if paths["prior_sanitized"].exists():
            cmd.extend(["--prior-year", str(paths["prior_sanitized"])])
        
        backend_name = "Claude API" if backend == "claude" else "Local LLM"
        success = run_step(f"Process with {backend_name}", cmd) and success
    
    if not success:
        console.print("[red]API processing failed. Stopping.[/red]")
        sys.exit(1)
    
    # Step 4: Assemble
    cmd = [
        sys.executable, "assemble.py",
        "--instructions", str(paths["instructions"]),
        "--vault", str(paths["vault"]),
        "--templates", str(paths["templates"]),
        "--output", str(paths["output"]),
        "--passphrase", passphrase
    ]
    if flatten:
        cmd.append("--flatten")
    
    success = run_step("Assemble Final Forms", cmd) and success
    
    # Final summary
    console.print("\n" + "=" * 60)
    if success:
        console.print(Panel.fit(
            f"[bold green]✓ Pipeline Complete![/bold green]\n\n"
            f"Output directory: {paths['output']}\n\n"
            "Next steps:\n"
            "1. Review REVIEW.md for summary and warnings\n"
            "2. Check each filled PDF form\n"
            "3. Verify all numbers before filing\n",
            title="Success"
        ))
    else:
        console.print("[bold red]Pipeline completed with errors. Review output carefully.[/bold red]")
    
    # Security reminder
    console.print("\n[bold yellow]Security Reminders:[/bold yellow]")
    console.print("• Delete extracted/sanitized data when done")
    console.print("• Keep vault file secure (it contains your SSNs)")
    console.print("• Consider encrypting the output folder")


if __name__ == "__main__":
    main()
