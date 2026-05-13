#!/usr/bin/env python3
"""
dashboard-validate.py — Pre-push validation for index.html
Blocks push if any of these conditions are unmet:
  1. Exactly 1 <script> and 1 </script> tag
  2. renderSponsors function defined
  3. AUTOMATION_END marker present
  4. renderSponsors defined AFTER prospectsData
  5. AUTOMATION_END present AFTER renderSponsors
  6. No .upper() or .lower() calls (Python str methods in JS — crash silently)
  7. No str() calls in JS context (Python builtin, not valid in browser JS)
  8. No prospectGrid div
  9. prospectsData array closes before renderSponsors starts
 10. No double-dash in script comment (browsers treat --> as HTML comment end)

Run BEFORE every GitHub Pages push. Exit code 1 = blocked, 0 = OK.
"""
import sys
import re

HTML_PATH = "index.html"


def validate(path: str) -> list[str]:
    errors: list[str] = []

    with open(path, encoding="utf-8") as f:
        content = f.read()

    script_match = None  # will be lazily compiled by each check that needs it

    # ── 1. Script tag count ──────────────────────────────────────────────
    script_opens = len(re.findall(r"<script\b", content))
    script_closes = len(re.findall(r"</script>", content))
    if script_opens != 1:
        errors.append(f"[FAIL] <script> count: {script_opens} (expected 1)")
    if script_closes != 1:
        errors.append(f"[FAIL] </script> count: {script_closes} (expected 1)")

    # ── 2. renderSponsors function exists ─────────────────────────────────
    if "function renderSponsors" not in content:
        errors.append("[FAIL] function renderSponsors not found in HTML")

    # ── 3. AUTOMATION_END marker ─────────────────────────────────────────
    if "// === AUTOMATION_END ===" not in content:
        errors.append("[FAIL] AUTOMATION_END marker not found")

    # ── 4. renderSponsors after prospectsData ────────────────────────────
    data_pos = content.find("let prospectsData = [")
    render_pos = content.find("function renderSponsors")
    end_marker = content.find("// === AUTOMATION_END ===")
    script_close_pos = content.find("</script>")

    if data_pos < 0:
        errors.append("[FAIL] let prospectsData = [ not found")
    else:
        # renderSponsors must be AFTER the data array in the file
        if render_pos >= 0 and render_pos < data_pos:
            errors.append(f"[FAIL] renderSponsors (pos {render_pos}) appears before prospectsData (pos {data_pos})")
        # AUTOMATION_END must be AFTER data starts
        if end_marker < 0:
            errors.append("[FAIL] AUTOMATION_END not found")
        elif end_marker < data_pos:
            errors.append(f"[FAIL] AUTOMATION_END (pos {end_marker}) appears before prospectsData (pos {data_pos})")

    # ── 5. AUTOMATION_END before renderSponsors (script block intact) ────
    # Correct order: data section → AUTOMATION_END → renderSponsors → </script>
    if render_pos >= 0 and end_marker >= 0 and end_marker > render_pos:
        errors.append(f"[FAIL] AUTOMATION_END (pos {end_marker}) appears AFTER renderSponsors (pos {render_pos}) — render functions deleted")

    # ── 6. No Python str methods in JS ───────────────────────────────────
    for line_no, line in enumerate(content.split("\n"), 1):
        if ".upper()" in line:
            errors.append(f"[FAIL] .upper() on line {line_no}: use .toUpperCase() (JS). Full line: {line.strip()[:100]}")
        if ".lower()" in line and "toLowerCase" not in line:
            errors.append(f"[FAIL] .lower() on line {line_no}: use .toLowerCase() (JS). Full line: {line.strip()[:100]}")
        if ".capitalize()" in line:
            errors.append(f"[FAIL] .capitalize() on line {line_no}: use .charAt(0).toUpperCase() + slice(1) (JS). Full line: {line.strip()[:100]}")

    # ── 6b. No Python boolean operators in JS ─────────────────────────
    # Python: "or", "and" — JS: "||", "&&"
    # Only check inside renderSponsors function body (not data strings)
    render_func_match = re.search(
        r"function renderSponsors\s*\([^)]*\)\s*\{(.*?)(?=\n\s*function\s|\n\s*(?:document|const|let|var|renderSponsors)\s*\(|\n\s*</script>)",
        content, re.DOTALL
    )
    if render_func_match:
        func_body = render_func_match.group(1)
        for line_no, line in enumerate(func_body.split("\n"), 1):
            stripped = line.strip()
            # Skip comment lines
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            # Python 'or' keyword: whitespace-bounded, inside JS expressions
            # Not flagged: "for", "color", "border", "origin" (word parts)
            # Flagged: "notes or bio", "(x or y)", "p.notes or ''"
            if re.search(r'\bor\b', line):
                errors.append(f"[FAIL] 'or' keyword in renderSponsors: use '||' (JS). Line: {line.strip()[:100]}")
            if re.search(r'\band\b', line):
                errors.append(f"[FAIL] 'and' keyword in renderSponsors: use '&&' (JS). Line: {line.strip()[:100]}")

    # ── 6c. No Python ternary syntax in JS ────────────────────────────
    # Python: "val if condition else alt"  — JS: "condition ? val : alt"
    # Also: "(expr) if x else ''" pattern inside JS strings
    if not script_match:
        script_match = re.search(r"<script>(.*?)</script>", content, re.DOTALL)
    if script_match:
        script_body = script_match.group(1)
        for line_no, line in enumerate(script_body.split("\n"), 1):
            # Python ternary: `if <expr> else` or `if <expr> then <expr> else`
            if re.search(r'\s+if\s+\S+\s+else\b', line) or re.search(r'\s+if\s+\S+\s+then\s+', line):
                errors.append(f"[FAIL] Python ternary 'if...else' on line {line_no}: use JS ternary 'cond ? true : false'. Line: {line.strip()[:100]}")

    # ── 7. No str() calls in JS context (Python function) ────────────────
    # Be lenient — str() is used in .innerHTML in the current code legitimately.
    # Only flag if inside the script tag in suspicious patterns.
    script_match = re.search(r"<script>(.*?)</script>", content, re.DOTALL)
    if script_match:
        script_body = script_match.group(1)
        # Flag str() used as a function call in JS — valid if used as `str(something)` pattern
        for line_no, line in enumerate(script_body.split("\n"), 1):
            # Only flag str() used standalone (e.g., `str(score)` when score is already a number)
            # Pattern: `str(` followed by a non-variable like number or expression that makes no sense in JS
            if re.search(r"\bstr\(\d+\)", line):
                errors.append(f"[FAIL] str() with literal on line {line_no}: str() is not valid JS. Use String() or concatenation. Line: {line.strip()[:100]}")
            # Flag str() inside innerHTML that's clearly unnecessary
            if "str(p." in line and "String(p." not in line:
                errors.append(f"[FAIL] str(p.*) on line {line_no}: use String(p.*) or template literal in JS. Line: {line.strip()[:100]}")

    # ── 8. prospectGrid div exists ───────────────────────────────────────
    if 'id="prospectGrid"' not in content:
        errors.append('[FAIL] id="prospectGrid" div not found')

    # ── 9. prospectsData array closes before renderSponsors ──────────────
    # Correct: data array closes (]) → AUTOMATION_END → renderSponsors
    # The ] of the array is at or just before AUTOMATION_END
    if data_pos >= 0 and render_pos >= 0 and end_marker >= 0:
        # Find the ] of the prospectsData array — it's the ] that precedes AUTOMATION_END
        # or the last ] in the data section
        array_section = content[data_pos:end_marker]
        # Find the innermost ] that closes the array
        last_bracket = array_section.rfind("]")
        array_close = data_pos + last_bracket
        if array_close > render_pos:
            errors.append(f"[FAIL] prospectsData array (closes at pos {array_close}) closes after renderSponsors (pos {render_pos})")

    # ── 10. No HTML comment end in script ────────────────────────────────
    if "-->" in (script_body if script_match else ""):
        errors.append("[FAIL] --> found inside <script> block — browsers treat this as end of script")

    # ── 11. renderError banner div exists ──────────────────────────────
    if 'id="renderError"' not in content:
        errors.append('[WARN] id="renderError" banner div not found — add it to detect future JS crashes')

    # ── 12. try-catch around renderSponsors call ────────────────────────
    if script_match:
        script_body = script_match.group(1)
        if "try {" not in script_body or "catch(" not in script_body:
            errors.append("[WARN] No try-catch around renderSponsors() call — JS crashes will be silent")

    return errors


def main():
    errors = validate(HTML_PATH)
    if errors:
        print("=" * 60)
        print("DASHBOARD VALIDATION FAILED — BLOCKING PUSH")
        print("=" * 60)
        for e in errors:
            tag = e.split("]")[0] + "]"
            if "[FAIL]" in tag:
                print(f"  {e}")
            else:
                print(f"  {e}")
        print("=" * 60)
        print("Fix all [FAIL] items before pushing. [WARN] items are recommendations.")
        sys.exit(1)
    else:
        print(f"[PASS] {HTML_PATH} passed all validation checks.")
        sys.exit(0)


if __name__ == "__main__":
    main()
