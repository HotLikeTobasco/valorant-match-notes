# Valorant Match Notes

Queue. Play. Write down one thing to fix.

A small offline match journal with a Python summary tool. Record your results in a CSV file and see your win rate, total K/D/A, and overall K/D.

## Try it

Requires Python 3.9 or newer. No packages or Riot account connection needed.

```sh
python3 summary.py example.csv
```

```text
Matches: 3 | W 1 / L 1 / D 1
Win rate: 33.3%
K/D/A totals: 52/49/24
K/D: 1.06
```

The example matches are fictional. Copy `example.csv` to `matches.csv`, remove the example rows, and add your own games. Keep the header.

| Column | What to enter |
| --- | --- |
| map | Map name |
| agent | Agent played |
| result | W, L, or D |
| kills, deaths, assists | Non-negative whole numbers |
| lesson | One decision to improve next game |

```sh
python3 summary.py matches.csv
```

Win rate is wins divided by all recorded matches, including draws. K/D uses total kills divided by total deaths; zero deaths displays N/A. Empty journals are supported. Missing columns and invalid stats produce a readable error.

Your journal stays on your computer unless you share it. `matches.csv` is ignored by Git to help avoid accidentally publishing your notes. Lessons are stored for manual review; the script does not analyse them.

Unofficial fan project. Not affiliated with or endorsed by Riot Games.
