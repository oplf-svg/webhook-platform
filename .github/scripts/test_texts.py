"""The few lines that have to be the same everywhere: whose this is, and who made what. And no link that leads nowhere."""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
HOME = "https://github.com/oplf-svg"


def problems(readme, notice, exists):
    found = []
    lines = notice.splitlines()
    owner = re.fullmatch(r"Copyright 20\d\d (\S.*\S)", lines[1]) if len(lines) > 1 else None
    if not owner or lines[0] != "webhook-platform":
        return ["NOTICE does not say whose this is"]
    name = owner.group(1)
    if not "".join(readme.splitlines()[2:3]).startswith(f"Part of the public portfolio of {name}."):
        found.append("line 3 of the README does not say whose this is")
    if HOME not in "\n".join(readme.splitlines()[2:5]):
        found.append("the README does not say where the rest of it is")
    made = re.search(r"(?s)\n## Who made this\n(.*?)(?=\n## |\Z)", readme)
    told = re.sub(r"\s+", " ", made.group(1)) if made else ""
    if f"Directed and approved by {name}. Code, tests and texts are written by Claude, an AI agent that proposes its changes under an identity of its own." not in told:
        found.append("the README does not say who made what")
    if "(LICENSE)" not in told or "(NOTICE)" not in told:
        found.append("the README does not name the licence")
    for link in re.findall(r"\]\(([^)#\s]+)", readme):
        if not link.startswith(("https://", "../../actions/")) and not exists(link):
            found.append(f"{link} leads nowhere")
    return found


class Texts(unittest.TestCase):
    def setUp(self):
        self.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.notice = (ROOT / "NOTICE").read_text(encoding="utf-8")

    def test_right(self):
        self.assertEqual(problems(self.readme, self.notice, lambda link: (ROOT / link).exists()), [])

    def test_links_of_the_pages(self):
        for page in sorted((ROOT / "docs").glob("*.md")):
            for link in re.findall(r"\]\(([^)#\s]+)", page.read_text(encoding="utf-8")):
                if not link.startswith("https://"):
                    self.assertTrue((page.parent / link).exists(), f"{page.name}: {link}")

    def test_mistakes_are_noticed(self):
        name = self.notice.splitlines()[1].split(" ", 2)[2]
        mistakes = {
            "another name in NOTICE": (self.readme, self.notice.replace(name, "Another Name")), "no name in NOTICE": (self.readme, self.notice.replace(f"Copyright 2026 {name}\n", "")),
            "no name in the README": (self.readme.replace(f"portfolio of {name}", "portfolio of somebody"), self.notice), "no address": (self.readme.replace(f"]({HOME})", "](https://github.com/somebody)"), self.notice), "the line is gone": (self.readme.replace(self.readme.splitlines()[2] + "\n", "", 1), self.notice), "another heading": (self.readme.replace("## Who made this", "## Thanks"), self.notice),
            "who made what is gone": (self.readme.replace("Directed and approved", "Reviewed"), self.notice), "more is claimed": (self.readme.replace("Code, tests and texts are written by Claude", "Some code is written by Claude"), self.notice),
            "the licence is gone": (self.readme.replace("(LICENSE)", "()"), self.notice), "NOTICE is gone": (self.readme.replace("(NOTICE)", "()"), self.notice), "a dead link": (self.readme + "\n[x](docs/nowhere.md)\n", self.notice),
        }
        for what, (readme, notice) in mistakes.items():
            with self.subTest(what):
                self.assertNotEqual(problems(readme, notice, lambda link: (ROOT / link).exists()), [])


if __name__ == "__main__":
    unittest.main()
