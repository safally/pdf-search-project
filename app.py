import os
import sys
import csv
import logging
import shutil
from pathlib import Path
from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename
from pypdf import PdfReader

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
CSV_PATH = DATA_DIR / "pdf_data.csv"


def ensure_data_directory():
    """Create data directory if it doesn't exist."""
    if not DATA_DIR.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created data directory at: {DATA_DIR}")
        return True
    return False


def extract_metadata_from_text(text):
    """
    Attempt to extract year and journal from PDF text.
    Returns (title, year, journal, abstract)
    """
    lines = [line.strip() for line in text.split('\n') if line.strip()]

    title = ""
    year = ""
    journal = ""
    abstract = ""

    if not lines:
        return title, year, journal, abstract

    # Title is usually the first non-empty line or first few lines
    title = lines[0]
    if len(lines) > 1 and len(lines[1]) < 100:
        title += " " + lines[1]
    title = title[:100].strip()

    # Extract year (4-digit number between 1900-2099)
    import re
    year_pattern = r'\b(19[0-9]{2}|20[0-9]{2})\b'
    for line in lines[:10]:
        match = re.search(year_pattern, line)
        if match:
            year = match.group(0)
            break

    # Extract journal (look for patterns like "Journal of...", "arXiv:", etc.)
    journal_keywords = ['journal', 'conference', 'proceedings', 'arxiv', 'volume', 'pp.']
    for line in lines[:15]:
        lower_line = line.lower()
        if any(keyword in lower_line for keyword in journal_keywords):
            journal = line[:100].strip()
            break

    # Abstract: combine lines until we hit keywords like keywords, introduction, etc.
    stop_keywords = ['keywords', 'introduction', 'references', 'related work', 'methodology']
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


def scan_pdfs(force_rescan=False):
    """
    Scan PDF files in data directory and generate CSV index.
    Returns (success: bool, message: str, count: int)
    """
    ensure_data_directory()

    # Check if rescan is needed
    if not force_rescan and CSV_PATH.exists():
        logger.info(f"CSV already exists at {CSV_PATH}. Use force=True to rescan.")
        return True, "CSV already exists", count_pdfs_in_csv()

    pdf_files = sorted(DATA_DIR.glob("*.pdf"))

    if not pdf_files:
        logger.warning("No PDF files found in data/ folder")
        return False, "No PDF files found in data/ folder", 0

    logger.info(f"Scanning {len(pdf_files)} PDF file(s)...")

    data = []
    for pdf_file in pdf_files:
        try:
            with open(pdf_file, "rb") as f:
                reader = PdfReader(f)
                if reader.pages:
                    text = reader.pages[0].extract_text() or ""
                else:
                    text = ""

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
                logger.info(f"  [OK] Processed: {pdf_file.name} (year={year}, journal={journal[:30] if journal else 'N/A'})")
        except Exception as e:
            logger.error(f"  [ERROR] Error reading {pdf_file.name}: {e}")
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
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)
        logger.info(f"[SUCCESS] CSV generated: {CSV_PATH} ({len(data)} entries)")
        return True, f"Successfully indexed {len(data)} PDF(s)", len(data)
    except Exception as e:
        logger.error(f"Failed to write CSV: {e}")
        return False, f"Failed to write CSV: {e}", 0


def count_pdfs_in_csv():
    """Count how many PDFs are in the CSV."""
    if not CSV_PATH.exists():
        return 0
    try:
        with open(CSV_PATH, "r", encoding="utf-8") as f:
            return sum(1 for _ in f) - 1
    except:
        return 0


def auto_discover_and_import_pdfs():
    """
    Auto-discover PDF files from common user directories and import them into DATA_DIR.
    
    Searches recursively in:
    - ~/Downloads
    - ~/Desktop
    
    Can be customized via env var PDF_SEARCH_PATHS (comma-separated paths)
    
    Returns: (copied_count: int, source_stats: dict)
    """
    home = Path.home()
    
    # Support custom paths via environment variable
    custom_paths_env = os.environ.get('PDF_SEARCH_PATHS', '').strip()
    if custom_paths_env:
        search_paths = []
        for p in custom_paths_env.split(','):
            p = p.strip()
            if p:
                path = Path(p).expanduser()
                if path.exists() and path.is_dir():
                    search_paths.append(path)
                else:
                    logger.warning(f"Custom search path does not exist or is not a directory: {path}")
    else:
        # Default search locations
        search_paths = [
            home / "Downloads",
            home / "Desktop"
        ]
    
    # Filter to only existing directories
    valid_search_paths = [p for p in search_paths if p.exists() and p.is_dir()]
    
    if not valid_search_paths:
        logger.info("No valid search directories found. Skipping auto-discovery.")
        return 0, {}
    
    logger.info(f"No PDFs in data/. Searching {len(valid_search_paths)} directory(ies)...")
    
    # Collect all unique PDFs by filename to avoid duplicates across locations
    collected_pdfs = {}  # filename -> Path object (first occurrence wins)
    
    for search_dir in valid_search_paths:
        try:
            # Use rglob for recursive search
            # Limit depth for safety: limit to reasonable depth (avoid scanning deeply nested dirs)
            max_depth = 5  # configurable safety limit
            for pdf_path in search_dir.rglob("*.pdf"):
                # Check depth relative to search_dir
                try:
                    rel_parts = pdf_path.relative_to(search_dir).parts
                    if len(rel_parts) > max_depth:
                        continue  # skip very deep files
                except ValueError:
                    pass  # should not happen
                
                filename = pdf_path.name
                if filename not in collected_pdfs:
                    collected_pdfs[filename] = pdf_path
            
            # Also find uppercase .PDF files
            for pdf_path in search_dir.rglob("*.PDF"):
                try:
                    rel_parts = pdf_path.relative_to(search_dir).parts
                    if len(rel_parts) > max_depth:
                        continue
                except ValueError:
                    pass
                
                filename = pdf_path.name
                if filename not in collected_pdfs:
                    collected_pdfs[filename] = pdf_path
                    
        except PermissionError as e:
            logger.warning(f"Permission denied accessing {search_dir}: {e}")
        except Exception as e:
            logger.error(f"Error scanning {search_dir}: {e}")
    
    # Get set of existing files in DATA_DIR (case-insensitive on macOS)
    existing_files = set()
    if DATA_DIR.exists():
        for f in DATA_DIR.glob("*.pdf"):
            existing_files.add(f.name.lower())
    
    # Determine which files need copying (skip existing by name, case-insensitive)
    to_copy = {}
    for filename, src_path in collected_pdfs.items():
        if filename.lower() not in existing_files:
            to_copy[filename] = src_path
    
    # Log discovery summary by source
    source_counts = {}
    for src_path in collected_pdfs.values():
        src_dir = str(src_path.parent)
        source_counts[src_dir] = source_counts.get(src_dir, 0) + 1
    
    for src_dir, count in source_counts.items():
        logger.info(f"   [INFO] Found {count} PDF(s) in {src_dir}")
    
    logger.info(f"   [INFO] Total unique PDFs found: {len(collected_pdfs)}")
    logger.info(f"   [INFO] Already in data/ (skipping): {len(existing_files)}")
    logger.info(f"   [INFO] To import: {len(to_copy)}")
    
    # Copy files
    copied = 0
    for filename, src_path in sorted(to_copy.items()):
        dest_path = DATA_DIR / filename
        
        # Handle name collision (should be rare since we deduped)
        if dest_path.exists():
            base = dest_path.stem
            ext = dest_path.suffix
            counter = 1
            while dest_path.exists():
                dest_path = DATA_DIR / f"{base}_{counter}{ext}"
                counter += 1
        
        try:
            shutil.copy2(src_path, dest_path)
            copied += 1
            logger.info(f"   [IMPORT] Copied: {filename}")
        except PermissionError as e:
            logger.error(f"   [ERROR] Permission denied copying {filename}: {e}")
        except Exception as e:
            logger.error(f"   [ERROR] Failed to copy {filename}: {e}")
    
    if copied > 0:
        logger.info(f"[SUCCESS] Imported {copied} PDF(s) into {DATA_DIR}")
    else:
        logger.info("No new PDFs to import.")
    
    return copied, source_counts


def get_search_results(query):
    """Search the CSV index for matching PDFs."""
    if not CSV_PATH.exists():
        logger.warning("CSV not found. Cannot search.")
        return []

    results = []
    try:
        with open(CSV_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                searchable_text = (
                    (row.get("title", "") or "") + " " +
                    (row.get("abstract", "") or "") + " " +
                    (row.get("journal", "") or "") + " " +
                    (row.get("year", "") or "")
                ).lower()
                if query.lower() in searchable_text:
                    results.append(row)
    except Exception as e:
        logger.error(f"Error reading CSV: {e}")
        return []

    return results


# ============== Flask Routes ==============

@app.route("/")
def index():
    """Serve the main UI."""
    try:
        return render_template("index.html")
    except Exception as e:
        logger.error(f"Template error: {e}")
        return jsonify({"error": "Template not found", "message": str(e)}), 500


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "OK",
        "csv_exists": CSV_PATH.exists(),
        "data_dir": str(DATA_DIR),
        "port": 5001
    })


@app.route("/scan", methods=["GET", "POST"])
def scan():
    """
    Trigger PDF scanning and CSV generation.
    GET: Returns status or triggers scan if CSV missing
    POST: Force rescan
    """
    if request.method == "GET":
        if CSV_PATH.exists():
            count = count_pdfs_in_csv()
            return jsonify({
                "status": "already_scanned",
                "message": f"CSV already exists with {count} entries",
                "count": count
            })
        else:
            success, message, count = scan_pdfs(force_rescan=True)
            return jsonify({
                "status": "success" if success else "no_pdfs",
                "message": message,
                "count": count
            })
    else:
        force = request.args.get("force", "false").lower() == "true"
        success, message, count = scan_pdfs(force_rescan=force)
        return jsonify({
            "status": "success" if success else "error",
            "message": message,
            "count": count
        })


@app.route("/upload", methods=["POST"])
def upload():
    """
    Accept a PDF file upload, save it to DATA_DIR, and re-index.
    Returns JSON: {"message": "Uploaded successfully. Indexed X PDFs."}
    """
    ensure_data_directory()

    if "pdf" not in request.files:
        logger.warning("Upload request received with no file field 'pdf'.")
        return jsonify({"status": "error", "message": "No file field 'pdf' in request."}), 400

    uploaded_file = request.files["pdf"]

    if uploaded_file.filename == "" or uploaded_file.filename is None:
        logger.warning("Upload request received with empty filename.")
        return jsonify({"status": "error", "message": "No file selected."}), 400

    filename = secure_filename(uploaded_file.filename)

    if not filename.lower().endswith(".pdf"):
        logger.warning(f"Upload rejected — not a PDF: {filename}")
        return jsonify({"status": "error", "message": "Only PDF files are allowed."}), 400

    # Handle duplicate filenames by appending _1, _2, ...
    save_path = DATA_DIR / filename
    if save_path.exists():
        base = save_path.stem
        ext = save_path.suffix
        counter = 1
        while save_path.exists():
            save_path = DATA_DIR / f"{base}_{counter}{ext}"
            counter += 1
        logger.info(f"Duplicate filename detected. Saving as: {save_path.name}")
    else:
        logger.info(f"Saving upload as: {filename}")

    try:
        uploaded_file.save(str(save_path))
        logger.info(f"Upload saved successfully: {save_path.name}")
    except Exception as e:
        logger.error(f"Failed to save uploaded file: {e}")
        return jsonify({"status": "error", "message": f"Failed to save file: {e}"}), 500

    logger.info("Triggering PDF re-index after upload...")
    success, message, count = scan_pdfs(force_rescan=True)

    if success:
        logger.info(f"Upload + index completed. Indexed {count} PDF(s).")
        return jsonify({"status": "success", "message": f"Uploaded successfully. Indexed {count} PDFs."})
    else:
        logger.error(f"Upload saved but indexing failed: {message}")
        return jsonify({"status": "error", "message": f"File uploaded but indexing failed: {message}"}), 500


@app.route("/search")
def search():
    """Search endpoint."""
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"results": [], "message": "Empty query"})

    logger.info(f"Searching for: {query}")
    results = get_search_results(query)
    logger.info(f"Found {len(results)} results")

    return jsonify({
        "query": query,
        "count": len(results),
        "results": results
    })


@app.route("/debug/files")
def debug_files():
    """Debug endpoint to show filesystem state."""
    import os
    try:
        cwd = os.getcwd()
        base_dir = str(BASE_DIR.absolute())
        data_dir = str(DATA_DIR.absolute())
        data_exists = DATA_DIR.exists()
        files_in_data = os.listdir(DATA_DIR) if data_exists else []
        pdfs_glob = [str(f) for f in DATA_DIR.glob("*.pdf")] if data_exists else []
        all_files_detail = []
        if data_exists:
            for f in os.listdir(DATA_DIR):
                fp = DATA_DIR / f
                try:
                    stat = fp.stat()
                    all_files_detail.append({
                        "name": f,
                        "is_file": fp.is_file(),
                        "is_dir": fp.is_dir(),
                        "size": stat.st_size,
                        "extension": fp.suffix.lower()
                    })
                except Exception as e:
                    all_files_detail.append({"name": f, "error": str(e)})
        return jsonify({
            "cwd": cwd,
            "base_dir": base_dir,
            "data_dir": data_dir,
            "data_dir_exists": data_exists,
            "all_files": files_in_data,
            "pdf_glob_results": pdfs_glob,
            "files_detail": all_files_detail
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/status")
def api_status():
    """Return app status."""
    data_dir_exists = DATA_DIR.exists()
    csv_exists = CSV_PATH.exists()
    pdf_count = len(list(DATA_DIR.glob("*.pdf"))) if data_dir_exists else 0
    indexed_count = count_pdfs_in_csv() if csv_exists else 0

    return jsonify({
        "data_directory": str(DATA_DIR),
        "data_directory_exists": data_dir_exists,
        "csv_exists": csv_exists,
        "pdf_files_count": pdf_count,
        "indexed_pdfs_count": indexed_count,
        "message": "No PDFs indexed" if indexed_count == 0 else f"{indexed_count} PDF(s) indexed and searchable",
        "scan_needed": data_dir_exists and pdf_count > 0 and not csv_exists
    })


@app.route("/download")
def download_csv():
    """Download the CSV file."""
    if CSV_PATH.exists():
        return send_from_directory(DATA_DIR, "pdf_data.csv", as_attachment=True)
    else:
        return jsonify({"error": "CSV not found. Run /scan first."}), 404


# ============== App Initialization ==============

def initialize_app():
    """Initialize the application on startup (both local and production)."""
    logger.info("=" * 60)
    logger.info("PDF CRAWLER + SEARCH APP initializing...")
    logger.info(f"BASE_DIR: {BASE_DIR.absolute()}")
    logger.info(f"DATA_DIR: {DATA_DIR.absolute()}")
    logger.info("=" * 60)

    # Step 1: Ensure data directory exists
    created = ensure_data_directory()
    if created:
        logger.info("Data directory created.")

    # Step 2: Check for existing CSV or auto-scan
    if CSV_PATH.exists():
        count = count_pdfs_in_csv()
        logger.info(f"Found existing CSV index: {CSV_PATH.name} ({count} entries)")
    else:
        pdf_count = len(list(DATA_DIR.glob("*.pdf"))) if DATA_DIR.exists() else 0
        
        if pdf_count == 0:
            # Auto-discover and import PDFs from common user directories
            logger.info("No PDFs in data/. Starting auto-discovery from ~/Downloads and ~/Desktop...")
            imported_count, _ = auto_discover_and_import_pdfs()
            # Re-count after import
            pdf_count = len(list(DATA_DIR.glob("*.pdf"))) if DATA_DIR.exists() else 0
        
        if pdf_count > 0:
            logger.info(f"Found {pdf_count} PDF(s) in data/. Generating search index...")
            success, message, count = scan_pdfs(force_rescan=True)
            if success:
                logger.info(f"CSV generated successfully: {count} entries")
            else:
                logger.warning(f"Scan failed: {message}")
        else:
            logger.warning("No PDFs found in data/ or user directories. Search will return no results.")
    
    logger.info("=" * 60)
    logger.info("Initialization complete. Ready to serve requests.")
    
    # Verify template exists (fail fast if missing)
    template_path = BASE_DIR / "templates" / "index.html"
    if not template_path.exists():
        logger.error(f"FATAL: Template missing: {template_path}")
        sys.exit(1)


# ============== App Entry Point ==============

# Initialize on import (for gunicorn/WSGI) and on direct run
if __name__ == "__main__":
    # Local development: run init then start dev server
    initialize_app()
    port = int(os.environ.get("PORT", 5001))
    logger.info(f"Starting Flask development server on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
else:
    # Production (gunicorn): import this module, so run init once
    initialize_app()
