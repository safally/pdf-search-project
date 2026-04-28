"""
Search Engine Module

Search through PDF CSV index.
Searches across title, abstract, journal, and year fields.

Usage:
    from search_engine import search_pdfs
    results = search_pdfs("machine learning")
"""

import csv
import os
from pathlib import Path


def search_pdfs(query, csv_path=None):
    """
    Search the PDF index CSV for matching documents.
    
    Args:
        query: Search string
        csv_path: Optional custom CSV path (defaults to ./data/pdf_data.csv)
    
    Returns:
        list: Matching rows as dictionaries
    """
    if csv_path is None:
        csv_path = Path(__file__).parent / "data" / "pdf_data.csv"
    else:
        csv_path = Path(csv_path)
    
    if not csv_path.exists():
        return []
    
    results = []
    query_lower = query.lower()
    
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Search across all text fields
                searchable = " ".join([
                    row.get("title", "") or "",
                    row.get("abstract", "") or "",
                    row.get("journal", "") or "",
                    row.get("year", "") or ""
                ]).lower()
                
                if query_lower in searchable:
                    results.append(row)
    except Exception as e:
        print(f"Search error: {e}")
        return []
    
    return results


def count_results(query, csv_path=None):
    """Count matching documents for a query."""
    results = search_pdfs(query, csv_path)
    return len(results)


if __name__ == "__main__":
    # Quick CLI test
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "test"
    results = search_pdfs(q)
    print(f"Found {len(results)} results for '{q}':")
    for r in results[:5]:
        print(f"  - {r.get('title', 'Unknown')}")
