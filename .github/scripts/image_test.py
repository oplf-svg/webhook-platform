"""Shows that the image keeps what docs/container.md promises. Needs Docker, and nothing that has to be installed.

  image_test.py IMAGE

Everything runs as it has to run on a cloud: read-only disk, no rights of the system, the user of the image. What was seen,
for example the code of a crash, is written to build/public/container.json.
"""

import base64
import hashlib
import hmac
import http.client
import json
import os
import pathlib
import secrets
import socket
import subprocess
import sys
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORK = ROOT / "build" / "image-test"
NAME = f"image-test-{secrets.token_hex(4)}"
HARD = ["--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "512m", "--cpus", "1", "--pids-limit", "200"]
API, WORKER = "/app/api/Platform.Api.dll", "/app/worker/Platform.Worker.dll"
PASSWORD, SECRET = secrets.token_hex(16), secrets.token_bytes(32)
DATABASE = f"Host=database;Username=platform;Password={PASSWORD};Database=platform;Maximum Pool Size=8"
FEEDS = {"feeds": [{"name": "north-web", "shop": "north", "secrets": ["whsec_" + base64.b64encode(SECRET).decode()]}]}
IMAGE = ""
FACTS = {}


def docker(*words, check=True, timeout=120, text=True, **how):
    return subprocess.run(["docker", *words], check=check, timeout=timeout, text=text, capture_output=True, **how)


def wait_until(what, look, seconds=30.0):
    end = time.monotonic() + seconds
    while not (result := look()):
        if time.monotonic() > end:
            raise AssertionError(f"after {seconds:.0f} seconds still not: {what}")
        time.sleep(0.1)
    return result


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def sql(text, user="postgres", database="platform"):
    return docker("exec", "--interactive", f"{NAME}-db", "psql", "--username", user, "--dbname", database, "--tuples-only", "--no-align", "--set", "ON_ERROR_STOP=1", "--command", text).stdout.strip()


class Box:
    """One container of the image."""

    number = 0
    all = []

    def __init__(self, *words, settings=None, feeds=None, port=None, hard=True, own=()):
        Box.number += 1
        self.name = f"{NAME}-{Box.number}"
        self.port = port
        command = ["run", "--detach", "--name", self.name, "--network", NAME, *(HARD if hard else [])]
        for key, value in (settings or {}).items():
            command += ["--env", f"{key}={value}"]
        if feeds:
            command += ["--volume", f"{feeds}:/feeds/feeds.json:ro"]
        if port:
            command += ["--publish", f"127.0.0.1:{port}:8080"]
        docker(*command, *own, IMAGE, *words)
        Box.all.append(self)

    def state(self):
        return json.loads(docker("inspect", self.name).stdout)[0]

    def running(self):
        return self.state()["State"]["Running"]

    def code(self, seconds=30.0):
        """Waits for the end and returns the exit code."""
        wait_until(f"{self.name} has ended", lambda: not self.running(), seconds)
        return self.state()["State"]["ExitCode"]

    def logs(self):
        done = docker("logs", self.name)
        return done.stdout, done.stderr

    def said(self):
        return "".join(self.logs())

    def signal(self, name):
        docker("kill", "--signal", name, self.name)

    def ask(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            connection.request(method, path, body, headers or {})
            answer = connection.getresponse()
            answer.read()
            return answer.status
        except OSError:
            return None
        finally:
            connection.close()


def event(number, sku="tea"):
    return json.dumps({"type": "order.placed", "order": f"A-{number}", "sku": sku, "quantity": 1, "cents": 100, "placed_at": "2026-11-24T12:00:00Z"}).encode()


def signed(name, body, secret=SECRET):
    moment = str(int(time.time()))
    signature = hmac.new(secret, f"{name}.{moment}.".encode() + body, hashlib.sha256).digest()
    return {"webhook-id": name, "webhook-timestamp": moment, "webhook-signature": "v1," + base64.b64encode(signature).decode(), "content-type": "application/json"}


def forms(value):
    """Every form in which a secret could stand in a log."""
    raw = value if isinstance(value, bytes) else value.encode()
    found = {raw.hex(), raw.hex().upper(), base64.b64encode(raw).decode().rstrip("="), base64.urlsafe_b64encode(raw).decode().rstrip("=")}
    return found | ({value} if isinstance(value, str) else set())


def leaks(text, values):
    return sorted(form for value in values for form in forms(value) if form in text)


class Container(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.feeds = WORK / f"{NAME}-feeds.json"
        cls.feeds.write_text(json.dumps(FEEDS), encoding="utf-8")
        cls.feeds.chmod(0o444)
        docker("network", "create", NAME)
        cls.addClassCleanup(cls.end)
        image = (ROOT / ".github" / "postgres-image").read_text(encoding="utf-8").strip()
        docker("run", "--detach", "--name", f"{NAME}-db", "--network", NAME, "--network-alias", "database", "--env", f"POSTGRES_PASSWORD={PASSWORD}", image, timeout=300)
        wait_until("the database answers", lambda: docker("exec", f"{NAME}-db", "pg_isready", "--quiet", "--host", "127.0.0.1", check=False).returncode == 0, 60)
        sql(f"create role platform login password '{PASSWORD}'", database="postgres")
        sql("create database platform owner platform", database="postgres")

    @classmethod
    def end(cls):
        for box in Box.all:
            docker("rm", "--force", "--volumes", box.name, check=False)
        docker("rm", "--force", "--volumes", f"{NAME}-db", check=False)
        docker("volume", "rm", "--force", f"{NAME}-feeds", check=False)
        docker("network", "rm", NAME, check=False)
        cls.feeds.unlink(missing_ok=True)
        public = ROOT / "build" / "public"
        public.mkdir(parents=True, exist_ok=True)
        (public / "container.json").write_text(json.dumps(FACTS, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def api(self, **how):
        box = Box(settings={"PLATFORM_DATABASE": DATABASE, "PLATFORM_FEEDS": "/feeds/feeds.json"}, feeds=self.feeds, port=free_port(), **how)
        wait_until("the API lives", lambda: box.ask("GET", "/health/live") == 200 or not box.running())
        self.assertTrue(box.running(), box.said())
        return box

    def worker(self, *words, **settings):
        return Box(WORKER, *words, settings={"PLATFORM_DATABASE": DATABASE} | settings)

    def migrate(self):
        box = self.worker("migrate")
        self.assertEqual(box.code(), 0, box.said())

    # In the order of their names, because each needs what the one before it left.

    def test_01_a_setting_that_is_missing_stops_the_start(self):
        for words, settings, name in (((), {}, "PLATFORM_DATABASE"), ((), {"PLATFORM_DATABASE": DATABASE}, "PLATFORM_FEEDS"), ((WORKER,), {}, "PLATFORM_DATABASE"), ((WORKER, "migrate"), {}, "PLATFORM_DATABASE")):
            with self.subTest(words=words, name=name):
                box = Box(*words, settings=settings)
                code = box.code()
                self.assertNotEqual(code, 0)
                self.assertIn(f"{name} is not set", box.said())
                FACTS[f"code of a crash at the start, {(words or ('api',))[0].rpartition('.')[0].rpartition('/')[2] or 'api'}"] = code

    def test_02_wrong_words(self):
        box = self.worker("other")
        self.assertEqual(box.code(), 2, box.said())

    def test_03_a_wrong_setting_is_named_and_not_repeated(self):
        box = self.worker(PLATFORM_LOOPS="many-4711")
        self.assertNotEqual(box.code(), 0)
        self.assertIn("PLATFORM_LOOPS", box.said())
        self.assertNotIn("4711", box.said())

    def test_04_feeds_that_nobody_may_read(self):
        closed = WORK / f"{NAME}-closed.json"
        closed.write_text(json.dumps(FEEDS), encoding="utf-8")
        closed.chmod(0o000)
        self.addCleanup(closed.unlink)
        box = Box(settings={"PLATFORM_DATABASE": DATABASE, "PLATFORM_FEEDS": "/feeds/feeds.json"}, feeds=closed)
        self.assertNotEqual(box.code(), 0)
        self.assertEqual(leaks(box.said(), [SECRET, PASSWORD]), [])

    def test_05_before_the_tables_are_there(self):
        api = self.api()
        self.assertEqual(api.ask("GET", "/health/live"), 200)
        self.assertEqual(api.ask("GET", "/health/ready"), 503)
        worker = self.worker()
        self.assertEqual(worker.code(), 1, worker.said())
        self.assertIn("inbox", worker.said())
        api.signal("TERM")
        self.assertEqual(api.code(40), 0)

    def test_06_migration(self):
        holder = subprocess.Popen(["docker", "exec", f"{NAME}-db", "psql", "--username", "postgres", "--dbname", "platform", "--command", "select pg_advisory_lock(4711), pg_sleep(8)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait_until("somebody else holds the lock", lambda: sql("select count(*) from pg_locks where locktype = 'advisory' and granted") == "1")
        second = self.worker("migrate")
        self.assertNotIn(second.code(20), (0, 2), second.said())
        self.assertEqual(sql("select to_regclass('inbox') is null"), "t")
        holder.wait(30)
        wait_until("the lock is free", lambda: sql("select count(*) from pg_locks where locktype = 'advisory'") == "0")
        self.migrate()
        self.migrate()
        self.assertEqual(sql("select to_regclass('inbox') is not null"), "t")

    def test_07_answers(self):
        api = self.api()
        wait_until("the API is ready", lambda: api.ask("GET", "/health/ready") == 200)
        body = event(1)
        self.assertEqual(api.ask("POST", "/hooks/north-web", body, signed("one", body)), 202)
        self.assertEqual(api.ask("POST", "/hooks/north-web", body, signed("one", body)), 200)
        self.assertEqual(api.ask("POST", "/hooks/north-web", event(2), signed("one", event(2))), 409)
        self.assertEqual(api.ask("POST", "/hooks/north-web", body, signed("two", body, b"x" * 32)), 401)
        self.assertEqual(api.ask("POST", "/hooks/south-web", body, signed("two", body)), 401)
        self.assertEqual(api.ask("POST", "/hooks/north-web", body, {}), 400)
        # Only the head: the API answers before it has read the body, and a sender that is still sending would break off.
        head = "".join(f"{key}: {value}\r\n" for key, value in (signed("large", b"") | {"host": "x", "content-length": str(256 * 1024 + 1)}).items())
        with socket.create_connection(("127.0.0.1", api.port), timeout=20) as line:
            line.sendall(f"POST /hooks/north-web HTTP/1.1\r\n{head}\r\n".encode())
            self.assertTrue(line.recv(200).startswith(b"HTTP/1.1 413"))
        self.assertEqual(api.ask("GET", "/metrics"), 404)
        api.signal("TERM")
        self.assertEqual(api.code(40), 0)

    def test_08_the_worker_makes_the_effect_and_stops_when_asked(self):
        worker = self.worker()
        wait_until("the event is done", lambda: sql("select state from inbox where event_id = 'one'") == "done")
        self.assertEqual(sql("select count(*) from orders"), "1")
        began = time.monotonic()
        worker.signal("TERM")
        self.assertEqual(worker.code(20), 0, worker.said())
        FACTS["seconds until an idle worker had ended"] = round(time.monotonic() - began, 1)

    def test_09_a_request_that_has_begun_is_finished(self):
        api = self.api()
        body = event(9)
        head = "".join(f"{key}: {value}\r\n" for key, value in (signed("nine", body) | {"host": "x", "content-length": str(len(body)), "connection": "close"}).items())
        with socket.create_connection(("127.0.0.1", api.port), timeout=20) as line:
            line.sendall(f"POST /hooks/north-web HTTP/1.1\r\n{head}\r\n".encode() + body[:10])
            time.sleep(1)
            api.signal("TERM")
            time.sleep(2)
            line.sendall(body[10:])
            answer = line.recv(200)
        self.assertTrue(answer.startswith(b"HTTP/1.1 202"), answer)
        began = time.monotonic()
        self.assertEqual(api.code(40), 0)
        FACTS["seconds until the API had ended after its last request"] = round(time.monotonic() - began, 1)
        self.assertEqual(sql("select count(*) from inbox where event_id = 'nine'"), "1")

    def test_10_the_box(self):
        api = self.api()
        self.assertEqual(docker("exec", api.name, "/usr/bin/dotnet", "--info", check=False).returncode, 0, "exec does not work, so the next line would prove nothing")
        for shell in ("/bin/sh", "/bin/bash", "/bin/busybox", "sh"):
            self.assertNotEqual(docker("exec", api.name, shell, "-c", "true", check=False).returncode, 0, shell)
        state = api.state()
        self.assertEqual((state["Config"]["User"], state["HostConfig"]["ReadonlyRootfs"], state["HostConfig"]["CapDrop"]), ("1654:1654", True, ["ALL"]))
        self.assertIn("1654", docker("top", api.name, "-eo", "uid,pid").stdout.splitlines()[1].split()[0])
        api.signal("KILL")
        self.assertEqual(api.code(), 137)

    def test_11_nothing_is_written(self):
        api = self.api(hard=False)
        body = event(11)
        wait_until("the API is ready", lambda: api.ask("GET", "/health/ready") == 200)
        self.assertEqual(api.ask("POST", "/hooks/north-web", body, signed("eleven", body)), 202)
        # What Docker itself puts into every container, and the place of the feeds file. Besides that there must be nothing.
        dockers = {"/.dockerenv", "/dev", "/dev/console", "/etc", "/etc/hostname", "/etc/hosts", "/etc/mtab", "/etc/resolv.conf", "/feeds", "/feeds/feeds.json", "/proc", "/sys"}
        self.assertEqual([line for line in docker("diff", api.name).stdout.splitlines() if line.split(" ", 1)[1] not in dockers], [])
        api.signal("TERM")
        self.assertEqual(api.code(40), 0)

    @unittest.skipUnless((ROOT / "src" / "Platform.Api" / "FeedsMode.cs").exists(), "the program that writes the feeds is not built yet (W1c)")
    def test_11b_a_first_container_writes_the_feeds(self):
        # As on ECS: the volume belongs to root, so this one container is root, but without any right of the system.
        docker("volume", "create", f"{NAME}-feeds")

        def write(content, *own):
            return Box(API, "feeds", "/feeds/feeds.json", settings={"PLATFORM_FEEDS_CONTENT": content} if content else {}, own=["--volume", f"{NAME}-feeds:/feeds", *own])

        for wrong in ("", "{", json.dumps({"feeds": []})):
            with self.subTest(wrong):
                box = write(wrong, "--user", "0:0")
                self.assertNotEqual(box.code(), 0)
                self.assertEqual(leaks(box.said(), [SECRET]), [])
        # The user of the image may not write there. If it could, the next lines would prove nothing about root.
        self.assertNotEqual(write(json.dumps(FEEDS)).code(), 0)
        box = write(json.dumps(FEEDS), "--user", "0:0")
        self.assertEqual(box.code(), 0, box.said())
        api = Box(settings={"PLATFORM_DATABASE": DATABASE, "PLATFORM_FEEDS": "/feeds/feeds.json"}, port=free_port(), own=["--volume", f"{NAME}-feeds:/feeds:ro"])
        wait_until("the API is ready", lambda: api.ask("GET", "/health/ready") == 200 or not api.running())
        body = event(12)
        self.assertEqual(api.ask("POST", "/hooks/north-web", body, signed("twelve", body)), 202, api.said())
        self.assertEqual(leaks(json.dumps(api.state()), [SECRET]), [], "the API has the content of the feeds as a setting")
        api.signal("TERM")
        self.assertEqual(api.code(40), 0)

    def test_12_logs(self):
        lines = 0
        for box in Box.all:
            out, _ = box.logs()
            self.assertEqual(leaks(box.said(), [SECRET, PASSWORD]), [], box.name)
            self.assertNotIn("placed_at", box.said(), box.name)
            for line in out.splitlines():
                record = json.loads(line)
                self.assertLessEqual({"EventId", "LogLevel", "Category", "Message"}, set(record), line)
                self.assertIn(record["LogLevel"], ("Trace", "Debug", "Information", "Warning", "Error", "Critical"))
                lines += 1
        self.assertGreater(lines, 5, "there are no lines, so nothing was checked")
        FACTS["lines of log that were read"] = lines

    def test_13_a_leak_would_be_found(self):
        for value in (SECRET, PASSWORD):
            for form in forms(value):
                self.assertEqual(len(leaks(f"a line with {form} in it", [value])) > 0, True, form)
        self.assertEqual(leaks("a line without", [SECRET, PASSWORD]), [])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    IMAGE = sys.argv.pop()
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        pathlib.Path(summary).open("a", encoding="utf-8").write(f"The container was checked against docs/container.md: `{IMAGE}`\n")
    unittest.main(verbosity=2, failfast=False)
