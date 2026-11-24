"""Everything goes wrong at once, and then it is counted from outside. Every run is different; the seed is in its report."""

import collections
import contextlib
import json
import os
import queue
import random
import signal
import threading
import time

import psycopg

import count
from feed import FORGERIES, Event, order
from world import ROOT, Slot, Waker, wait_until

POISON = "worker.read.POISON:kill:1"
KILLS = {"api": ["api.after_verify", "api.after_store"], "worker": ["worker.after_claim", "worker.after_mark", "worker.after_effect", "worker.after_commit"]}
# How often a worker has to be stopped there, and go on later.
STOPS = {"api": {}, "worker": {"worker.after_claim": 3, "worker.after_mark": 2, "worker.after_effect": 2}}

WORDS = {
    ("api.after_verify", "kill"): "The API killed after the check and before it stored the event",
    ("api.after_store", "kill"): "The API killed after it stored the event and before it answered",
    ("worker.after_claim", "kill"): "A worker killed after it claimed an event",
    ("worker.after_mark", "kill"): "A worker killed after the mark and before the effect",
    ("worker.after_effect", "kill"): "A worker killed after the effect and before the commit",
    ("worker.after_commit", "kill"): "A worker killed after the commit",
    ("worker.read.POISON", "kill"): "A worker killed by an event that kills whoever touches it",
    ("worker.after_claim", "stop"): "A worker stopped after it claimed an event, for longer than its claim",
    ("worker.after_mark", "stop"): "A worker stopped after the mark, also for longer than the database waits",
    ("worker.after_effect", "stop"): "A worker stopped after the effect, also for longer than the database waits",
}

# How large it is: the fewest events, and how often each fault has to happen before it may end.
EVENTS = int(os.environ.get("STORM_EVENTS", "1500"))
TIMES = int(os.environ.get("STORM_TIMES", "4"))
# Far more attempts than usual, so that being killed again and again is not mistaken for an event that kills.
ATTEMPTS = int(os.environ.get("STORM_ATTEMPTS", "12"))
# How long it may take until every fault has happened, and until everything is settled afterwards.
LONGEST, SETTLED = float(os.environ.get("STORM_LONGEST", "240")), float(os.environ.get("STORM_SETTLED", "120"))


class Faults:
    """Says which fault the next process gets, until each has happened often enough."""

    def __init__(self, world, seed):
        self.world, self.random, self.lock = world, random.Random(seed), threading.Lock()

    def happened(self):
        return collections.Counter((point, action) for _, point, action in self.world.reached())

    def missing(self, kind, plan=("kill", "stop")):
        happened = self.happened()
        wanted = [(point, "kill", TIMES) for point in KILLS[kind]] + [(point, "stop", times) for point, times in STOPS[kind].items()]
        return [(point, action) for point, action, times in wanted if action in plan and happened[point, action] < times]

    def next(self, kind, plan=("kill", "stop")):
        with self.lock:
            always = [POISON] if kind == "worker" else []
            missing = self.missing(kind, plan)
            if not plan:
                return always
            if not missing:
                # Enough of it. From now on only a fault now and then, so that the work gets on.
                return [*always, f"{self.random.choice(KILLS[kind])}:kill:{self.random.randint(100, 400)}"]
            point, action = self.random.choice(missing)
            return [*always, f"{point}:{action}:{self.random.randint(1, 40)}"]


class Traffic(threading.Thread):
    """Makes up what the feeds and the forgers do, in an order that nobody planned."""

    def __init__(self, sender, feeds, seed, jobs):
        super().__init__(daemon=True)
        self.sender, self.feeds, self.random, self.jobs = sender, feeds, random.Random(seed), jobs
        self.number = 0
        self.poison = []
        self._over = threading.Event()

    def run(self):
        while not self._over.is_set():
            for job in self.about_one_event():
                self.jobs.put(job)
            time.sleep(self.random.uniform(0, 0.04))

    def about_one_event(self):
        chance, feed = self.random.random, self.random.choice(self.feeds)
        self.number += 1
        id, number = f"evt_{self.number}", f"N-{self.number}"
        if self.number in (40, 160):
            event = order(feed, number, id=id, sku="POISON", ends="aside")
            self.poison.append(id)
        elif chance() < 0.03:
            event = Event(feed, id, self.random.choice([b"{", b"\xff", b"[]", b'{"type":"order.eaten"}']), "aside")
        else:
            event = order(feed, number, id=id, sku=self.random.choice(["BOX-S", "BOX-M", "BAG"]), quantity=self.random.randint(1, 9), cents=self.random.randint(1, 9999))
        other = self.random.choice([one for one in self.feeds if one != feed])
        jobs = [lambda: self.sender.deliver(event)] * self.random.choice([1, 1, 2, 3, 5])
        if chance() < 0.1:
            jobs.append(lambda: self.sender.replay(event))
        if chance() < 0.05 and event.ends == "done" and event.sku != "POISON":
            jobs.append(lambda: self.sender.contradict(event))
        for _ in range(self.random.choice([0, 1, 1, 2])):
            # Half of the forged ones take the id of a real one, and may come before it.
            how, taken = self.random.choice(FORGERIES), id if chance() < 0.5 else f"made-up-{self.number}-{self.random.randrange(1000)}"
            jobs.insert(self.random.randint(0, len(jobs)), lambda how=how, taken=taken: self.sender.forge(how, feed, other, taken))
        return jobs

    def finish(self):
        self._over.set()
        self.join(10)


class Chaos(threading.Thread):
    """Kills processes at any moment, cuts connections, holds rows, and kills the database twice."""

    def __init__(self, world, slots, waker, seed, marks):
        super().__init__(daemon=True)
        self.world, self.slots, self.waker, self.random, self.marks = world, slots, waker, random.Random(seed), marks
        self.done = collections.Counter()
        # When the database is killed, in seconds.
        self.database_kills = [4.0, 11.0]
        self._over = threading.Event()

    def enough(self):
        return not self.database_kills and self.done["killed at any moment"] >= 3 and self.done["connections are cut"] >= 10

    def run(self):
        start = time.monotonic()
        while not self._over.wait(self.random.uniform(0.15, 0.5)):
            if self.database_kills and time.monotonic() - start > self.database_kills[0]:
                self.world.postgres.kill()
                self.note("database", "the database is killed")
                time.sleep(self.random.uniform(0, 0.5))
                self.world.postgres.start_again()
                self.note("database", "the database is back")
                self.database_kills.pop(0)
                continue
            match self.random.choice(["kill", "cut", "cut", "hold"]):
                case "kill":
                    slot = self.random.choice(self.slots)
                    # One that is stopped has to come back, because that is what it was stopped for.
                    if not (slot.process and slot.process.pid in self.waker.stopped()) and slot.kill():
                        self.note(slot.kind, "a process is killed at any moment", "killed at any moment")
                case "cut":
                    cut = self.world.cut_connections(self.random.randint(1, 4))
                    self.note("database", "connections are cut", times=cut)
                case "hold":
                    with contextlib.suppress(psycopg.Error), self.world.postgres.connect(self.world.database) as connection, connection.transaction():
                        connection.execute("select 1 from totals where shop = %s for update", [self.random.choice(["north", "south"])])
                        time.sleep(self.random.uniform(0.2, 0.8))
                        self.note("database", "a row is held")

    def note(self, lane, what, name=None, times=1):
        self.done[name or what] += times
        self.marks(lane, what)

    def finish(self):
        self._over.set()
        self.join(60)


def test_everything_at_once(make_world, sent, make_sender, feeds, seed):
    start = time.monotonic()
    marks = []

    def mark(lane, what):
        marks.append({"at": round(time.monotonic() - start, 2), "lane": lane, "what": what})

    world = make_world(PLATFORM_MAX_ATTEMPTS=str(ATTEMPTS))
    faults, jobs = Faults(world, seed), queue.Queue(maxsize=400)
    slots = [Slot(world, "api", f"api-{n}", lambda: faults.next("api")) for n in (1, 2)]
    # Nothing is planned for the first worker, and the second is never stopped, so that somebody takes over what others had.
    slots += [Slot(world, "worker", f"worker-{n}", lambda plan=plan: faults.next("worker", plan)) for n, plan in enumerate([(), ("kill",), ("kill", "stop"), ("kill", "stop")], 1)]
    senders = [make_sender(world, number) for number in range(12)]
    traffic, waker = Traffic(senders[0], feeds, seed, jobs), Waker(world, seed)
    chaos = Chaos(world, slots, waker, seed, mark)
    over = threading.Event()

    def work():
        while not over.is_set() or not jobs.empty():
            with contextlib.suppress(queue.Empty):
                jobs.get(timeout=0.1)()

    hands = [threading.Thread(target=work, daemon=True) for _ in senders]
    for thread in [*slots, waker, traffic, chaos, *hands]:
        thread.start()
    try:
        wait_until("every fault has happened often enough", lambda: traffic.number >= EVENTS and not faults.missing("api") and not faults.missing("worker") and chaos.enough(), LONGEST, 0.2)
        chaos.finish()
        traffic.finish()
        wait_until("every stopped process has gone on", lambda: not waker.stopped())
        time.sleep(1)
        mark("feed", "the storm is over")
        # From now on only the events that kill their worker still do so, until they are set aside.
        for slot in slots:
            slot.faults = (lambda: [POISON]) if slot.kind == "worker" else (lambda: [])
            slot.kill()
        over.set()
        for hand in hands:
            hand.join(120)
        assert not any(thread.is_alive() for thread in [traffic, chaos, *hands])
        world.settle(SETTLED)
        mark("worker", "everything is settled")

        # And later: old ones come again, and one feed was away the whole time.
        later = random.Random(seed)
        known = list(sent.events.values())
        for event, wanted in [*((event, 200) for event in later.sample(known, min(100, len(known)))), *((order(feeds[3], f"N-late-{number}", cents=number), 202) for number in range(50))]:
            status = senders[0].deliver(event)
            # Anything but 200 and 202 is a problem that is noted already.
            if status not in (wanted, None):
                sent.problem(f"later, {event.key} was answered with {status}")
        world.settle()
        mark("feed", "late ones and old ones are in")
    finally:
        for thread in (chaos, traffic, waker):
            thread.finish()
        over.set()
        for slot in slots:
            slot.finish()

    found = count.problems(world, sent) + count.secrets_in_logs(world, sent)
    happened = faults.happened()
    ends = collections.Counter((slot.kind, code) for slot in slots for code in slot.ends)
    # A process may only have ended because it was killed. Any other end would be a crash that nobody asked for.
    found += [f"a copy of the {kind} ended by itself with {code}" for kind, code in ends if code != -signal.SIGKILL]
    found += [f"a process that should have been stopped was not: {pid}" for pid in waker.awake]
    poisoned = world.ask("select event_id, attempts from inbox where state = 'aside' and event_id = any(%s)", traffic.poison)
    found += [f"the event that kills its worker, {id}, was tried {attempts} times" for id, attempts in poisoned if attempts != ATTEMPTS]
    report(world, sent, seed, happened, chaos.done, marks, found)
    assert found == []
    assert len(poisoned) == 2


def report(world, sent, seed, happened, chaos, marks, found):
    """What happened, for people: into the summary of the run, and as a file for the site's recorder."""
    refused = sum(sent.forged.values())
    effects = world.one("select count(*) from orders")
    rows = [
        ("Real events", len(sent.events)),
        ("Times they were sent", sum(sent.tries.values())),
        ("Requests that got no answer, because something had just been killed", sent.answers[None] + sent.answers[503]),
        ("Forged or malformed requests", refused),
        ("Events whose id also came with two other bodies", len(sent.conflicts)),
        *[(words, happened[fault]) for fault, words in WORDS.items()],
        ("A process killed at any other moment", chaos["killed at any moment"]),
        ("Connections to the database that were cut", chaos["connections are cut"]),
        ("The database killed", chaos["the database is killed"]),
        ("Orders made", effects),
        ("The most attempts that an order needed", world.one("select max(attempts) from inbox where state = 'done'")),
        ("Events set aside for a person, each with its reason", world.one("select count(*) from inbox where state = 'aside'")),
        ("**Lost**", sum(line.startswith(("lost", "no effect")) for line in found)),
        ("**Handled twice**", sum(line.startswith("twice") for line in found)),
        ("**Forged and accepted**", sum(line.startswith(("forged", "made up")) for line in found)),
        ("**Anything else that is not as it should be**", sum(not line.startswith(("lost", "no effect", "twice", "forged", "made up")) for line in found)),
    ]
    text = f"### Everything at once, seed {seed}\n\n| What | How often |\n|---|---|\n" + "".join(f"| {what} | {times} |\n" for what, times in rows)
    print("\n" + text)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as file:
            file.write(text + "\n")
    public = ROOT / "build" / "public"
    public.mkdir(parents=True, exist_ok=True)
    (public / "once.json").write_text(json.dumps({"seed": seed, "counted": {what.strip("*"): times for what, times in rows}, "marks": marks, "found": found}, indent=1), encoding="utf-8")
