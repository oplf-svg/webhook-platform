"""What image.yml has to keep to: when it starts, who may do what, and what its shell steps do, tried with stand-ins.

It needs ruby (for YAML), bash and jq, and writes only to a folder that is thrown away.
"""

import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FILE = ROOT / ".github/workflows/image.yml"
LIST = ROOT / ".github/scripts/image_list.sh"
SHA = "c" * 40
IMAGE = "ghcr.io/oplf-svg/webhook-platform"


def load(text):
    done = subprocess.run(["ruby", "-ryaml", "-rjson", "-e", "puts JSON.dump(YAML.load(STDIN.read))"], input=text, capture_output=True, text=True)
    if done.returncode:
        raise ValueError(done.stderr)
    data = json.loads(done.stdout)
    data["on"] = data.pop("true", data.get("on"))  # YAML 1.1 reads "on" as true
    return data


def problems(text):
    """Everything that has to hold for this workflow. Empty if it does."""
    found = []
    def need(right, what):
        if not right:
            found.append(what)
    flow = load(text)
    need(flow["on"] == {"push": {"branches": ["main"]}}, "it starts on something else than a push to main")
    need(flow["permissions"] == {}, "it does not start from no rights")
    rights = {name: job.get("permissions") for name, job in flow["jobs"].items()}
    need(rights == {"build": {"contents": "read"}, "look": {"contents": "read"}, "push": {"packages": "write"},
                    "attest": {"attestations": "write", "id-token": "write", "packages": "write"}, "check": {"attestations": "read"}}, f"rights: {rights}")
    needs = {name: job.get("needs") for name, job in flow["jobs"].items()}
    need(needs == {"build": None, "look": "build", "push": "look", "attest": ["look", "push"], "check": ["push", "attest"]}, f"order: {needs}")
    for name, job in flow["jobs"].items():
        need(job["runs-on"] == "ubuntu-24.04" and job.get("timeout-minutes", 999) <= 45, f"{name}: the machine or its time")
        need(not {"if", "continue-on-error", "container", "services", "strategy", "environment", "uses", "secrets"} & set(job), f"{name}: a key that is not needed")
        writes = "write" in (job.get("permissions") or {}).values()
        for step in job["steps"]:
            what = step.get("uses", "")
            need(not what or re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", what), f"{name}: {what}")
            need("continue-on-error" not in step and step.get("if") in (None, "${{ always() }}"), f"{name}: a step may fail or be skipped")
            need(not writes or "checkout" not in what, f"{name}: has the repo's code beside a right to write")
            need(not writes or ".github/scripts" not in step.get("run", "") and "python" not in step.get("run", ""), f"{name}: runs the repo's code beside a right to write")
            need("${{" not in step.get("run", ""), f"{name}: text from outside in a command")
            # Bash goes on when the first of two fails, so such a line would look like a check and be none.
            need("&&" not in step.get("run", ""), f"{name}: two commands in one line, and the first may fail without a word")
            if "checkout" in what:
                need(step["with"].get("persist-credentials") is False, f"{name}: the token stays on the disk")
            if "actions/attest" in what:
                need(step["with"]["subject-digest"] == "${{ needs.push.outputs.digest }}" and step["with"]["subject-name"] == "${{ env.IMAGE }}", f"{name}: signs something else")
                need(step["with"]["push-to-registry"] is True and step["with"]["create-storage-record"] is False, f"{name}: where the statement goes")
    attests = [step["with"].get("sbom-path") for step in flow["jobs"]["attest"]["steps"] if "actions/attest" in step.get("uses", "")]
    need(attests == [None, "in/contents.spdx.json"], f"what is signed: {attests}")
    need("secrets." not in text and "${{ secrets" not in text, "a secret")
    need(text.count("id-token") == 1, "id-token stands somewhere else too")
    runs = {name: [step["run"].strip() for step in job["steps"] if "run" in step] for name, job in flow["jobs"].items()}
    need(runs["build"] == ["bash .github/scripts/tests.sh"], f"build runs {runs['build']}")
    need(runs["look"] == ["python3 .github/scripts/test_image_look.py", 'python3 .github/scripts/image_look.py in/image.tar "$GITHUB_SHA" >> "$GITHUB_OUTPUT"',
                          'bash .github/scripts/image_list.sh in/image.tar contents.spdx.json "$GITHUB_SHA"',
                          """echo "contents=$(sha256sum contents.spdx.json | cut -d ' ' -f 1)" >> "$GITHUB_OUTPUT\""""], f"look runs {runs['look']}")
    need(flow["jobs"]["look"]["outputs"] == {"tar": "${{ steps.look.outputs.tar }}", "config": "${{ steps.look.outputs.config }}", "contents": "${{ steps.list.outputs.contents }}"}, "what look hands on")
    # Whatever a job with a right to write takes from another job is compared with its fingerprint before it is used.
    for name, number, setting, of, file in (("push", 2, "TAR", "tar", "in/image.tar"), ("attest", 1, "CONTENTS", "contents", "in/contents.spdx.json")):
        step = flow["jobs"][name]["steps"][number]
        need(step.get("env", {}).get(setting) == f"${{{{ needs.look.outputs.{of} }}}}" and f'echo "${setting}  {file}" | sha256sum --check --strict' in step.get("run", ""), f"{name} takes {file} by its name")
        need(all("actions/attest" not in one.get("uses", "") and "docker load" not in one.get("run", "") for one in flow["jobs"][name]["steps"][:number]), f"{name} uses {file} before it is compared")
    tars = [step["with"] for step in flow["jobs"]["build"]["steps"] if step.get("with", {}).get("name") == "image"]
    need([(one["path"], one["if-no-files-found"]) for one in tars] == [("build/image.tar", "error")], "the image that was tried is not the one that is handed on")
    need(flow["defaults"] == {"run": {"shell": "bash"}}, "a command in a row of commands could fail without a word")
    return found


def list_problems(text):
    """What has to hold for the script that makes the list."""
    found = []
    for words in ('--user "$(id -u):$(id -g)"', "--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges", ":/in:ro", "set -euo pipefail", "--contents"):
        if words not in text:
            found.append(f"{words} is missing")
    if not re.search(r"(?m)^syft=ghcr\.io/anchore/syft@sha256:[0-9a-f]{64}$", text):
        found.append("the tool is not pinned")
    return found


class Form(unittest.TestCase):
    def setUp(self):
        self.text = FILE.read_text(encoding="utf-8")

    def test_it_has_the_right_form(self):
        self.assertEqual(problems(self.text), [])

    def test_mistakes_are_noticed(self):
        mistakes = {
            "a pull request": ("  push:\n    branches: [main]\n", "  push:\n    branches: [main]\n  pull_request:\n"),
            "every branch": ("    branches: [main]\n", "    branches: ['**']\n"),
            "by hand": ("on:\n", "on:\n  workflow_dispatch:\n"),
            "all rights": ("permissions: {}\n", "permissions: write-all\n"),
            "build may write": ("    timeout-minutes: 45\n    permissions:\n      contents: read\n", "    timeout-minutes: 45\n    permissions:\n      contents: read\n      packages: write\n"),
            "push may sign": ("      packages: write\n    outputs:\n", "      packages: write\n      id-token: write\n    outputs:\n"),
            "push without the look": ("    needs: look\n", "    needs: build\n"),
            "attest without the push": ("  attest:\n    needs: [look, push]\n", "  attest:\n    needs: look\n"),
            "attest without the look": ("  attest:\n    needs: [look, push]\n", "  attest:\n    needs: push\n"),
            "the list is taken by its name": ('          echo "$CONTENTS  in/contents.spdx.json" | sha256sum --check --strict\n', ""),
            "the list is compared with nothing": ("          CONTENTS: ${{ needs.look.outputs.contents }}\n", "          CONTENTS: ${{ needs.look.outputs.tar }}\n"),
            "the fingerprint of the list comes from elsewhere": ("      contents: ${{ steps.list.outputs.contents }}\n", "      contents: ${{ steps.look.outputs.contents }}\n"),
            "a check that cannot fail": ('          [ -n "$TAR" ]\n          [ -n "$CONFIG" ]\n', '          [ -n "$TAR" ] && [ -n "$CONFIG" ]\n'),
            "the image is taken by its name": ('          echo "$TAR  in/image.tar" | sha256sum --check --strict\n', ""),
            "an action by a tag": ("actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6", "actions/attest@v4"),
            "somebody else's action": ("actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6", "docker/build-push-action@1e69f48acb82d1966a394da916b4c1698aa569d6"),
            "the token stays": ("          persist-credentials: false\n", "          persist-credentials: true\n"),
            "text from outside": ('contents.spdx.json "$GITHUB_SHA"', 'contents.spdx.json "${{ github.event.head_commit.message }}"'),
            "main without the tests": ("run: bash .github/scripts/tests.sh", "run: bash .github/scripts/image_try.sh"),
            "another file is handed on": ("          path: build/image.tar\n", "          path: build/other.tar\n"),
            "nothing is handed on and nobody says so": ("          path: build/image.tar\n          if-no-files-found: error\n", "          path: build/image.tar\n          if-no-files-found: ignore\n"),
            "no list": ('      - run: bash .github/scripts/image_list.sh in/image.tar contents.spdx.json "$GITHUB_SHA"\n', ""),
            "signs something else": ("          subject-digest: ${{ needs.push.outputs.digest }}\n", "          subject-digest: ${{ needs.look.outputs.config }}\n"),
            "no list signed": ("          sbom-path: in/contents.spdx.json\n", ""),
            "a step may fail": ("      - run: python3 .github/scripts/test_image_look.py\n", "      - run: python3 .github/scripts/test_image_look.py\n        continue-on-error: true\n"),
            "a job may be skipped": ("  check:\n    needs: [push, attest]\n", "  check:\n    if: ${{ always() }}\n    needs: [push, attest]\n"),
            "a secret": ("          GH_TOKEN: ${{ github.token }}\n        run: docker login", "          GH_TOKEN: ${{ secrets.TOKEN }}\n        run: docker login"),
            "the repo's code beside a right": ('          docker load --input in/image.tar\n', '          python3 .github/scripts/image_look.py in/image.tar "$GITHUB_SHA"\n          docker load --input in/image.tar\n'),
            "another machine": ("runs-on: ubuntu-24.04\n    timeout-minutes: 10\n    permissions:\n      attestations: write", "runs-on: ubuntu-latest\n    timeout-minutes: 10\n    permissions:\n      attestations: write"),
            "errors in a row are lost": ("defaults:\n  run:\n    shell: bash\n", "defaults:\n  run:\n    shell: sh\n"),
        }
        for name, (old, new) in mistakes.items():
            with self.subTest(name):
                self.assertIn(old, self.text)
                self.assertNotEqual(problems(self.text.replace(old, new)), [])


    def test_the_script_of_the_list(self):
        text = LIST.read_text(encoding="utf-8")
        self.assertEqual(list_problems(text), [])
        for words in ('--user "$(id -u):$(id -g)" ', "--network none ", "--read-only ", "--cap-drop ALL ", "--security-opt no-new-privileges ", ":ro", "set -euo pipefail", " --contents"):
            with self.subTest(words):
                self.assertNotEqual(list_problems(text.replace(words, "")), [])
        self.assertNotEqual(list_problems(re.sub(r"@sha256:[0-9a-f]{64}", ":latest", text)), [])


STANDINS = {
    "docker": r'''#!/bin/bash
echo "docker $*" >> "$CALLS"
case "$1 $2 $3" in
  "buildx imagetools inspect")
    if [ "$4" = --raw ]; then cat "$MANIFEST"; exit 0; fi
    echo "$THERE_SAYS" >&2; exit "$THERE";;
  "run --rm --user") cat "$CONTENTS"; exit "${SYFT_CODE:-0}";;
  "push "*) exit "${PUSH_CODE:-0}";;
esac''',
    "gh": r'''#!/bin/bash
echo "gh $*" >> "$CALLS"
case "$*" in
  *mcr.microsoft.com*) [ "$MODE" = blind ] && exit 0; [ "$MODE" = away ] && { echo "could not reach" >&2; exit 1; }; echo "HTTP 404: Not Found" >&2; exit 1;;
  *refs/heads/other*) [ "$MODE" = wide ] && exit 0; echo "expected SourceRepositoryRef to be refs/heads/other, got refs/heads/main" >&2; exit 1;;
  *0000000000*) [ "$MODE" = old ] && exit 0; echo "expected SourceRepositoryDigest to be 0, got c" >&2; exit 1;;
  *spdx.dev*) [ "$MODE" = nolist ] && { echo "HTTP 404" >&2; exit 1; }; exit 0;;
  *) [ "$MODE" = unsigned ] && { echo "HTTP 404" >&2; exit 1; }; exit 0;;
esac''',
    "curl": r'''#!/bin/bash
echo "curl $*" >> "$CALLS"
case "$*" in
  *token?service*) [ "$MODE" = private ] && exit 22; echo '{"token": "everybody"}';;
  *) [ "$MODE" = hidden ] && exit 22; while [ $# -gt 0 ]; do [ "$1" = --output ] && cp "$MANIFEST" "$2"; shift; done;;
esac''',
    "sha256sum": r'''#!/usr/bin/env python3
import hashlib, sys
def of(path): return hashlib.sha256(open(path, "rb").read()).hexdigest()
if "--check" in sys.argv:
    lines = [line.split("  ", 1) for line in sys.stdin.read().splitlines()]
    sys.exit(0 if lines and all(len(line) == 2 and of(line[1]) == line[0] for line in lines) else 1)
print(f"{of(sys.argv[1])}  {sys.argv[1]}")''',
}


class Steps(unittest.TestCase):
    """Each shell step, as bash would run it on GitHub, with stand-ins for what it calls."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = pathlib.Path(folder.name)
        (self.folder / "in").mkdir()
        (self.folder / "in/image.tar").write_bytes(b"the image")
        self.manifest = self.folder / "registry.json"
        self.manifest.write_text(json.dumps({"config": {"digest": "sha256:config"}}))
        tools = self.folder / "bin"
        tools.mkdir()
        for name, text in STANDINS.items():
            (tools / name).write_text(text)
            (tools / name).chmod(0o755)
        self.env = {"PATH": f"{tools}:/usr/bin:/bin:/opt/homebrew/bin", "HOME": str(self.folder), "CALLS": str(self.folder / "calls"), "MANIFEST": str(self.manifest), "CONTENTS": str(self.folder / "contents"),
                    "GITHUB_SHA": SHA, "GITHUB_ACTOR": "oplf-svg", "GITHUB_REPOSITORY": "oplf-svg/webhook-platform", "GITHUB_OUTPUT": str(self.folder / "output"), "GITHUB_STEP_SUMMARY": str(self.folder / "summary"),
                    "IMAGE": IMAGE, "GH_TOKEN": "token", "MODE": "", "THERE": "1", "THERE_SAYS": f"ERROR: {IMAGE}:{SHA}: not found"}
        self.flow = load(FILE.read_text(encoding="utf-8"))

    def step(self, job, number, **more):
        step = [one for one in self.flow["jobs"][job]["steps"] if "run" in one][number]
        done = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]], cwd=self.folder, env=self.env | more, capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    def calls(self):
        path = self.folder / "calls"
        return path.read_text() if path.exists() else ""

    def sha(self, path):
        return hashlib.sha256((self.folder / path).read_bytes()).hexdigest()

    def push(self, **more):
        return self.step("push", 1, **({"TAR": self.sha("in/image.tar"), "CONFIG": "sha256:config"} | more))

    def test_push(self):
        code, said = self.push()
        self.assertEqual(code, 0, said)
        self.assertEqual((self.folder / "output").read_text(), f"digest=sha256:{self.sha('registry.json')}\n")
        self.assertIn(f"docker push {IMAGE}:{SHA}", self.calls())
        self.assertIn(self.sha("registry.json"), (self.folder / "summary").read_text())

    def test_push_stops(self):
        for name, more in {"another file": {"TAR": "0" * 64}, "no fingerprint of the file": {"TAR": ""}, "there is one": {"THERE": "0"}, "the registry is away": {"THERE_SAYS": "ERROR: timeout"},
                           "another image arrived": {"CONFIG": "sha256:other"}, "no settings named": {"CONFIG": ""}}.items():
            with self.subTest(name):
                (self.folder / "output").unlink(missing_ok=True)
                code, said = self.push(**more)
                self.assertNotEqual(code, 0, said)
                self.assertFalse((self.folder / "output").exists(), "a fingerprint was handed on")
                if name not in ("another image arrived", "no settings named"):
                    self.assertNotIn("docker push", self.calls())

    def test_push_that_fails(self):
        code, _ = self.push(PUSH_CODE="1")
        self.assertNotEqual(code, 0)
        self.assertFalse((self.folder / "output").exists())

    def test_the_first_package(self):
        for said in (f"ERROR: {IMAGE}:{SHA}: not found", "ERROR: denied", "ERROR: failed to authorize: 403 Forbidden", "MANIFEST_UNKNOWN: manifest unknown", "NAME_UNKNOWN"):
            with self.subTest(said):
                self.assertEqual(self.push(THERE_SAYS=said)[0], 0)

    def test_sign_in_never_shows_the_token(self):
        for job, number in (("push", 0), ("attest", 1)):
            code, said = self.step(job, number)
            self.assertEqual(code, 0)
            self.assertNotIn("token", said + self.calls().replace("docker login ghcr.io --username oplf-svg --password-stdin", ""))

    def list(self, contents, **more):
        (self.folder / ".github/scripts").mkdir(parents=True, exist_ok=True)
        shutil.copy(LIST, self.folder / ".github/scripts")
        (self.folder / ".github/scripts/image_look.py").write_text("import sys, json, pathlib\nsys.exit(0 if json.loads(pathlib.Path(sys.argv[3]).read_text()).get('right') else 1)\n")
        (self.folder / "contents").write_text(contents)
        return self.step("look", 2, **more)

    def test_list(self):
        self.assertEqual(self.list('{"right": true}')[0], 0)
        self.assertIn("--network none", self.calls())

    def test_list_that_is_not_right(self):
        self.assertNotEqual(self.list("{}")[0], 0)
        self.assertNotEqual(self.list('{"right": true}', SYFT_CODE="1")[0], 0)
        self.assertNotEqual(self.list("")[0], 0)

    def test_the_list_that_is_signed(self):
        (self.folder / "in/contents.spdx.json").write_text("the list")
        right = self.sha("in/contents.spdx.json")
        self.assertEqual(self.step("attest", 0, CONTENTS=right)[0], 0)
        for name, wrong in {"another list": "0" * 64, "no fingerprint": "", "the fingerprint of something else": self.sha("in/image.tar"), "more than one line": f"{right}  in/contents.spdx.json\n{'0' * 64}"}.items():
            with self.subTest(name):
                self.assertNotEqual(self.step("attest", 0, CONTENTS=wrong)[0], 0)
        (self.folder / "in/contents.spdx.json").write_text("the list of somebody else")
        self.assertNotEqual(self.step("attest", 0, CONTENTS=right)[0], 0)
        (self.folder / "in/contents.spdx.json").unlink()
        self.assertNotEqual(self.step("attest", 0, CONTENTS=right)[0], 0)

    def test_the_fingerprint_of_the_list(self):
        (self.folder / "contents.spdx.json").write_text("the list")
        code, said = self.step("look", 3)
        self.assertEqual((code, (self.folder / "output").read_text()), (0, f"contents={self.sha('contents.spdx.json')}\n"))

    def test_check(self):
        digest = f"sha256:{self.sha('registry.json')}"
        for number in (0, 1):
            code, said = self.step("check", number, DIGEST=digest, OTHER="mcr.microsoft.com/dotnet/aspnet@sha256:0")
            self.assertEqual(code, 0, said)
        self.assertEqual(self.calls().count("gh attestation verify"), 5)

    def test_check_that_has_to_be_red(self):
        digest = f"sha256:{self.sha('registry.json')}"
        for number, mode in ((0, "private"), (0, "hidden"), (1, "unsigned"), (1, "nolist"), (1, "wide"), (1, "old"), (1, "blind"), (1, "away")):
            with self.subTest(mode):
                code, said = self.step("check", number, DIGEST=digest, OTHER="mcr.microsoft.com/dotnet/aspnet@sha256:0", MODE=mode)
                self.assertNotEqual(code, 0, said)
        with self.subTest("another manifest"):
            self.assertNotEqual(self.step("check", 0, DIGEST="sha256:" + "0" * 64, OTHER="x")[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
