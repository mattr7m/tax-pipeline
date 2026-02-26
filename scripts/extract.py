#!/usr/bin/env python3
"""
extract.py - Extract structured data from tax PDFs using LLM

Supports two backends for LLM extraction:
  --extraction-backend ollama : Use Ollama with small model (default, fast)
  --extraction-backend local  : Use llama.cpp server with large model (same as processing)

Tesseract is always used for OCR. The LLM interprets the OCR text
and structures it into JSON.
"""

import json
import sys
from pathlib import Path
from typing import Optional
import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
import fitz  # pymupdf
import pytesseract
from PIL import Image
import io
import yaml
import requests

console = Console()


def load_config() -> dict:
    """Load configuration from config.yaml"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def extract_text_from_pdf(pdf_path: Path, use_ocr: bool = True) -> str:
    """
    Extract text from PDF, using OCR if needed.
    
    Args:
        pdf_path: Path to PDF file
        use_ocr: Whether to use OCR for image-based pages
        
    Returns:
        Extracted text content
    """
    doc = fitz.open(pdf_path)
    full_text = []
    
    for page_num, page in enumerate(doc):
        # Try native text extraction first
        text = page.get_text()
        
        # If little/no text found and OCR enabled, use OCR
        if len(text.strip()) < 50 and use_ocr:
            # Render page to image
            mat = fitz.Matrix(300/72, 300/72)  # 300 DPI
            pix = page.get_pixmap(matrix=mat)
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data))
            
            # OCR the image
            text = pytesseract.image_to_string(img, lang='eng')
        
        full_text.append(f"--- Page {page_num + 1} ---\n{text}")
    
    doc.close()
    return "\n\n".join(full_text)


def detect_document_type(text: str, config: dict) -> tuple[str, float]:
    """
    Detect the type of tax document based on keywords.
    
    Returns:
        Tuple of (document_type, confidence)
    """
    doc_types = config.get("document_types", {})
    
    best_match = ("unknown", 0.0)
    text_lower = text.lower()
    
    for doc_type, info in doc_types.items():
        keywords = info.get("keywords", [])
        matches = sum(1 for kw in keywords if kw.lower() in text_lower)
        confidence = matches / len(keywords) if keywords else 0
        
        if confidence > best_match[1]:
            best_match = (doc_type, confidence)
    
    return best_match


def build_extraction_prompt(
    text: str,
    doc_type: str,
    config: dict,
    prior_year_context: Optional[dict] = None
) -> str:
    """Build the extraction prompt for the LLM."""
    doc_type_info = config.get("document_types", {}).get(doc_type, {})
    expected_fields = doc_type_info.get("fields", [])
    
    prompt = f"""You are a tax document data extraction assistant. Extract structured data from the following tax document.

Document Type: {doc_type.upper().replace('_', '-')}
Expected Fields: {', '.join(expected_fields)}

IMPORTANT: 
- Extract ALL numerical values exactly as they appear
- Preserve SSNs, EINs, and account numbers exactly (they will be sanitized later)
- If a field is not found, use null
- Return ONLY valid JSON, no other text

{f"Prior year context for reference: {json.dumps(prior_year_context)}" if prior_year_context else ""}

Document text:
---
{text[:8000]}  
---

Return a JSON object with the extracted data. Include:
- document_type: "{doc_type}"
- tax_year: (extracted year)
- payer/employer info (name, address, EIN)
- recipient info (name, address, SSN)
- All monetary amounts as numbers (no $ signs or commas)
- Any other relevant fields for this document type

JSON output:"""

    return prompt


def extract_with_ollama(
    text: str,
    doc_type: str,
    config: dict,
    prior_year_context: Optional[dict] = None
) -> dict:
    """
    Use Ollama to extract structured data from document text.
    """
    try:
        import ollama
    except ImportError:
        console.print("[red]Error: ollama package not installed.[/red]")
        console.print("Install with: pip install ollama")
        sys.exit(1)
    
    ollama_config = config.get("ollama", {})
    model = ollama_config.get("model", "llama3.2")
    
    prompt = build_extraction_prompt(text, doc_type, config, prior_year_context)

    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={
                "temperature": ollama_config.get("temperature", 0.1),
            }
        )
        
        response_text = response['message']['content']
        return parse_json_response(response_text, doc_type, text)
        
    except Exception as e:
        console.print(f"[red]Error calling Ollama: {e}[/red]")
        raise


def extract_with_local_llm(
    text: str,
    doc_type: str,
    config: dict,
    prior_year_context: Optional[dict] = None
) -> dict:
    """
    Use local llama.cpp server to extract structured data from document text.
    """
    llm_config = config.get("local_llm_server", {})
    extraction_config = config.get("extraction", {})
    
    base_url = llm_config.get("base_url", "http://localhost:8080/v1")
    model_name = llm_config.get("model", "local-model")
    timeout = extraction_config.get("timeout", llm_config.get("timeout", 180))
    temperature = extraction_config.get("temperature", 0.1)
    max_tokens = extraction_config.get("max_tokens", 4096)
    
    prompt = build_extraction_prompt(text, doc_type, config, prior_year_context)
    
    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            json={
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=timeout
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        console.print("[red]Error: Could not connect to local LLM server.[/red]")
        console.print(f"Make sure llama.cpp server is running at {base_url}")
        sys.exit(1)
    except requests.exceptions.Timeout:
        console.print("[red]Error: Request timed out.[/red]")
        console.print("Try increasing timeout in config.yaml under 'extraction'")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        console.print(f"[red]Error: HTTP {e.response.status_code}[/red]")
        console.print(e.response.text)
        sys.exit(1)
    
    result = response.json()
    
    # Handle different response formats
    if "choices" in result:
        response_text = result["choices"][0]["message"]["content"]
    elif "content" in result:
        response_text = result["content"]
    else:
        response_text = str(result)
    
    return parse_json_response(response_text, doc_type, text)


def parse_json_response(response_text: str, doc_type: str, original_text: str) -> dict:
    """Parse JSON from LLM response, handling markdown code blocks."""
    try:
        # Handle markdown code blocks
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
        console.print(f"[yellow]Warning: Could not parse LLM response as JSON: {e}[/yellow]")
        return {
            "document_type": doc_type,
            "raw_text": original_text[:2000],
            "extraction_error": str(e)
        }


def process_directory(
    input_dir: Path,
    output_file: Path,
    config: dict,
    extraction_backend: str,
    document_role: str = "source_document",
    prior_year_data: Optional[dict] = None
) -> dict:
    """
    Process all PDFs in a directory.
    
    Args:
        input_dir: Directory containing PDFs
        output_file: Output JSON file
        config: Configuration dict
        extraction_backend: "ollama" or "local"
        document_role: "source_document" or "filed_return"
        prior_year_data: Optional prior year data for context
    """
    pdf_files = list(input_dir.rglob("*.pdf"))
    
    if not pdf_files:
        console.print(f"[yellow]No PDF files found in {input_dir}[/yellow]")
        return {}
    
    console.print(f"[blue]Found {len(pdf_files)} PDF files to process[/blue]")
    
    extracted_data = {
        "tax_year": None,
        "document_role": document_role,  # Track the role of these documents
        "documents": [],
        "summary": {
            "income": {},
            "deductions": {},
            "withholding": {}
        }
    }
    
    # Select extraction function based on backend
    extract_fn = extract_with_ollama if extraction_backend == "ollama" else extract_with_local_llm
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        for pdf_path in pdf_files:
            task = progress.add_task(f"Processing {pdf_path.name}...", total=None)
            
            # Extract text via OCR
            text = extract_text_from_pdf(pdf_path)
            
            # Detect document type
            doc_type, confidence = detect_document_type(text, config)
            console.print(f"  Detected: {doc_type} (confidence: {confidence:.0%})")
            
            # Extract structured data with LLM
            doc_data = extract_fn(
                text, 
                doc_type, 
                config,
                prior_year_data
            )
            doc_data["source_file"] = pdf_path.name
            doc_data["detection_confidence"] = confidence
            doc_data["document_role"] = document_role  # Track role per document
            
            extracted_data["documents"].append(doc_data)
            
            # Update summary based on document type
            update_summary(extracted_data["summary"], doc_data, doc_type)
            
            # Capture tax year
            if doc_data.get("tax_year") and not extracted_data["tax_year"]:
                extracted_data["tax_year"] = doc_data["tax_year"]
            
            progress.remove_task(task)
    
    # Write output
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w') as f:
        json.dump(extracted_data, f, indent=2)
    
    console.print(f"[green]Extracted data written to {output_file}[/green]")
    
    return extracted_data


def update_summary(summary: dict, doc_data: dict, doc_type: str):
    """Update running summary with data from a document."""
    
    if doc_type == "w2":
        wages = doc_data.get("wages") or doc_data.get("wages_tips_compensation")
        if wages:
            try:
                summary["income"]["wages"] = summary["income"].get("wages", 0) + float(wages)
            except (TypeError, ValueError):
                pass
        
        fed_withheld = doc_data.get("federal_withheld") or doc_data.get("federal_income_tax_withheld")
        if fed_withheld:
            try:
                summary["withholding"]["federal"] = summary["withholding"].get("federal", 0) + float(fed_withheld)
            except (TypeError, ValueError):
                pass
    
    elif doc_type == "1099_int":
        interest = doc_data.get("interest_income")
        if interest:
            try:
                summary["income"]["interest"] = summary["income"].get("interest", 0) + float(interest)
            except (TypeError, ValueError):
                pass
    
    elif doc_type == "1099_div":
        dividends = doc_data.get("ordinary_dividends")
        if dividends:
            try:
                summary["income"]["dividends"] = summary["income"].get("dividends", 0) + float(dividends)
            except (TypeError, ValueError):
                pass
        
        qualified = doc_data.get("qualified_dividends")
        if qualified:
            try:
                summary["income"]["qualified_dividends"] = summary["income"].get("qualified_dividends", 0) + float(qualified)
            except (TypeError, ValueError):
                pass
    
    elif doc_type == "1098":
        mortgage_int = doc_data.get("mortgage_interest")
        if mortgage_int:
            try:
                summary["deductions"]["mortgage_interest"] = summary["deductions"].get("mortgage_interest", 0) + float(mortgage_int)
            except (TypeError, ValueError):
                pass
        
        prop_tax = doc_data.get("property_taxes")
        if prop_tax:
            try:
                summary["deductions"]["property_taxes"] = summary["deductions"].get("property_taxes", 0) + float(prop_tax)
            except (TypeError, ValueError):
                pass


def check_backend_available(backend: str, config: dict) -> bool:
    """Check if the selected backend is available."""
    if backend == "ollama":
        try:
            import ollama
            ollama.list()
            return True
        except Exception:
            return False
    else:  # local
        try:
            llm_config = config.get("local_llm_server", {})
            base_url = llm_config.get("base_url", "http://localhost:8080/v1")
            response = requests.get(f"{base_url.replace('/v1', '')}/health", timeout=5)
            return response.status_code == 200
        except Exception:
            return False


@click.command()
@click.option('--input', '-i', 'input_path', required=True, 
              type=click.Path(exists=True), help='Input PDF file or directory')
@click.option('--output', '-o', 'output_path', required=True,
              type=click.Path(), help='Output JSON file')
@click.option('--prior-year', '-p', 'prior_year_path',
              type=click.Path(exists=True), help='Prior year extracted data for context')
@click.option('--extraction-backend', '-e', 'extraction_backend',
              type=click.Choice(['ollama', 'local'], case_sensitive=False),
              default='ollama',
              help='LLM backend for extraction: "ollama" (fast, small model) or "local" (llama.cpp server)')
@click.option('--document-role', '-r', 'document_role',
              type=click.Choice(['source_document', 'filed_return'], case_sensitive=False),
              default='source_document',
              help='Role of documents: "source_document" (W-2s, 1099s) or "filed_return" (completed tax returns)')
def main(input_path: str, output_path: str, prior_year_path: Optional[str], extraction_backend: str, document_role: str):
    """
    Extract structured data from tax PDFs using LLM.
    
    Supports two backends:
    
    \b
    --extraction-backend ollama : Use Ollama with small model (default, fast)
    --extraction-backend local  : Use llama.cpp server (same model as processing)
    
    Document roles:
    
    \b
    --document-role source_document : W-2s, 1099s, 1098s (input documents)
    --document-role filed_return    : Previously filed tax returns (for reference)
    
    All processing happens locally - no data leaves your machine.
    """
    backend_display = "Ollama" if extraction_backend == "ollama" else "Local LLM (llama.cpp)"
    role_display = "Source Documents" if document_role == "source_document" else "Filed Returns"
    
    console.print(f"[bold blue]Tax Document Extractor ({backend_display})[/bold blue]")
    console.print(f"Document Role: {role_display}")
    console.print("All processing runs locally - no data leaves your machine\n")
    
    config = load_config()
    input_path = Path(input_path)
    output_path = Path(output_path)
    
    # Check backend is available
    if not check_backend_available(extraction_backend, config):
        if extraction_backend == "ollama":
            console.print("[red]Error: Ollama is not running.[/red]")
            console.print("Start it with: ollama serve")
            console.print("Then pull a model: ollama pull llama3.2")
        else:
            llm_config = config.get("local_llm_server", {})
            base_url = llm_config.get("base_url", "http://localhost:8080/v1")
            console.print(f"[red]Error: Local LLM server not available at {base_url}[/red]")
            console.print("Start llama.cpp server with: ./scripts/start-local-llm.sh")
        sys.exit(1)
    
    console.print(f"[green]✓ {backend_display} is available[/green]\n")
    
    # Load prior year data if provided
    prior_year_data = None
    if prior_year_path:
        with open(prior_year_path) as f:
            prior_year_data = json.load(f)
        console.print(f"[dim]Loaded prior year context from {prior_year_path}[/dim]")
    
    if input_path.is_dir():
        process_directory(input_path, output_path, config, extraction_backend, document_role, prior_year_data)
    else:
        # Single file
        text = extract_text_from_pdf(input_path)
        doc_type, confidence = detect_document_type(text, config)
        console.print(f"Detected document type: {doc_type} (confidence: {confidence:.0%})")
        
        # Select extraction function based on backend
        extract_fn = extract_with_ollama if extraction_backend == "ollama" else extract_with_local_llm
        
        doc_data = extract_fn(text, doc_type, config, prior_year_data)
        doc_data["source_file"] = input_path.name
        doc_data["document_role"] = document_role
        
        # Wrap single file in same structure as directory
        output_data = {
            "tax_year": doc_data.get("tax_year"),
            "document_role": document_role,
            "documents": [doc_data],
            "summary": {}
        }
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(output_data, f, indent=2)
        
        console.print(f"[green]Extracted data written to {output_path}[/green]")


if __name__ == "__main__":
    main()
