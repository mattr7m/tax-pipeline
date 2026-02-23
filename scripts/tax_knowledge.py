#!/usr/bin/env python3
"""
tax_knowledge.py - Load and manage tax knowledge base

Provides functions to load tax tables, form field mappings, and rules
for injection into LLM prompts.
"""

import json
from pathlib import Path
from typing import Optional

# Make rich optional
try:
    from rich.console import Console
    console = Console()
except ImportError:
    class DummyConsole:
        def print(self, msg, **kwargs):
            import re
            clean = re.sub(r'\[/?[^\]]+\]', '', str(msg))
            print(clean)
    console = DummyConsole()


class TaxKnowledgeBase:
    """
    Manages tax knowledge for a specific tax year.
    
    Loads:
    - Tax tables (brackets, limits, rates)
    - Form field mappings (PDF field → IRS line)
    - Form instructions (line-by-line IRS guidance)
    - Tax rules summary (key instructions)
    """
    
    def __init__(self, tax_year: int, knowledge_dir: Optional[Path] = None):
        self.tax_year = tax_year
        
        if knowledge_dir is None:
            # Default to tax-knowledge directory relative to scripts
            knowledge_dir = Path(__file__).parent.parent / "tax-knowledge"
        
        self.knowledge_dir = knowledge_dir
        self.year_dir = knowledge_dir / str(tax_year)
        
        # Loaded data
        self._tax_tables = None
        self._form_mappings = {}
        self._form_instructions = {}
        self._rules_summary = None
        
    def is_available(self) -> bool:
        """Check if knowledge base exists for this tax year."""
        return self.year_dir.exists()
    
    def load_tax_tables(self) -> dict:
        """Load tax tables (brackets, deductions, limits)."""
        if self._tax_tables is not None:
            return self._tax_tables
        
        tables_file = self.year_dir / "tax-tables.json"
        if not tables_file.exists():
            console.print(f"[yellow]Warning: Tax tables not found for {self.tax_year}[/yellow]")
            return {}
        
        with open(tables_file) as f:
            self._tax_tables = json.load(f)
        
        return self._tax_tables
    
    def load_form_mapping(self, form_id: str) -> dict:
        """
        Load field mapping for a specific form.
        
        Args:
            form_id: Form identifier (e.g., "1040", "schedule_a")
            
        Returns:
            Form mapping dict or empty dict if not found
        """
        if form_id in self._form_mappings:
            return self._form_mappings[form_id]
        
        # Try different filename patterns
        patterns = [
            f"form-{form_id}-fields.json",
            f"{form_id}-fields.json",
            f"form-{form_id.lower()}-fields.json",
            f"{form_id.lower().replace('_', '-')}-fields.json",
        ]
        
        for pattern in patterns:
            mapping_file = self.year_dir / pattern
            if mapping_file.exists():
                with open(mapping_file) as f:
                    self._form_mappings[form_id] = json.load(f)
                return self._form_mappings[form_id]
        
        console.print(f"[yellow]Warning: Form mapping not found for {form_id}[/yellow]")
        return {}
    
    def load_form_instructions(self, form_id: str) -> str:
        """
        Load instructions for a specific form.
        
        Args:
            form_id: Form identifier (e.g., "1040", "schedule_a")
            
        Returns:
            Instructions markdown or empty string if not found
        """
        if form_id in self._form_instructions:
            return self._form_instructions[form_id]
        
        # Try different filename patterns
        patterns = [
            f"form-{form_id}-instructions.md",
            f"{form_id}-instructions.md",
            f"form-{form_id.lower()}-instructions.md",
            f"{form_id.lower().replace('_', '-')}-instructions.md",
        ]
        
        for pattern in patterns:
            instructions_file = self.year_dir / pattern
            if instructions_file.exists():
                with open(instructions_file) as f:
                    self._form_instructions[form_id] = f.read()
                console.print(f"[dim]Loaded instructions for {form_id} ({len(self._form_instructions[form_id])} chars)[/dim]")
                return self._form_instructions[form_id]
        
        # Instructions are optional, so just note they're not available
        console.print(f"[dim]Note: Form instructions not found for {form_id}[/dim]")
        return ""
    
    def load_rules_summary(self) -> str:
        """Load the tax rules summary markdown."""
        if self._rules_summary is not None:
            return self._rules_summary
        
        rules_file = self.year_dir / "tax-rules-summary.md"
        if not rules_file.exists():
            console.print(f"[yellow]Warning: Tax rules summary not found for {self.tax_year}[/yellow]")
            return ""
        
        with open(rules_file) as f:
            self._rules_summary = f.read()
        
        return self._rules_summary
    
    def get_forms_needed(self, extracted_data: dict) -> list:
        """
        Determine which forms are needed based on extracted data.
        
        Args:
            extracted_data: Data from extraction step
            
        Returns:
            List of form IDs that appear to be needed
        """
        forms = ["1040"]  # Always need 1040
        
        summary = extracted_data.get("summary", {})
        documents = extracted_data.get("documents", [])
        
        # Check for itemizable deductions
        deductions = summary.get("deductions", {})
        if deductions:
            mortgage_interest = deductions.get("mortgage_interest", 0)
            property_taxes = deductions.get("property_taxes", 0)
            
            # Get standard deduction (simplified check)
            tables = self.load_tax_tables()
            std_ded = tables.get("standard_deductions", {}).get("single", 15000)
            
            # If deductions might exceed standard, suggest Schedule A
            if (mortgage_interest + property_taxes) > std_ded * 0.5:
                forms.append("schedule_a")
        
        # Check document types
        doc_types = {doc.get("document_type") for doc in documents}
        
        if "1099_div" in doc_types or "1099_int" in doc_types:
            # May need Schedule B if interest/dividends exceed $1,500
            income = summary.get("income", {})
            if income.get("interest", 0) > 1500 or income.get("dividends", 0) > 1500:
                forms.append("schedule_b")
        
        # Add more detection logic as needed
        
        return forms
    
    def build_context_for_forms(self, form_ids: list, max_tokens: int = 8000, include_instructions: bool = True) -> str:
        """
        Build context string with relevant tax knowledge for specified forms.
        
        Args:
            form_ids: List of form IDs to include
            max_tokens: Approximate maximum tokens for context
            include_instructions: Whether to include form instructions
            
        Returns:
            Formatted context string
        """
        sections = []
        
        # Always include tax tables (compact)
        tables = self.load_tax_tables()
        if tables:
            compact_tables = self._compact_tax_tables(tables)
            sections.append(f"## Tax Year {self.tax_year} - Key Numbers\n\n{compact_tables}")
        
        # Include field mappings for each form
        for form_id in form_ids:
            mapping = self.load_form_mapping(form_id)
            if mapping:
                form_name = mapping.get("form_name", form_id)
                fields_section = self._format_field_mapping(mapping)
                sections.append(f"## {form_name} ({form_id.upper()}) Field Mapping\n\n{fields_section}")
        
        # Include form instructions if available and requested
        if include_instructions:
            for form_id in form_ids:
                instructions = self.load_form_instructions(form_id)
                if instructions:
                    # Truncate instructions if too long
                    max_instruction_chars = (max_tokens * 4) // len(form_ids) // 2
                    if len(instructions) > max_instruction_chars:
                        instructions = instructions[:max_instruction_chars] + "\n\n[... truncated for length ...]"
                    sections.append(f"## Form {form_id.upper()} Instructions\n\n{instructions}")
        
        # Include relevant portions of rules summary
        rules = self.load_rules_summary()
        if rules:
            # Extract most relevant sections based on forms
            relevant_rules = self._extract_relevant_rules(rules, form_ids)
            if relevant_rules:
                sections.append(f"## Key Tax Rules\n\n{relevant_rules}")
        
        context = "\n\n---\n\n".join(sections)
        
        # Rough token estimation (4 chars ≈ 1 token)
        estimated_tokens = len(context) // 4
        if estimated_tokens > max_tokens:
            # Truncate if needed (prefer keeping tables and field mappings)
            console.print(f"[yellow]Context truncated from ~{estimated_tokens} to ~{max_tokens} tokens[/yellow]")
            context = context[:max_tokens * 4]
        
        return context
    
    def _compact_tax_tables(self, tables: dict) -> str:
        """Format tax tables in a compact way."""
        lines = []
        
        # Standard deductions
        std_ded = tables.get("standard_deductions", {})
        if std_ded:
            lines.append("**Standard Deductions:**")
            lines.append(f"- Single: ${std_ded.get('single', 0):,}")
            lines.append(f"- Married Filing Jointly: ${std_ded.get('married_filing_jointly', 0):,}")
            lines.append(f"- Head of Household: ${std_ded.get('head_of_household', 0):,}")
            lines.append("")
        
        # Key limits
        retirement = tables.get("retirement_contributions", {})
        if retirement:
            lines.append("**Retirement Contribution Limits:**")
            lines.append(f"- 401(k): ${retirement.get('401k_limit', 0):,} (+${retirement.get('401k_catch_up_50_plus', 0):,} catch-up)")
            lines.append(f"- IRA: ${retirement.get('ira_limit', 0):,} (+${retirement.get('ira_catch_up_50_plus', 0):,} catch-up)")
            lines.append("")
        
        # Deduction limits
        deductions = tables.get("deductions", {})
        if deductions:
            lines.append("**Deduction Limits:**")
            lines.append(f"- SALT Cap: ${deductions.get('salt_cap', 0):,}")
            lines.append(f"- Mortgage Interest Debt Limit: ${deductions.get('mortgage_interest_debt_limit', 0):,}")
            lines.append(f"- Medical Expense AGI Threshold: {deductions.get('medical_expense_agi_threshold', 0) * 100}%")
            lines.append("")
        
        # Tax brackets (simplified - just single)
        brackets = tables.get("tax_brackets", {}).get("single", [])
        if brackets:
            lines.append("**Tax Brackets (Single):**")
            for b in brackets:
                max_val = f"${b['max']:,}" if b['max'] else "∞"
                lines.append(f"- {int(b['rate']*100)}%: ${b['min']:,} - {max_val}")
            lines.append("")
        
        # Credits
        credits = tables.get("credits", {})
        if credits:
            lines.append("**Key Credits:**")
            lines.append(f"- Child Tax Credit: ${credits.get('child_tax_credit', 0):,}")
            lines.append(f"- EITC (3+ children): ${credits.get('earned_income_credit_max_3_plus_children', 0):,}")
            lines.append("")
        
        return "\n".join(lines)
    
    def _format_field_mapping(self, mapping: dict) -> str:
        """Format field mapping in a readable way."""
        lines = []
        
        field_mappings = mapping.get("field_mappings", {})
        for section_name, fields in field_mappings.items():
            lines.append(f"**{section_name.replace('_', ' ').title()}:**")
            for field_id, field_info in fields.items():
                if isinstance(field_info, dict):
                    line = field_info.get("line", "")
                    desc = field_info.get("description", "")
                    lines.append(f"- `{field_id}` → Line {line}: {desc}")
            lines.append("")
        
        # Include calculation rules if present
        calc_rules = mapping.get("calculation_rules", {})
        if calc_rules:
            lines.append("**Calculation Rules:**")
            for line, formula in calc_rules.items():
                lines.append(f"- {line}: {formula}")
        
        return "\n".join(lines)
    
    def _extract_relevant_rules(self, rules_md: str, form_ids: list) -> str:
        """Extract sections of rules most relevant to the forms being filled."""
        # For now, return key sections
        # A more sophisticated version could parse markdown and select sections
        
        # Extract first ~2000 chars of rules (filing requirements, key info)
        relevant = rules_md[:3000]
        
        # Find the last complete section
        last_header = relevant.rfind("\n## ")
        if last_header > 1000:
            relevant = relevant[:last_header]
        
        return relevant


def load_knowledge_for_processing(
    tax_year: int,
    extracted_data: dict,
    knowledge_dir: Optional[Path] = None,
    max_context_tokens: int = 8000
) -> tuple[str, list]:
    """
    Convenience function to load all relevant tax knowledge for processing.
    
    Args:
        tax_year: The tax year to load knowledge for
        extracted_data: Extracted data from Step 1
        knowledge_dir: Optional custom knowledge directory
        max_context_tokens: Maximum tokens for context
        
    Returns:
        Tuple of (context_string, list_of_forms_needed)
    """
    kb = TaxKnowledgeBase(tax_year, knowledge_dir)
    
    if not kb.is_available():
        console.print(f"[yellow]Warning: No tax knowledge found for {tax_year}[/yellow]")
        console.print(f"[dim]Expected location: {kb.year_dir}[/dim]")
        return "", ["1040"]
    
    console.print(f"[green]✓ Loaded tax knowledge for {tax_year}[/green]")
    
    # Determine forms needed
    forms = kb.get_forms_needed(extracted_data)
    console.print(f"[dim]Forms detected: {', '.join(forms)}[/dim]")
    
    # Build context
    context = kb.build_context_for_forms(forms, max_context_tokens)
    
    return context, forms


if __name__ == "__main__":
    # Test loading
    import sys
    
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2025
    
    kb = TaxKnowledgeBase(year)
    
    print(f"\nTax Knowledge Base for {year}")
    print(f"Available: {kb.is_available()}")
    
    if kb.is_available():
        print("\nTax Tables:")
        tables = kb.load_tax_tables()
        print(f"  - {len(tables)} sections loaded")
        
        print("\nForm 1040 Mapping:")
        mapping = kb.load_form_mapping("1040")
        print(f"  - {len(mapping.get('field_mappings', {}))} sections")
        
        print("\nRules Summary:")
        rules = kb.load_rules_summary()
        print(f"  - {len(rules)} characters")
        
        print("\nSample Context:")
        context = kb.build_context_for_forms(["1040", "schedule_a"], max_tokens=2000)
        print(context[:1000] + "...")
