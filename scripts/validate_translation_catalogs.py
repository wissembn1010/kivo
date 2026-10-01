#!/usr/bin/env python3
"""Read-only CSV validation; never regenerate catalogs from cached translations."""
import csv
import io
import re
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "be21c94f86403a6cdc67ae36baa082728d7d0660"


def parse(stream, label):
    catalog = {}
    for line, row in enumerate(csv.reader(stream, strict=True), 1):
        assert len(row) in (2, 3), f"{label}:{line}: expected source, translation[, context]"
        assert row[0] and row[1], f"{label}:{line}: empty source or translation"
        key = (row[0], row[2] if len(row) == 3 else "")
        assert key not in catalog, f"{label}:{line}: duplicate source/context {key!r}"
        for pattern in (r"\{\d+\}", r"%\([^)]+\)[sdf]", r"(?<!%)%[sdf]"):
            assert Counter(re.findall(pattern, row[0])) == Counter(re.findall(pattern, row[1])), f"{label}:{line}: placeholder mismatch"
        catalog[key] = row[1]
    return catalog


def main():
    for language in ("fr", "ar", "ar-TN"):
        relative = f"creditflow/translations/{language}.csv"
        with (ROOT / relative).open(encoding="utf-8", newline="") as stream:
            current = parse(stream, relative)
        old = subprocess.check_output(["git", "show", f"{BASELINE}:{relative}"], cwd=ROOT, text=True)
        # Baseline Arabic files contain a repeated identical email-title key.
        baseline = {(r[0], r[2] if len(r) > 2 else ""): r[1] for r in csv.reader(io.StringIO(old), strict=True)}
        assert not baseline.keys() - current.keys(), f"{language}: existing translations deleted"
        if language != "fr":
            assert not current.keys() - baseline.keys(), f"{language}: unrelated generated keys added"
        else:
            for source, expected in {"Business": "Entreprise", "Current Stock": "Stock actuel", "Negative": "Négatif", "Show Zero Stock": "Afficher les stocks à zéro", "Show Negative Stock Only": "Afficher uniquement les stocks négatifs"}.items():
                assert current[(source, "")] == expected, (source, current.get((source, "")))
            assert current[("CASH", "Sale Mode")] == "Au comptant"
        print(f"{language}: PASS ({len(current)} unique keys; syntax, placeholders, baseline preservation)")


if __name__ == "__main__":
    main()
