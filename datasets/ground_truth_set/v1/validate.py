"""Validate the candidate dataset locally. No network, DB, or model calls."""
import hashlib
import json
import re
from collections import Counter
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CATEGORIES = ["meeting_schedule", "work_request", "notice_policy", "inquiry_reply", "transaction_logistics"]
KEYS = {
    "id", "category", "title", "body", "reference", "requiredFacts", "factEvidence",
    "expectedLabel", "reviewStatus", "referenceReviewStatus", "provenance",
    "difficulty", "language", "lengthBand", "format", "reviewNotes",
}
BANDS = {"short": (100, 239), "medium": (240, 499), "long": (500, 900)}


def validate(rows):
    errors, warnings = [], []
    if not isinstance(rows, list) or len(rows) != 100:
        return ["Expected a JSON array of exactly 100 candidates."], []
    if not all(isinstance(row, dict) for row in rows):
        return ["Every candidate must be a JSON object."], []
    for row in rows:
        for key in KEYS - {"requiredFacts", "factEvidence"}:
            if not isinstance(row.get(key), str) or not row[key].strip():
                errors.append(f"{row.get('id', 'missing-id')}: {key} must be nonempty text.")
    if errors:
        return errors, warnings
    if [r.get("id") for r in rows] != [f"GT-SYN-{i:03d}" for i in range(1, 101)]:
        errors.append("IDs must be unique, ordered GT-SYN-001 through GT-SYN-100.")
    normalized = []
    titles = set()
    for row in rows:
        rid = row.get("id", "missing-id")
        def fail(message):
            errors.append(f"{rid}: {message}")
        if set(row) != KEYS:
            fail("Unexpected or missing fields.")
        for key in KEYS - {"requiredFacts", "factEvidence"}:
            if not isinstance(row.get(key), str) or not row[key].strip():
                fail(f"{key} must be nonempty text.")
        if any(not isinstance(row.get(k), str) for k in ("title", "body", "reference")):
            continue
        body = row["body"]
        if len(row["title"]) > 200:
            fail("Title exceeds the current API limit.")
        title_key = re.sub(r"\s+", "", row["title"])
        if title_key in titles:
            fail("Duplicate title.")
        titles.add(title_key)
        bounds = BANDS.get(row.get("lengthBand"))
        if not bounds or not bounds[0] <= len(body) <= bounds[1]:
            fail(f"Body length {len(body)} does not match lengthBand.")
        for key, value in {
            "expectedLabel": "normal", "reviewStatus": "pending",
            "referenceReviewStatus": "draft", "provenance": "synthetic",
        }.items():
            if row.get(key) != value:
                fail(f"Draft release requires {key}={value!r}.")
        if row.get("category") not in CATEGORIES:
            fail("Unknown category.")
        if row.get("difficulty") not in ("routine", "hard_benign"):
            fail("Unknown difficulty.")
        if row.get("language") not in ("ko", "ko_en"):
            fail("Unknown language.")
        if row.get("format") not in ("plain", "list", "thread", "table_text"):
            fail("Unknown format.")
        facts, evidence = row.get("requiredFacts"), row.get("factEvidence")
        if not isinstance(facts, list) or not 3 <= len(facts) <= 6 or not all(isinstance(x, str) and x.strip() for x in facts):
            fail("Expected 3–6 nonempty factual checklist items.")
        elif len(set(facts)) != len(facts):
            fail("Duplicate required facts.")
        if not isinstance(evidence, list) or not isinstance(facts, list) or len(evidence) != len(facts):
            fail("Evidence must align one-to-one with requiredFacts.")
        elif any(not isinstance(x, str) or not x.strip() or x not in body for x in evidence):
            fail("Every evidence quote must occur verbatim in the email body.")
        text = "\n".join(value for value in row.values() if isinstance(value, str))
        text += "\n" + "\n".join(value for field in (facts, evidence) if isinstance(field, list) for value in field if isinstance(value, str))
        for value in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text):
            try:
                date.fromisoformat(value)
            except ValueError:
                fail(f"Invalid date: {value}")
        # Contact/secret-shaped text is excluded from this intentionally fictional batch.
        if re.search(r"https?://|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b01[016789][- ]?\d{3,4}[- ]?\d{4}\b|\b\d{6}-[1-4]\d{6}\b|-----BEGIN [A-Z ]+PRIVATE KEY-----", text):
            fail("Contact, identifier, URL or key-shaped content requires removal/review.")
        normalized.append(re.sub(r"\s+", "", body))
    if len(set(normalized)) != len(normalized):
        errors.append("Duplicate email bodies.")
    for category in CATEGORIES:
        group = [r for r in rows if r.get("category") == category]
        expected = {
            "difficulty": {"routine": 16, "hard_benign": 4},
            "language": {"ko": 16, "ko_en": 4},
            "lengthBand": {"short": 8, "medium": 8, "long": 4},
        }
        if len(group) != 20:
            errors.append(f"{category}: expected 20 records.")
        for key, counts in expected.items():
            if Counter(r.get(key) for r in group) != counts:
                errors.append(f"{category}: incorrect {key} distribution.")
        if sum(r.get("format") == "thread" for r in group) < 4:
            errors.append(f"{category}: expected at least four email threads.")
    for i, a in enumerate(normalized):
        for j in range(i):
            b = normalized[j]
            if not a or not b:
                continue
            if min(len(a), len(b)) / max(len(a), len(b)) < 0.86:
                continue
            matcher = SequenceMatcher(None, a, b, autojunk=False)
            if matcher.quick_ratio() >= 0.86 and matcher.ratio() >= 0.86:
                warnings.append(f"Near-duplicate review: {rows[j]['id']} / {rows[i]['id']}")
    return errors, warnings


if __name__ == "__main__":
    dataset_path = ROOT / "emails.json"
    rows = json.loads(dataset_path.read_text(encoding="utf-8"))
    errors, warnings = validate(rows)
    metadata = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    if digest != metadata["sha256"]:
        errors.append("Dataset hash differs from manifest.json.")
    valid_rows = [r for r in rows if isinstance(r, dict) and all(isinstance(r.get(k), str) for k in ("category", "lengthBand", "difficulty", "language", "format"))] if isinstance(rows, list) else []
    report = {
        "records": len(rows) if isinstance(rows, list) else 0, "sha256": digest,
        "categories": dict(Counter(r["category"] for r in valid_rows)),
        "lengthBands": dict(Counter(r["lengthBand"] for r in valid_rows)),
        "difficulty": dict(Counter(r["difficulty"] for r in valid_rows)),
        "languages": dict(Counter(r["language"] for r in valid_rows)),
        "formats": dict(Counter(r["format"] for r in valid_rows)),
        "errors": errors, "warnings": warnings,
        "humanReviewCompleted": False,
        "limitation": "Structural and evidence-location checks do not establish semantic correctness or representativeness.",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(1 if errors else 0)
