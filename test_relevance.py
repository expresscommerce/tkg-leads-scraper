import csv
from backend.scraper.utils import is_relevant_business

csv_path = "output/junkcar_houston_new_batch.csv"
query = "junk car buyer"

kept = []
filtered = []

with open(csv_path, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        name = row["business_name"]
        category = row["category"]
        if is_relevant_business(name, category, query):
            kept.append((name, category))
        else:
            filtered.append((name, category))

print(f"Total rows in CSV: {len(kept) + len(filtered)}")
print(f"Kept count: {len(kept)}")
print(f"Filtered count: {len(filtered)}")

print("\n--- SAMPLE FILTERED OUT (IRRELEVANT) ---")
for name, cat in filtered[:20]:
    print(f" - {name} ({cat})")

print("\n--- SAMPLE KEPT (RELEVANT) ---")
for name, cat in kept[:20]:
    print(f" - {name} ({cat})")
