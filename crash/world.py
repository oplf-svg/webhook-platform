"""The system as it really runs, seen from outside: a database in a container, and processes that can be killed."""

import collections
import contextlib
import json
import os
import pathlib
import random
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import psycopg

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
IMAGE = (ROOT / ".github" / "postgres-image").read_text(encoding="utf-8").strip()


def wait_until(what, check, seconds=30.0, pause=0.05):
    """Waits until the check is true and returns what it returned. Says what it waited for if that never happens."""
    end = time.monotonic() + seconds
    while True:
        result = check()
        if result:
            return result
        if time.monotonic() > end:
            raise AssertionError(f"after {seconds:.0f} seconds still not: {what}")
        time.sleep(pause)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def is_stopped(pid):
    """Whether the process is stopped, as the system sees it."""
    if sys.platform == "linux":
        with contextlib.suppress(FileNotFoundError):
            return pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rpartition(")")[2].split()[0] == "T"
        return False
    return subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], stdout=subprocess.PIPE, text=True, check=False).stdout.startswith("T")


def docker(*words, check=True):
    return subprocess.run(["docker", *words], check=check, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout.strip()


class Postgres:
    """One PostgreSQL in a container. It can be killed and started again, and then it has to recover."""

    def __init__(self):
        self.name = f"crash-{secrets.token_hex(4)}"
        self.port = free_port()
        self.password = secrets.token_hex(16)

    def start(self):
        docker("run", "--detach", "--name", self.name, "--env", f"POSTGRES_PASSWORD={self.password}", "--publish", f"127.0.0.1:{self.port}:5432", IMAGE, "-c", "max_connections=200")
        self.wait()

    def wait(self):
        wait_until("the database answers", lambda: self._answers(), 60)

    def _answers(self):
        with contextlib.suppress(psycopg.OperationalError), self.connect() as connection:
            return connection.execute("select 1").fetchone()
        return None

    def kill(self):
        docker("kill", "--signal", "KILL", self.name)

    def start_again(self):
        docker("start", self.name)
        self.wait()

    def hang(self):
        """It is still there and takes connections, but nothing moves."""
        docker("pause", self.name)

    def go_on(self):
        docker("unpause", self.name)

    def remove(self):
        docker("rm", "--force", "--volumes", self.name, check=False)

    def connect(self, database="postgres"):
        return psycopg.connect(host="127.0.0.1", port=self.port, user="postgres", password=self.password, dbname=database, autocommit=True, connect_timeout=3)

    def dotnet(self, database, user, password, port=None):
        return f"Host=127.0.0.1;Port={port or self.port};Username={user};Password={password};Database={database}"


class Line:
    """A line to the database. It is cut after the database has done what it was told, and before the answer arrives.

    That is the one moment in which nobody but the database knows what happened.
    """

    def __init__(self, postgres, what, every):
        self.target, self.what, self.every = postgres.port, what, every
        self.seen = self.cut = 0
        self._lock = threading.Lock()
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                near, _ = self._listener.accept()
            except OSError:
                return
            far = socket.create_connection(("127.0.0.1", self.target))
            swallow = threading.Event()
            threading.Thread(target=self._ask, args=(near, far, swallow), daemon=True).start()
            threading.Thread(target=self._answer, args=(near, far, swallow), daemon=True).start()

    def _ask(self, near, far, swallow):
        with contextlib.suppress(OSError):
            while data := near.recv(1 << 16):
                if self.what.search(data):
                    with self._lock:
                        self.seen += 1
                        if self.seen % self.every == 0:
                            swallow.set()
                far.sendall(data)
        self._close(near, far)

    def _answer(self, near, far, swallow):
        with contextlib.suppress(OSError):
            while data := far.recv(1 << 16):
                if swallow.is_set():
                    with self._lock:
                        self.cut += 1
                    break
                near.sendall(data)
        self._close(near, far)

    @staticmethod
    def _close(*ends):
        for end in ends:
            with contextlib.suppress(OSError):
                end.shutdown(socket.SHUT_RDWR)
            end.close()

    def end(self):
        self._close(self._listener)


class Process:
    """One running copy of the API or of the worker."""

    def __init__(self, name, command, environment, log):
        self.name = name
        self.log = log
        self._output = log.open("ab")
        self._process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL, stdout=self._output, stderr=subprocess.STDOUT)

    @property
    def pid(self):
        return self._process.pid

    @property
    def alive(self):
        return self._process.poll() is None

    def send(self, number):
        with contextlib.suppress(ProcessLookupError):
            self._process.send_signal(number)

    def kill(self):
        self.send(signal.SIGKILL)

    def go_on(self):
        self.send(signal.SIGCONT)

    def ask_to_stop(self):
        self.send(signal.SIGTERM)

    def wait(self, seconds=30.0):
        """Waits for the end and returns the exit code. A killed process has the code -9."""
        try:
            code = self._process.wait(seconds)
        except subprocess.TimeoutExpired:
            raise AssertionError(f"after {seconds:.0f} seconds still not: {self.name} has ended") from None
        self._output.close()
        return code

    def end(self):
        if self.alive:
            # A stopped process has to go on before it can be waited for.
            self.kill()
            self.go_on()
        with contextlib.suppress(AssertionError):
            self.wait(10)


class World:
    """A fresh database with the tables, and as many processes as a test wants."""

    def __init__(self, postgres, folder, feeds, *, build, net=False, **settings):
        self.postgres = postgres
        self.folder = folder
        self.feeds = feeds
        self.build = build
        self.settings = {"PLATFORM_LEASE_MS": "1500", "PLATFORM_PAUSE_MS": "40", "PLATFORM_FIRST_DELAY_MS": "100", "PLATFORM_TROUBLE_DELAY_MS": "200"} | settings
        self.processes = []
        self.ports = []
        self.faults = folder / "faults"
        self.faults.mkdir()
        self.database = f"crash_{secrets.token_hex(6)}"
        # What runs is not the owner of the database server, only of its own database.
        self.user, self.password = self.database, secrets.token_hex(16)
        with postgres.connect() as admin:
            admin.execute(f"create role {self.user} login password '{self.password}'")
            admin.execute(f"create database {self.database} owner {self.user}")
        self.feeds_file = folder / "feeds.json"
        self.feeds_file.touch(mode=0o600)
        self.feeds_file.write_text(json.dumps({"feeds": [{"name": feed.name, "shop": feed.shop, "secrets": [feed.written()]} for feed in feeds]}), encoding="utf-8")
        migrate = self._start("migrate", "worker", ["migrate"], [])
        assert migrate.wait() == 0, migrate.log.read_text(encoding="utf-8")
        self.net = net
        if not net:
            # Without the safety net only the inbox decides. See docs/contract.md.
            self.ask("alter table orders drop constraint orders_once")

    # What runs

    def _start(self, name, kind, words, faults, port=None, line=None):
        dll = self.build / kind / f"Platform.{kind.capitalize()}.dll"
        environment = {key: value for key, value in os.environ.items() if not key.startswith("PLATFORM_") or key == "PLATFORM_SQL"}
        environment |= self.settings
        environment |= {
            "PLATFORM_DATABASE": self.postgres.dotnet(self.database, self.user, self.password, line and line.port),
            "PLATFORM_FEEDS": str(self.feeds_file),
            "PLATFORM_FAULT": ",".join(faults),
            "PLATFORM_FAULTS": str(self.faults),
            "DOTNET_gcServer": "0",
        }
        if port:
            environment["ASPNETCORE_URLS"] = f"http://127.0.0.1:{port}"
        process = Process(name, ["dotnet", str(dll), *words], environment, self.folder / f"{name}.log")
        self.processes.append(process)
        return process

    def worker(self, name="worker", faults=(), line=None):
        return self._start(name, "worker", [], faults, line=line)

    def api(self, name="api", faults=(), port=None, ready=True, line=None):
        port = port or free_port()
        if port not in self.ports:
            self.ports.append(port)
        process = self._start(name, "api", [], faults, port, line)
        if ready:
            wait_until(f"{name} is ready", lambda: self.is_ready(port) or not process.alive)
            assert process.alive, process.log.read_text(encoding="utf-8")
        return process

    @staticmethod
    def is_ready(port):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/ready", timeout=2) as answer:
                return answer.status == 200
        except (urllib.error.URLError, OSError):
            return False

    # What is seen from outside

    def ask(self, sql, *values):
        """Asks the database itself. If it is just starting again, it is asked again."""
        end = time.monotonic() + 30
        while True:
            try:
                with self.postgres.connect(self.database) as connection:
                    cursor = connection.execute(sql, values or None)
                    return cursor.fetchall() if cursor.description else []
            except psycopg.OperationalError:
                if time.monotonic() > end:
                    raise
                time.sleep(0.1)

    def one(self, sql, *values):
        return self.ask(sql, *values)[0][0]

    def reached(self):
        """Every fault that really happened, as (pid, point, action)."""
        found = []
        for path in sorted(self.faults.iterdir(), key=lambda path: path.stat().st_mtime_ns):
            # The last line can be half written at this moment.
            found += [(int(path.name), *line.split(" ")) for line in path.read_text(encoding="utf-8").split("\n")[:-1]]
        return found

    def wait_for_stop(self, point):
        pid = wait_until(f"{point} is reached", lambda: [pid for pid, *rest in self.reached() if rest == [point, "stop"]])[0]
        wait_until("it is really stopped", lambda: is_stopped(pid))

    def unsettled(self):
        return self.ask("select feed, event_id, state, attempts, reason from inbox where state in ('waiting', 'working') limit 10")

    def settle(self, seconds=60.0):
        """Waits until no event waits any more and none is worked on."""
        try:
            wait_until("everything is settled", lambda: not self.unsettled(), seconds, 0.2)
        except AssertionError as problem:
            raise AssertionError(f"{problem}: {self.unsettled()}") from None

    def cut_connections(self, most):
        return len(self.ask(
            "select pg_terminate_backend(pid) from (select pid from pg_stat_activity where datname = %s and application_name in ('api', 'worker') order by random() limit %s) chosen",
            self.database, most))

    def logs(self):
        return {path.name: path.read_bytes() for path in self.folder.glob("*.log")}

    def end(self):
        for process in self.processes:
            process.end()
        with contextlib.suppress(psycopg.Error), self.postgres.connect() as admin:
            admin.execute(f"drop database if exists {self.database} with (force)")
            admin.execute(f"drop role if exists {self.user}")


class Slot(threading.Thread):
    """Keeps one copy running: whenever it dies, the next one starts, with the faults that are asked for then."""

    def __init__(self, world, kind, name, faults, pause=0.0):
        super().__init__(daemon=True, name=name)
        self.world, self.kind, self.slot, self.faults, self.pause = world, kind, name, faults, pause
        self.port = free_port() if kind == "api" else None
        if self.port:
            world.ports.append(self.port)
        self.process = None
        self.ends = []
        self._over = threading.Event()

    def run(self):
        number = 0
        while not self._over.is_set():
            number += 1
            name = f"{self.slot}-{number}"
            self.process = self.world.api(name, self.faults(), self.port, ready=False) if self.kind == "api" else self.world.worker(name, self.faults())
            while self.process.alive and not self._over.wait(0.02):
                pass
            if not self.process.alive:
                self.ends.append(self.process.wait())
                self._over.wait(self.pause)

    def kill(self):
        process = self.process
        if process and process.alive:
            process.kill()
            process.go_on()
            return True
        return False

    def finish(self):
        self._over.set()
        self.join(10)
        if self.process:
            self.process.end()


class Waker(threading.Thread):
    """Lets every process that a fault has stopped go on again, after a while.

    One that has only claimed an event goes on after the longest claim has ended. One in the middle of its transaction goes
    on sometimes before and sometimes after the database has lost its patience.
    """

    def __init__(self, world, seed):
        super().__init__(daemon=True)
        self.world, self.random = world, random.Random(seed)
        self.times = collections.Counter()
        self.due = {}
        # Those that have gone on again, and those that were not stopped after all. Of the latter there must not be any.
        self.woken, self.awake = set(), []
        self._over = threading.Event()

    def stopped(self):
        """The processes that are stopped at this moment."""
        return {pid for pid, _, action in self.world.reached() if action == "stop"} - self.woken

    def run(self):
        seen = set()
        while not self._over.wait(0.1):
            for pid, point, action in set(self.world.reached()) - seen:
                seen.add((pid, point, action))
                if action == "stop":
                    self.times[point] += 1
                    after_the_database = self.times[point] % 2 == 1
                    seconds = (4, 5.5) if point == "worker.after_claim" else (7, 9) if after_the_database else (2, 4)
                    self.due[pid] = time.monotonic() + self.random.uniform(*seconds)
            for pid in [pid for pid, when in self.due.items() if when <= time.monotonic()]:
                if not is_stopped(pid):
                    self.awake.append(pid)
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGCONT)
                del self.due[pid]
                self.woken.add(pid)

    def finish(self):
        self._over.set()
        self.join(10)
