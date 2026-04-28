"""
PDF Scanner Module

Scans PDF files in a directory and extracts metadata:
- Title
- Year (4-digit year found in text)
- Journal (journal/conference name if found)
- Abstract (first few paragraphs from first page)

Usage:
    from file_scanner import scan_pdf_directory
    scan_pdf_directory("./data")
"""

import csv
import re
from pathlib import Path
from pypdf import PdfReader


def extract_metadata_from_text(text):
    """
    Extract title, year, journal, and abstract from PDF text.
    Returns: (title, year, journal, abstract)
    """
    lines = [line.strip() for line in text.split('\n') if line.strip()]

    title = ""
    year = ""
    journal = ""
    abstract = ""

    if not lines:
        return title, year, journal, abstract

    # Title: first significant line(s)
    title = lines[0]
    if len(lines) > 1 and len(lines[1]) < 100:
        title += " " + lines[1]
    title = title[:100].strip()

    # Year: 4-digit number 1900-2099
    year_pattern = r'\b(19[0-9]{2}|20[0-9]{2})\b'
    for line in lines[:10]:
        match = re.search(year_pattern, line)
        if match:
            year = match.group(0)
            break

    # Journal: look for journal/conference/proceedings identifiers
    journal_keywords = ['journal', 'conference', 'proceedings', 'arxiv', 'volume', 'pp.', 'vol.']
    for line in lines[:15]:
        lower_line = line.lower()
        if any(keyword in lower_line for keyword in journal_keywords):
            journal = line[:100].strip()
            break

    # Abstract: collect lines before stop words
    stop_keywords = ['keywords', 'introduction', 'references', 'related work', 'methodology', '§']
    abstract_lines = []
    for i, line in enumerate(lines):
        if i == 0:
            continue
        lower_line = line.lower()
        if any(keyword in lower_line for keyword in stop_keywords):
            break
        if len(line) > 20:
            abstract_lines.append(line)
        if len(' '.join(abstract_lines)) > 500:
            break

    abstract = ' '.join(abstract_lines)[:300].strip()

    return title, year, journal, abstract


def scan_pdf_directory(directory_path, output_csv=None):
    """
    Scan PDF directory and generate CSV index.

    Args:
        directory_path: Path to directory containing PDFs
        output_csv: Optional custom CSV output path

    Returns:
        tuple: (success: bool, message: str, count: int)
    """
    root_path = Path(directory_path)
    csv_path = Path(output_csv) if output_csv else root_path / "pdf_data.csv"

    # Ensure directory exists
    root_path.mkdir(parents=True, exist_ok=True)

    pdf_files = list(root_path.glob("*.pdf"))

    if not pdf_files:
        return False, "No PDF files found", 0

    print(f"\nScanning {len(pdf_files)} PDF(s)...")

    data = []
    for pdf_file in pdf_files:
        try:
            with open(pdf_file, "rb") as f:
                reader = PdfReader(f)
                text = reader.pages[0].extract_text() if reader.pages else ""

                title, year, journal, abstract = extract_metadata_from_text(text)

                if not title:
                    title = pdf_file.stem.replace('_', ' ').title()
                if not abstract and text:
                    abstract = text[:300].strip()

                data.append({
                    "filename": pdf_file.name,
                    "title": title,
                    "year": year,
                    "journal": journal,
                    "abstract": abstract
                })
                print(f"   [OK] {pdf_file.name} (year={year}, journal={journal[:20] if journal else 'N/A'})")
        except Exception as e:
            print(f"   [ERROR] Error reading {pdf_file.name}: {e}")
            data.append({
                "filename": pdf_file.name,
                "title": f"[ERROR] {pdf_file.name}",
                "year": "",
                "journal": "",
                "abstract": f"Error: {str(e)}"
            })

    # Write CSV
    fieldnames = ["filename", "title", "year", "journal", "abstract"]
    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)

        print(f"\n[SUCCESS] CSV generated: {csv_path} ({len(data)} entries)\n")
        return True, f"Successfully indexed {len(data)} PDF(s)", len(data)
    except Exception as e:
        print(f"\n[ERROR] Failed to write CSV: {e}\n")
        return False, f"Failed to write CSV: {e}", 0


if __name__ == "__main__":
    # Run scanner on ./data directory
    success, msg, count = scan_pdf_directory("./data")
    print(msg)
