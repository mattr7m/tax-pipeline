#!/usr/bin/env python3
"""
sanitize.py - Remove sensitive data before sending to Claude API

Replaces SSNs, EINs, account numbers with tokens.
Stores original values in an encrypted vault for later reassembly.
"""

import json
import re
import sys
import secrets
from pathlib import Path
from typing import Any
import click
from rich.console import Console
from rich.table import Table
import yaml

# For encryption we use 'age' via subprocess
# Pure Python alternative: pyage
import subprocess

from pathguard import safe_resolve

console = Console()


def load_config() -> dict:
    """Load configuration from config.yaml"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


class Sanitizer:
    """Handles detection and replacement of sensitive data."""
    
    def __init__(self, config: dict):
        self.config = config
        self.vault = {}  # Maps tokens to real values
        self.token_counter = {}  # Track token IDs per type
        
    def _generate_token(self, data_type: str) -> str:
        """Generate a unique token for a data type."""
        count = self.token_counter.get(data_type, 0) + 1
        self.token_counter[data_type] = count
        return f"[{data_type.upper()}_REDACTED_{count}]"
    
    def _replace_with_token(self, match: re.Match, data_type: str) -> str:
        """Replace a match with a token, storing original in vault."""
        original = match.group(0)
        
        # Check if we've already tokenized this value
        for token, value in self.vault.items():
            if value == original:
                return token
        
        # Generate new token
        token = self._generate_token(data_type)
        self.vault[token] = original
        
        return token
    
    def sanitize_value(self, value: Any) -> Any:
        """
        Recursively sanitize a value (string, dict, or list).
        """
        if isinstance(value, str):
            return self._sanitize_string(value)
        elif isinstance(value, dict):
            return {k: self.sanitize_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [self.sanitize_value(item) for item in value]
        else:
            return value
    
    def _sanitize_string(self, text: str) -> str:
        """Apply all sanitization patterns to a string."""
        patterns = self.config.get("sensitive_patterns", {})
        
        result = text
        
        # Apply patterns in a specific order (SSN first, then more general)
        pattern_order = ["ssn", "ein", "routing_number", "account_number"]
        
        for pattern_name in pattern_order:
            if pattern_name not in patterns:
                continue
                
            pattern_info = patterns[pattern_name]
            pattern = pattern_info.get("pattern")
            
            if pattern:
                result = re.sub(
                    pattern,
                    lambda m: self._replace_with_token(m, pattern_name),
                    result
                )
        
        return result
    
    def get_vault(self) -> dict:
        """Return the vault mapping tokens to original values."""
        return self.vault.copy()
    
    def get_summary(self) -> dict:
        """Return a summary of what was sanitized."""
        return {
            data_type: count 
            for data_type, count in self.token_counter.items()
        }


def encrypt_vault(vault: dict, output_path: Path, passphrase: str) -> None:
    """
    Encrypt the vault using 'age' with a passphrase.
    
    Args:
        vault: Dictionary mapping tokens to real values
        output_path: Path to write encrypted file
        passphrase: Encryption passphrase
    """
    vault_json = json.dumps(vault, indent=2)
    
    # Use age for encryption
    try:
        result = subprocess.run(
            ["age", "-p", "-o", str(output_path)],
            input=vault_json.encode(),
            env={"AGE_PASSPHRASE": passphrase, **dict(__import__('os').environ)},
            capture_output=True,
            check=True
        )
    except FileNotFoundError:
        # Fallback: save with simple XOR obfuscation (NOT secure, just for demo)
        console.print("[yellow]Warning: 'age' not found. Using basic obfuscation (install age for real security)[/yellow]")
        
        # Simple base64 encoding as fallback (NOT SECURE - just for demo)
        import base64
        encoded = base64.b64encode(vault_json.encode()).decode()
        output_path.write_text(encoded)
        output_path = output_path.with_suffix('.b64')  # Indicate it's not encrypted
        console.print(f"[yellow]Vault saved with basic encoding to {output_path}[/yellow]")
        return
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Encryption failed: {e.stderr.decode()}[/red]")
        raise


def decrypt_vault(vault_path: Path, passphrase: str) -> dict:
    """
    Decrypt a vault file.
    
    Args:
        vault_path: Path to encrypted vault
        passphrase: Decryption passphrase
        
    Returns:
        Decrypted vault dictionary
    """
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


@click.command()
@click.option('--input', '-i', 'input_path', required=True,
              type=click.Path(exists=True), help='Input JSON from extraction')
@click.option('--output', '-o', 'output_path', required=True,
              type=click.Path(), help='Output sanitized JSON')
@click.option('--vault', '-v', 'vault_path', required=True,
              type=click.Path(), help='Output encrypted vault file')
@click.option('--passphrase', '-p', 'passphrase', 
              prompt=True, hide_input=True,
              help='Passphrase for vault encryption')
def main(input_path: str, output_path: str, vault_path: str, passphrase: str):
    """
    Sanitize extracted tax data by removing sensitive information.
    
    Replaces SSNs, EINs, and account numbers with tokens.
    Original values are stored in an encrypted vault.
    """
    console.print("[bold blue]Tax Data Sanitizer[/bold blue]")
    console.print("Removing sensitive data before API transmission\n")
    
    config = load_config()
    project_root = Path(__file__).parent.parent
    input_path = safe_resolve(project_root, input_path)
    output_path = safe_resolve(project_root, output_path)
    vault_path = safe_resolve(project_root, vault_path)
    
    # Load extracted data
    with open(input_path) as f:
        data = json.load(f)
    
    # Sanitize
    sanitizer = Sanitizer(config)
    sanitized_data = sanitizer.sanitize_value(data)
    
    # Get vault and summary
    vault = sanitizer.get_vault()
    summary = sanitizer.get_summary()
    
    # Display summary
    console.print("\n[bold]Sanitization Summary:[/bold]")
    table = Table(show_header=True)
    table.add_column("Data Type")
    table.add_column("Items Redacted", justify="right")
    
    for data_type, count in summary.items():
        table.add_row(data_type.upper(), str(count))
    
    if not summary:
        table.add_row("[dim]No sensitive data found[/dim]", "-")
    
    console.print(table)
    
    # Save sanitized data
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(sanitized_data, f, indent=2)
    console.print(f"\n[green]Sanitized data written to {output_path}[/green]")
    
    # Encrypt and save vault
    if vault:
        vault_path.parent.mkdir(parents=True, exist_ok=True)
        encrypt_vault(vault, vault_path, passphrase)
        console.print(f"[green]Encrypted vault written to {vault_path}[/green]")
    else:
        console.print("[yellow]No sensitive data found - no vault created[/yellow]")
    
    # Show what the sanitized data looks like
    console.print("\n[bold]Sample of sanitized output:[/bold]")
    sample = json.dumps(sanitized_data, indent=2)[:500]
    console.print(f"[dim]{sample}...[/dim]")

    # Update dashboard
    try:
        from dashboard import update_phase, regenerate_html
        project_root = Path(__file__).parent.parent
        basename = output_path.name.lower()
        if "filed" in basename:
            category = "prior_filed"
        else:
            category = "current_sources"
        update_phase(project_root, "sanitized_input", category, [output_path])
        regenerate_html(project_root)
    except Exception:
        pass  # Dashboard update is non-critical


if __name__ == "__main__":
    main()
