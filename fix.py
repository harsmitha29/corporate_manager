from app import app
import re
from pathlib import Path

# collect all valid endpoints
valid = {rule.endpoint for rule in app.url_map.iter_rules()}

broken = []

for f in Path("templates").rglob("*.html"):
    text = f.read_text(encoding="utf-8", errors="ignore")

    matches = re.findall(r"url_for\('([^']+)'", text)

    for endpoint in matches:
        if endpoint not in valid and endpoint != "static":
            broken.append((str(f), endpoint))

print("\n======= BROKEN url_for ENDPOINTS =======\n")

if not broken:
    print("No broken endpoints found.")
else:
    for file, endpoint in broken:
        print(f"{file}  ->  {endpoint}")

print("\nTotal broken:", len(broken))