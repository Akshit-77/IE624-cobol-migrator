from __future__ import annotations

import ast
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from cobol_migrator.agent.state import AgentState
from cobol_migrator.dummy_files import get_record_layout_for_tests
from cobol_migrator.models import get_structured_model

logger = logging.getLogger(__name__)


def _validate_test_syntax(test_code: str) -> tuple[bool, str]:
    """
    Validate that test code is syntactically valid Python.
    Returns (is_valid, error_message).
    """
    try:
        ast.parse(test_code)
        return True, ""
    except SyntaxError as e:
        return False, f"Syntax error at line {e.lineno}: {e.msg}"


def _validate_test_structure(test_code: str) -> tuple[bool, list[str]]:
    """
    Validate that test code has required structure.
    Returns (is_valid, list_of_issues).
    """
    issues = []
    
    # Must import from main
    if "from main import" not in test_code and "import main" not in test_code:
        issues.append("Missing import from main.py")
    
    # Must have at least one test function
    if "def test_" not in test_code:
        issues.append("No test functions found (must start with 'def test_')")
    
    # Should not use subprocess
    if "subprocess" in test_code:
        issues.append("Uses subprocess (forbidden - use direct imports)")
    
    # Check for common hallucination patterns
    hallucination_patterns = [
        (r"import capsys", "capsys is a fixture, not an import"),
        (r"from pytest import capsys", "capsys is a fixture, not an import"),
        (r"capsys\s*=", "capsys must be a function parameter, not assigned"),
        (r"assert\s+\d+\s*==\s*\d+", "Hardcoded numeric comparison (likely hallucinated)"),
    ]
    
    for pattern, message in hallucination_patterns:
        if re.search(pattern, test_code):
            issues.append(message)
    
    return len(issues) == 0, issues


def _extract_test_functions(test_code: str) -> list[str]:
    """Extract test function names from test code."""
    return re.findall(r"def (test_\w+)\s*\(", test_code)


def _sanitize_test_code(test_code: str) -> str:
    """
    Clean up common issues in generated test code.
    """
    # Remove markdown code fences if present
    test_code = re.sub(r"^```python\s*\n?", "", test_code)
    test_code = re.sub(r"^```\s*\n?", "", test_code, flags=re.MULTILINE)
    test_code = re.sub(r"\n?```$", "", test_code)
    
    # Fix common capsys mistakes
    test_code = re.sub(r"import capsys\n?", "", test_code)
    test_code = re.sub(r"from pytest import capsys\n?", "", test_code)
    
    # Ensure proper imports at top
    if "from main import" not in test_code and "import main" not in test_code:
        test_code = "from main import main\n\n" + test_code
    
    return test_code.strip()


class GeneratedTests(BaseModel):
    """Structured output from test generation LLM."""

    test_code: str = Field(description="Complete pytest test file content")
    rationale: str = Field(description="Brief explanation of test strategy")


GEN_TESTS_SYSTEM_PROMPT = """\
Generate simple, reliable pytest tests for this Python code translated from COBOL.

## Python Code to Test
```python
{python_code}
```

## Program Info
Summary: {program_summary}
Inputs: {inputs}
Outputs: {outputs}

{lessons_context}

{record_layout_info}

## RULES - Follow exactly

1. **Import**: `from main import main` (NEVER use subprocess)

2. **Fixtures are PARAMETERS, not imports**:
   - CORRECT: `def test_foo(capsys):` then `capsys.readouterr()`
   - WRONG: `import capsys` or `capsys = ...`

3. **Choose test type based on code behavior**:
   - Code uses `print()` → use `capsys` fixture
   - Code uses `open()` to write files → check file exists, DON'T use capsys

4. **For file I/O programs**:
```python
def test_with_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Create input file (FIXED-WIDTH, NO spaces between fields!)
    (tmp_path / "INPUT.DAT").write_text("001234DATA HERE    00100\\n")
    main()
    assert (tmp_path / "OUTPUT.RPT").exists()
```

5. **Use SIMPLE assertions** - avoid exact value matching:
```python
# GOOD - checks structure, not exact values
assert output_file.exists()
assert "TOTAL" in content
assert len(content) > 0

# BAD - prone to errors
assert content == "exact string"  # Too brittle
assert "2750.00" in content  # Only if you calculated it!
```

6. **If you must check numeric values, CALCULATE and COMMENT**:
```python
# Input: 100 + 200 + 300 = 600
assert "600" in content  # Sum of input values
```

## Generate 2-3 simple tests:
1. `test_main_runs_without_error` - just call main()
2. `test_produces_output` - verify some output exists
3. (optional) `test_output_format` - check output structure

Keep tests SIMPLE. Prefer existence checks over exact value matching.
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


def _get_appropriate_fallback(python_code: str, state: AgentState) -> str:
    """Get the appropriate fallback test based on code characteristics."""
    if _code_uses_file_io(python_code):
        # Check if we have dummy file specs to use
        dummy_specs = state.get("dummy_file_specs", [])
        if dummy_specs:
            # Generate a more specific fallback for file I/O with known files
            input_files = [s for s in dummy_specs if "input" in s.filename.lower() or ".dat" in s.filename.lower()]
            if input_files:
                spec = input_files[0]
                return f'''\
"""Auto-generated tests for COBOL migration with file I/O."""
from main import main


def test_main_runs_with_mock_files(tmp_path, monkeypatch):
    """Test that main runs when input files exist."""
    monkeypatch.chdir(tmp_path)
    
    # Create input file with sample data
    (tmp_path / "{spec.filename}").write_text(
        "{spec.content[:100]}\\n"
    )
    
    # Should not raise when input exists
    try:
        main()
    except FileNotFoundError as e:
        raise AssertionError(f"Missing input file: {{e}}")


def test_main_completes(tmp_path, monkeypatch):
    """Test that main completes without error."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "{spec.filename}").write_text("{spec.content[:100]}\\n")
    main()  # Should complete without raising
'''
        return FALLBACK_FILE_IO_TEST_TEMPLATE
    else:
        return FALLBACK_TEST_TEMPLATE


def gen_tests(state: AgentState) -> dict[str, Any]:
    """
    Generate pytest tests for the current draft using I/O contract.
    
    Includes validation to ensure generated tests are syntactically correct
    and don't contain common hallucination patterns. Falls back to safe
    templates if LLM output is invalid.
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

    # Get appropriate fallback for this code type
    fallback_tests = _get_appropriate_fallback(python_code, state)
    tests = fallback_tests  # Default to fallback

    if io_contract:
        inputs_str = ", ".join(
            f"{p['name']}: {p['type']}" for p in io_contract.get("inputs", [])
        ) or "None"
        outputs_str = ", ".join(
            f"{p['name']}: {p['type']}" for p in io_contract.get("outputs", [])
        ) or "stdout"

        prompt = GEN_TESTS_SYSTEM_PROMPT.format(
            program_summary=program_summary,
            inputs=inputs_str,
            outputs=outputs_str,
            python_code=python_code[:6000],  # Reduced to leave room for response
            lessons_context=lessons_context,
            record_layout_info=record_layout_info,
        )

        try:
            model = get_structured_model("analyze", GeneratedTests)
            result: GeneratedTests = model.invoke(prompt)
            generated_tests = _sanitize_test_code(result.test_code)
            
            # Validate syntax
            syntax_valid, syntax_error = _validate_test_syntax(generated_tests)
            if not syntax_valid:
                logger.warning(f"Generated tests have syntax error: {syntax_error}")
                emit("test_generation_warning", {
                    "warning": f"LLM generated invalid syntax: {syntax_error}",
                    "using_fallback": True
                })
                # Use fallback
            else:
                # Validate structure
                structure_valid, issues = _validate_test_structure(generated_tests)
                if not structure_valid:
                    logger.warning(f"Generated tests have structure issues: {issues}")
                    emit("test_generation_warning", {
                        "warning": f"LLM generated problematic tests: {', '.join(issues)}",
                        "using_fallback": True
                    })
                    # Use fallback
                else:
                    # Tests passed validation
                    tests = generated_tests
                    test_funcs = _extract_test_functions(tests)
                    logger.info(
                        f"Generated valid tests with {len(test_funcs)} functions: "
                        f"{', '.join(test_funcs[:3])}"
                    )
                    
        except Exception as e:
            logger.warning(f"Test generation LLM call failed: {e}, using fallback")
            emit("test_generation_warning", {
                "warning": f"LLM call failed: {str(e)[:100]}",
                "using_fallback": True
            })
    else:
        logger.info("No I/O contract available, using fallback tests")

    emit("tests_generated", {"tests": tests})

    return {"generated_tests": tests}
