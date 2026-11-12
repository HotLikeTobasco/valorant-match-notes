"""Summarise manually recorded Valorant matches from a CSV file."""

import argparse
import csv
from collections import Counter


def summarise(path):
    results = Counter()
    kills = deaths = assists = 0
    with open(path, newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        required = {"map", "agent", "result", "kills", "deaths", "assists", "lesson"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(
                "missing columns: "
                + ", ".join(sorted(required - set(reader.fieldnames or [])))
            )
        for row in reader:
            if None in row or any(row.get(key) is None for key in required):
                raise ValueError(
                    f"line {reader.line_num}: row does not match CSV header"
                )
            result = (row["result"] or "").strip().upper()
            if result not in {"W", "L", "D"}:
                raise ValueError(f"line {reader.line_num}: result must be W, L, or D")
            try:
                stats = [int(row[key]) for key in ("kills", "deaths", "assists")]
            except (ValueError, TypeError):
                raise ValueError(
                    f"line {reader.line_num}: K/D/A must be whole numbers"
                ) from None
            if min(stats) < 0:
                raise ValueError(f"line {reader.line_num}: K/D/A cannot be negative")
            kills += stats[0]
            deaths += stats[1]
            assists += stats[2]
            results[result] += 1
    total = sum(results.values())
    ratio = f"{kills / deaths:.2f}" if deaths else "N/A (zero deaths)"
    rate = f"{results['W'] / total:.1%}" if total else "N/A"
    return (
        f"Matches: {total} | W {results['W']} / L {results['L']} / D {results['D']}\n"
        f"Win rate: {rate}\nK/D/A totals: {kills}/{deaths}/{assists}\nK/D: {ratio}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file")
    args = parser.parse_args()
    try:
        print(summarise(args.csv_file))
    except (OSError, ValueError, csv.Error) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    main()
