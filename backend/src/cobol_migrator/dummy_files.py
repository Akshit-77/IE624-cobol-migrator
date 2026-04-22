"""
Dummy file generator for testing COBOL-to-Python translations.

When a COBOL program requires external files (like EMPLOYEE.DAT), this module
generates correctly formatted dummy files based on actual COBOL record layouts.

CRITICAL: COBOL records are FIXED-WIDTH with NO separators between fields.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from cobol_migrator.cobol_parser import (
    generate_cobol_sample_data,
    get_input_file_layout,
)

logger = logging.getLogger(__name__)


@dataclass
class DummyFileSpec:
    """Specification for a dummy file to be created."""

    filename: str
    content: str
    record_count: int
    record_length: int
    field_docs: str | None = None  # Documentation of field positions


@dataclass
class DummyFileResult:
    """Result of dummy file creation."""

    files_created: list[str]
    temp_dir: Path | None
    success: bool
    error: str | None
    field_docs: dict[str, str] | None = None  # Per-file documentation


def _extract_filenames_from_cobol(cobol_source: str) -> list[str]:
    """Extract file names referenced in COBOL source."""
    filenames = []

    # Pattern: ASSIGN TO 'filename' or ASSIGN TO "filename"
    assign_pattern = r"ASSIGN\s+TO\s+['\"]([^'\"]+)['\"]"
    matches = re.findall(assign_pattern, cobol_source, re.IGNORECASE)
    filenames.extend(matches)

    # Pattern: ASSIGN TO filename (without quotes)
    assign_pattern2 = r"ASSIGN\s+TO\s+(\S+\.(?:DAT|TXT|DATA|RPT|OUT|INP|IN))"
    matches2 = re.findall(assign_pattern2, cobol_source, re.IGNORECASE)
    filenames.extend(matches2)

    return list(set(filenames))


def _extract_filenames_from_python(python_code: str) -> list[str]:
    """Extract file names referenced in Python code."""
    filenames = []

    # Pattern: open('filename') or open("filename")
    open_pattern = r"open\s*\(\s*['\"]([^'\"]+)['\"]"
    matches = re.findall(open_pattern, python_code, re.IGNORECASE)
    filenames.extend(matches)

    # Pattern: Path('filename') or Path("filename")
    path_pattern = r"Path\s*\(\s*['\"]([^'\"]+)['\"]"
    matches2 = re.findall(path_pattern, python_code, re.IGNORECASE)
    filenames.extend(matches2)

    return list(set(filenames))


def generate_dummy_file_specs(
    cobol_source: str,
    python_code: str,
    io_contract: dict | None = None,
) -> list[DummyFileSpec]:
    """
    Generate specifications for dummy files needed by the program.
    
    Uses the COBOL parser to analyze actual record layouts and generate
    correctly formatted fixed-width data.
    """
    specs = []

    # Use the new COBOL parser to generate sample data
    sample_data = generate_cobol_sample_data(cobol_source, count=3)
    
    # Also extract any additional files from Python code that might not be in COBOL
    python_files = _extract_filenames_from_python(python_code)
    cobol_files = _extract_filenames_from_cobol(cobol_source)
    
    # Get field documentation for the input file
    filename, layout = get_input_file_layout(cobol_source)
    field_docs = layout.get_field_documentation() if layout else None

    # Process files found via COBOL parsing (these have correct format)
    for file_name, content in sample_data.items():
        records = content.strip().split("\n")
        record_length = len(records[0]) if records else 0
        
        specs.append(
            DummyFileSpec(
                filename=file_name,
                content=content,
                record_count=len(records),
                record_length=record_length,
                field_docs=field_docs if file_name == filename else None,
            )
        )

    # Handle any Python-referenced files not in COBOL
    processed_names = {s.filename.lower() for s in specs}
    
    for py_file in python_files:
        if py_file.lower() in processed_names:
            continue
        
        # Skip output files
        if any(x in py_file.lower() for x in ['.rpt', '.out', 'report', 'output']):
            continue
        
        # Check if it's in COBOL files (might have different name format)
        is_in_cobol = any(
            py_file.lower() in cf.lower() or cf.lower() in py_file.lower()
            for cf in cobol_files
        )
        
        if not is_in_cobol:
            # Create a generic sample file
            content = "SAMPLE001SAMPLE DATA RECORD              00100\n"
            content += "SAMPLE002SAMPLE DATA RECORD              00200\n"
            content += "SAMPLE003SAMPLE DATA RECORD              00300\n"
            
            specs.append(
                DummyFileSpec(
                    filename=py_file,
                    content=content,
                    record_count=3,
                    record_length=50,
                    field_docs=None,
                )
            )

    return specs


def create_dummy_files(
    specs: list[DummyFileSpec],
    target_dir: Path,
) -> DummyFileResult:
    """
    Create dummy files in the target directory.
    
    Returns a result with list of created files and field documentation.
    """
    created_files = []
    field_docs = {}

    try:
        target_dir.mkdir(parents=True, exist_ok=True)

        for spec in specs:
            file_path = target_dir / spec.filename
            file_path.write_text(spec.content)
            created_files.append(str(file_path))
            
            if spec.field_docs:
                field_docs[spec.filename] = spec.field_docs
            
            logger.info(
                f"Created dummy file: {spec.filename} "
                f"({spec.record_count} records, {spec.record_length} chars/record)"
            )

        return DummyFileResult(
            files_created=created_files,
            temp_dir=target_dir,
            success=True,
            error=None,
            field_docs=field_docs if field_docs else None,
        )

    except Exception as e:
        logger.exception(f"Failed to create dummy files: {e}")
        return DummyFileResult(
            files_created=created_files,
            temp_dir=target_dir,
            success=False,
            error=str(e),
            field_docs=None,
        )


def cleanup_dummy_files(files: list[str]) -> None:
    """Remove dummy files after testing."""
    for file_path in files:
        try:
            path = Path(file_path)
            if path.exists():
                path.unlink()
                logger.info(f"Cleaned up dummy file: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to clean up {file_path}: {e}")


def get_record_layout_for_tests(cobol_source: str) -> str | None:
    """
    Get a documentation string describing the record layout.
    
    This can be included in test generation prompts so the LLM
    generates correctly formatted mock data.
    """
    filename, layout = get_input_file_layout(cobol_source)
    
    if layout:
        docs = layout.get_field_documentation()
        sample = layout.generate_sample_record(1)
        
        return f"""{docs}
#
# Sample record (exactly {layout.total_length} characters, NO spaces between fields):
# "{sample}"
"""
    
    return None
