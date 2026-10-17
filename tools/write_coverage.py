"""Turn a pytest-cov JSON report into the graded COVERAGE.json."""
import json
from pathlib import Path

data = json.loads(Path("coverage-raw.json").read_text(encoding="utf-8"))
files = data["files"]


def pct(parts):
    statements = missed = 0
    for name, info in files.items():
        norm = name.replace("\\", "/")
        if any(part in norm for part in parts):
            statements += info["summary"]["num_statements"]
            missed += info["summary"]["missing_lines"]
    if statements == 0:
        return 0
    return round(100 * (statements - missed) / statements, 1)


areas = {
    "fixtures": pct(["domain/fixtures.py"]),
    "scoring": pct(["domain/scoring.py"]),
    "standings": pct(["domain/standings.py"]),
    "store": pct(["services/rest.py"]),
    "roster": pct(["domain/roster.py"]),
    "rbac": pct(["core/security.py"]),
}
Path("COVERAGE.json").write_text(json.dumps(areas) + "\n", encoding="utf-8")
print(areas)
