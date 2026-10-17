import json

data = json.load(open("coverage-raw.json", encoding="utf-8"))
want = ("fixtures.py", "scoring.py", "standings.py", "roster.py", "security.py")
for name, info in data["files"].items():
    if not name.endswith(want):
        continue
    missing = info["missing_lines"]
    groups = []
    for n in missing:
        if groups and n == groups[-1][1] + 1:
            groups[-1][1] = n
        else:
            groups.append([n, n])
    print(name, info["summary"]["percent_covered_display"], groups)
