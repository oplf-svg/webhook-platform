"""Many storms, one after the other, each with another seed. One line for each, and a sum at the end.

    python storms.py 2/6 150        the second of six parts of 150 storms

A storm that is red does not stop the others. It starts no storm that could not end in time, and says how many that were.
"""

import json
import os
import re
import subprocess
import sys
import time

from world import ROOT

FIRST = 20261125000
# The job ends after 45 minutes. A storm takes about one.
LONGEST = float(os.environ.get("STORMS_MINUTES", "37")) * 60
RECORD = ROOT / "build" / "public" / "once.json"
KILLED = ("The API killed", "A worker killed", "A process killed")
SUMS = ("Real events", "Times they were sent", "Forged or malformed requests", "kills", "Connections to the database that were cut", "The database killed", "Orders made",
        "Lost", "Handled twice", "Forged and accepted", "Anything else that is not as it should be")
BAD = SUMS[-4:]


def storm(seed):
    """Runs one storm. Returns what it counted and what is wrong, in words."""
    RECORD.unlink(missing_ok=True)
    environment = {key: value for key, value in os.environ.items() if key != "GITHUB_STEP_SUMMARY"} | {"CRASH_SEED": str(seed)}
    done = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--no-header", "--tb=short", "test_storm.py"], env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", timeout=900)
    if not RECORD.is_file():
        return {}, f"nothing was counted:\n{done.stdout[-3000:]}"
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    counted = record["counted"] | {"kills": sum(times for what, times in record["counted"].items() if what.startswith(KILLED))}
    # Three that have to agree: the test, what it found, and the numbers.
    problems = []
    if record["seed"] != seed:
        problems.append(f"the record is of the seed {record['seed']}")
    if not re.search(r"^1 passed", done.stdout, re.MULTILINE) or done.returncode:
        problems.append(f"the test is red ({done.returncode}):\n{done.stdout[-3000:]}")
    if record["found"]:
        problems.append("found: " + "; ".join(record["found"][:10]))
    problems += [f"{what}: {counted[what]}" for what in BAD if counted[what]]
    return counted, "\n".join(problems)


def main(words):
    part, of = map(int, words[0].split("/"))
    total = int(words[1])
    seeds = [FIRST + number for number in range(total) if number % of == part - 1]
    start, rows, sums, red = time.monotonic(), [], dict.fromkeys(SUMS, 0), []
    for seed in seeds:
        began = time.monotonic()
        if began - start > LONGEST:
            break
        counted, problem = storm(seed)
        took = time.monotonic() - began
        for what in SUMS:
            sums[what] += counted.get(what, 0)
        rows.append((seed, "red" if problem else "green", round(took), *(counted.get(what, "-") for what in SUMS)))
        print(" | ".join(str(cell) for cell in rows[-1]), flush=True)
        if problem:
            red.append(seed)
            print(f"seed {seed} is red: {problem}", flush=True)

    left = len(seeds) - len(rows)
    head = "| seed | | seconds | " + " | ".join(SUMS) + " |\n|" + "---|" * (len(SUMS) + 3) + "\n"
    text = f"### {len(rows)} storms, {len(red)} of them red, {left} not started\n\n" + head + "".join(f"| {' | '.join(str(cell) for cell in row)} |\n" for row in rows) + f"| together | | | {' | '.join(str(sums[what]) for what in SUMS)} |\n"
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as file:
            file.write(text + "\n")
    print(f"\ntogether | {len(rows)} storms | {len(red)} red | {left} not started | " + " | ".join(f"{what}: {sums[what]}" for what in SUMS))
    print("red seeds:", red or "none")
    # No storm at all is not a green one.
    return 1 if red or left or not rows else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
