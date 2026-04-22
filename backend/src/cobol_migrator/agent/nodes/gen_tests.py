from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from cobol_migrator.agent.state import AgentState
from cobol_migrator.dummy_files import get_record_layout_for_tests
from cobol_migrator.models import get_structured_model

logger = logging.getLogger(__name__)


class GeneratedTests(BaseModel):
    """Structured output from test generation LLM."""

    test_code: str = Field(description="Complete pytest test file content")
    rationale: str = Field(description="Brief explanation of test strategy")


GEN_TESTS_SYSTEM_PROMPT = """\
You are a Python testing expert. Generate pytest tests for a Python program that was translated 
from COBOL based on the following specification.

## Program Summary
{program_summary}

## I/O Contract
Inputs: {inputs}
Outputs: {outputs}
Invariants: {invariants}

## Current Python Code
```python
{python_code}
```

{lessons_context}

{record_layout_info}

## Requirements - READ CAREFULLY
1. Generate a complete pytest file that imports from main.py
2. **CRITICAL**: Do NOT use subprocess. Import and call functions directly
3. Test that the main() function runs without errors
4. Use descriptive test names

## CRITICAL: Choose the RIGHT test approach based on program type

**For programs that PRINT to stdout (DISPLAY statements):**
- Use `capsys` fixture: `capsys.readouterr().out`
- Check that printed output contains expected text

**For programs that WRITE to files (most file-processing programs):**
- Do NOT use capsys - the program writes to files, not stdout!
- Instead, check that output files exist and contain expected content
- Use `tmp_path / "OUTPUT.RPT"` and read with `.read_text()`

## CRITICAL: COBOL RECORD FORMAT
COBOL records are FIXED-WIDTH with NO separators between fields!
- Each field has an exact position and length
- Fields are concatenated directly with NO spaces between them
- Numeric fields are zero-padded on the left
- Alpha fields are space-padded on the right

## HANDLING FILE I/O PROGRAMS
If the code reads/writes files (like 'EMPLOYEE.DAT', 'INPUT.TXT', etc.):
1. Use pytest's `tmp_path` fixture to create temporary directories
2. Create mock input files with CORRECTLY FORMATTED fixed-width records
3. Use `monkeypatch.chdir(tmp_path)` to run tests in the temp directory
4. Check output files exist and contain expected content after main() runs

**EXAMPLE - CORRECT mock data format:**
```python
import os
from main import main

def test_file_processing(tmp_path, monkeypatch, capsys):
    \"\"\"Test file processing with mock data.\"\"\"
    monkeypatch.chdir(tmp_path)
    
    # COBOL records are FIXED-WIDTH! Each field must be at exact position.
    # Example: ID(6) + NAME(30) + HOURS(5) + RATE(5) = 46 chars total
    # NO SPACES between fields! Fields are concatenated directly.
    input_file = tmp_path / "EMPLOYEE.DAT"
    input_file.write_text(
        "001234John Smith                     0400001500\\n"  # 46 chars exactly
        "002345Jane Doe                       0500002000\\n"  # 46 chars exactly
    )
    
    main()
    
    output_file = tmp_path / "PAYROLL.RPT"
    assert output_file.exists(), "Output file should be created"
    content = output_file.read_text()
    assert "John Smith" in content
```

WRONG (has spaces between fields):
```
"001234John Smith                     04000 01500"  # WRONG - space before rate!
```

RIGHT (fixed-width, no spaces between fields):
```
"001234John Smith                     0400001500"   # RIGHT - 46 chars exactly
```

## Example for simple DISPLAY programs (prints to stdout)
```python
from main import main

def test_main_runs_without_error():
    main()

def test_main_output(capsys):  # capsys MUST be a parameter!
    main()
    captured = capsys.readouterr()
    assert "expected text" in captured.out
```

## Example for FILE-WRITING programs (writes to files, NOT stdout)
```python
from main import main

def test_main_runs_without_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Create input files...
    main()
    # Do NOT use capsys here - program writes to file, not stdout!

def test_output_file_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Create input files...
    main()
    # Check output FILE (not stdout):
    output = (tmp_path / "OUTPUT.RPT").read_text()
    assert "expected content" in output
```

CRITICAL RULES:
- NO subprocess - direct imports only
- All fixtures (capsys, tmp_path, monkeypatch) MUST be function parameters
- For file I/O: Create FIXED-WIDTH records with NO spaces between fields!
- Match the exact field positions from the COBOL PIC clauses
- Keep tests simple and focused
- **DO NOT use capsys for file-writing programs** - they don't print to stdout!

Generate a complete, working pytest test file.
"""

FALLBACK_TEST_TEMPLATE = '''\
"""Auto-generated tests for COBOL migration."""
from main import main


def test_main_runs_without_error():
    """Test that the main function runs without raising exceptions."""
    main()


def test_main_produces_output(capsys):
    """Test that the code produces some output."""
    main()
    captured = capsys.readouterr()
    assert captured.out.strip(), "Expected some output"
'''

FALLBACK_FILE_IO_TEST_TEMPLATE = '''\
"""Auto-generated tests for COBOL migration with file I/O."""
import os
from main import main


def test_main_runs_with_mock_files(tmp_path, monkeypatch):
    """Test that main runs when input files exist."""
    monkeypatch.chdir(tmp_path)
    
    # COBOL records are FIXED-WIDTH with NO spaces between fields!
    # Create minimal mock input file with proper format
    # Format: fields concatenated directly, no separators
    (tmp_path / "INPUT.DAT").write_text(
        "001234SAMPLE NAME                    0010000100\\n"
        "002345ANOTHER NAME                   0020000200\\n"
    )
    
    # Should not raise when input exists
    try:
        main()
    except FileNotFoundError as e:
        raise AssertionError(f"Missing input file: {e}")
'''


def _code_uses_file_io(python_code: str) -> bool:
    """Detect if the Python code does file I/O operations."""
    file_indicators = [
        "open(",
        "read_text",
        "write_text",
        ".read()",
        ".write(",
        "with open",
    ]
    code_lower = python_code.lower()
    return any(indicator.lower() in code_lower for indicator in file_indicators)


def _build_lessons_context(state: AgentState) -> str:
    """Build context from lessons learned about test generation."""
    lessons = state.get("lessons_learned", [])
    test_lessons = [lesson for lesson in lessons if "test" in lesson.lower()]

    if not test_lessons:
        return ""

    parts = ["## Lessons from previous test failures - APPLY THESE"]
    for lesson in test_lessons[-3:]:
        parts.append(f"- {lesson}")
    return "\n".join(parts)


def _build_record_layout_context(state: AgentState) -> str:
    """Build context about COBOL record layout for test mock data generation."""
    cobol_source = state.get("cobol_source", "")
    
    if not cobol_source:
        return ""
    
    layout_docs = get_record_layout_for_tests(cobol_source)
    
    if layout_docs:
        return f"""## COBOL Record Layout - USE THIS FOR MOCK DATA
{layout_docs}
**When creating mock input files, use EXACTLY this format!**
"""
    
    return ""


def gen_tests(state: AgentState) -> dict[str, Any]:
    """
    Generate pytest tests for the current draft using I/O contract.
    
    If I/O contract is available, uses LLM to generate contract-driven tests.
    Includes COBOL record layout information for correctly formatted mock data.
    """
    emit = state.get("emit", lambda t, p: None)

    drafts = state.get("python_drafts", [])
    if not drafts:
        logger.warning("gen_tests called with no drafts")
        return {"error": "No draft to test"}

    current_draft_id = state.get("current_draft_id")
    current_draft = next((d for d in drafts if d.id == current_draft_id), drafts[-1])
    python_code = current_draft.code

    io_contract = state.get("io_contract")
    program_summary = state.get("program_summary", "COBOL program")
    lessons_context = _build_lessons_context(state)
    record_layout_info = _build_record_layout_context(state)

    if io_contract:
        inputs_str = ", ".join(
            f"{p['name']}: {p['type']}" for p in io_contract.get("inputs", [])
        ) or "None"
        outputs_str = ", ".join(
            f"{p['name']}: {p['type']}" for p in io_contract.get("outputs", [])
        ) or "stdout"
        invariants_str = "\n".join(
            f"- {inv}" for inv in io_contract.get("invariants", [])
        ) or "None specified"

        prompt = GEN_TESTS_SYSTEM_PROMPT.format(
            program_summary=program_summary,
            inputs=inputs_str,
            outputs=outputs_str,
            invariants=invariants_str,
            python_code=python_code[:8000],
            lessons_context=lessons_context,
            record_layout_info=record_layout_info,
        )

        try:
            model = get_structured_model("analyze", GeneratedTests)
            result: GeneratedTests = model.invoke(prompt)
            tests = result.test_code
            logger.info(f"Generated contract-driven tests: {result.rationale[:80]}")
        except Exception as e:
            logger.warning(f"Contract-driven test generation failed: {e}, using fallback")
            tests = FALLBACK_TEST_TEMPLATE
    else:
        # Choose fallback based on whether code does file I/O
        if _code_uses_file_io(python_code):
            tests = FALLBACK_FILE_IO_TEST_TEMPLATE
            logger.info("No I/O contract available, using file I/O fallback tests")
        else:
            tests = FALLBACK_TEST_TEMPLATE
            logger.info("No I/O contract available, using basic fallback tests")

    emit("tests_generated", {"tests": tests})

    return {"generated_tests": tests}
