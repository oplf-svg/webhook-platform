"""One thing goes wrong at a time, mostly at a place that is known. The numbers are those of the table in docs/contract.md."""

import concurrent.futures
import re
import shutil
import threading
import time

import pytest

import count
from feed import FORGERIES, Courier, Event, Sender, order
from world import BUILD, ROOT, Line, Slot, free_port, wait_until

POISON = "worker.read.POISON:kill:1"


def check(world, sent):
    """Everything is settled, every event had its effect exactly once, and the logs say neither too much nor too little."""
    world.settle()
    assert count.problems(world, sent) == []
    assert count.secrets_in_logs(world, sent) == []
    said(world)


def said(world):
    """Whatever was set aside is said in a log, a moment later."""
    wait_until("what was set aside is said", lambda: not count.not_said(world), 5)


def some(feeds, many, start=0):
    return [order(feeds[number % len(feeds)], f"N-{number}", cents=100 + number) for number in range(start, start + many)]


def test_with_nothing_wrong(world, sent, make_sender, feeds):
    world.api()
    world.worker()
    sender = make_sender(world)
    assert [sender.deliver(event) for event in some(feeds, 40)] == [202] * 40
    check(world, sent)


@pytest.mark.parametrize("how", FORGERIES)
def test_1_forged(world, sent, make_sender, feeds, how):
    api = world.api()
    world.worker()
    sender = make_sender(world)
    for number in range(5):
        sender.forge(how, feeds[0], feeds[2], f"forged-{number}")
    sender.deliver(order(feeds[0], "N-1"))
    assert sum(sent.forged.values()) == 5
    # One line about all five, because a feed that is refused must not go unnoticed and must not fill the log.
    assert sum(b": refused " in line for line in api.log.read_bytes().splitlines()) == 1
    check(world, sent)


@pytest.mark.parametrize("how", FORGERIES)
def test_2_a_forged_one_comes_first_with_the_id_of_a_real_one(world, sent, make_sender, feeds, how):
    world.api()
    world.worker()
    sender = make_sender(world)
    sender.forge(how, feeds[0], feeds[2], "evt_N-1")
    assert sender.deliver(order(feeds[0], "N-1", id="evt_N-1")) == 202
    check(world, sent)


def test_3_sent_again_at_once_and_later(world, sent, make_sender, feeds):
    world.api()
    world.worker()
    sender = make_sender(world)
    for event in some(feeds, 10):
        sender.replay(event)
    world.settle()
    for event in some(feeds, 10):
        assert sender.deliver(event) == 200
    check(world, sent)


def test_4_the_same_event_to_several_copies_at_once(world, sent, make_sender, feeds):
    world.api("api-a")
    world.api("api-b")
    world.worker("worker-a")
    world.worker("worker-b")
    sender = make_sender(world)
    with concurrent.futures.ThreadPoolExecutor(16) as pool:
        for event in some(feeds, 40):
            answers = list(pool.map(sender.deliver, [event] * 16))
            assert sorted(answers) == [200] * 15 + [202]
    check(world, sent)


def test_5_another_body_under_a_used_id(world, sent, make_sender, feeds):
    world.api()
    world.worker()
    sender = make_sender(world)
    for event in some(feeds, 10):
        sender.contradict(event)
    world.settle()
    for event in some(feeds, 10):
        sender.contradict(event)
    assert sorted(map(len, sent.conflicts.values())) == [2] * 10
    check(world, sent)


@pytest.mark.parametrize(("point", "answer"), [("api.after_verify", 202), ("api.after_store", 200)])
def test_6_the_api_is_killed(world, sent, make_sender, feeds, point, answer):
    port = free_port()
    doomed = world.api("doomed", [f"{point}:kill:1"], port)
    world.worker()
    sender = make_sender(world)
    answers = []
    feed = threading.Thread(target=lambda: answers.append(sender.deliver(order(feeds[0], "N-1"))))
    feed.start()
    assert doomed.wait() == -9
    assert world.reached() == [(doomed.pid, point, "kill")]
    world.api("next", port=port)
    feed.join(30)
    # Killed before it was stored, it is new the second time. Killed after that, it is already there.
    assert answers == [answer]
    check(world, sent)


@pytest.mark.parametrize("point", ["worker.after_claim", "worker.after_mark", "worker.after_effect", "worker.after_commit"])
def test_7_the_worker_is_killed(world, sent, make_sender, feeds, point):
    world.api()
    doomed = world.worker("doomed", [f"{point}:kill:3"])
    sender = make_sender(world)
    for event in some(feeds, 12):
        sender.deliver(event)
    assert doomed.wait() == -9
    assert world.reached() == [(doomed.pid, point, "kill")]
    assert world.unsettled()
    world.worker("next")
    check(world, sent)


def test_8_a_worker_stops_for_longer_than_its_claim(world, sent, make_sender, feeds):
    world.api()
    slow = world.worker("slow", ["worker.after_claim:stop:1"])
    sender = make_sender(world)
    sender.deliver(order(feeds[0], "N-1", cents=700))
    world.wait_for_stop("worker.after_claim")
    world.worker("fast")
    world.settle()
    assert count.problems(world, sent) == []
    slow.go_on()
    wait_until("the slow one notices that it lost its claim", lambda: b"lost its claim" in slow.log.read_bytes())
    check(world, sent)


@pytest.mark.parametrize("point", ["worker.after_mark", "worker.after_effect"])
def test_9_a_worker_stops_in_its_transaction(world, sent, make_sender, feeds, point):
    world.api()
    slow = world.worker("slow", [f"{point}:stop:1"])
    sender = make_sender(world)
    sender.deliver(order(feeds[0], "N-first", cents=700))
    world.wait_for_stop(point)
    world.worker("fast")
    for event in some(feeds, 20):
        sender.deliver(event)
    # It still holds the rows, and it will not let go of them by itself.
    world.settle(25)
    slow.go_on()
    wait_until("the slow one notices that it was ended", lambda: b"attempt 1 failed" in slow.log.read_bytes())
    for event in some(feeds, 20, start=20):
        sender.deliver(event)
    check(world, sent)


def test_10_connections_break_at_any_moment(world, sent, make_sender, feeds):
    world.api()
    world.worker("worker-a")
    world.worker("worker-b")
    sender = make_sender(world)
    cut = 0
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        deliveries = [pool.submit(sender.deliver, event) for event in some(feeds, 1500)]
        while not all(delivery.done() for delivery in deliveries) or world.unsettled():
            cut += world.cut_connections(3)
            time.sleep(0.03)
    assert cut >= 60
    check(world, sent)


def test_10_the_answer_to_a_commit_is_lost(world, sent, make_sender, feeds):
    line = Line(world.postgres, re.compile(rb"COMMIT(?!TED)"), every=3)
    world.api()
    worker = world.worker(line=line)
    sender = make_sender(world)
    for event in some(feeds, 60):
        sender.deliver(event)
    check(world, sent)
    line.end()
    assert line.cut >= 15
    # Each time the worker did not know, gave it back, and heard that there was nothing to give back.
    assert sum(b"no longer ours" in entry for entry in worker.log.read_bytes().splitlines()) == line.cut


def test_10_the_answer_to_storing_is_lost(world, sent, make_sender, feeds):
    line = Line(world.postgres, re.compile(rb"event_id"), every=3)
    world.api(line=line)
    world.worker()
    sender = make_sender(world)
    answers = [sender.deliver(event) for event in some(feeds, 30)]
    line.end()
    # Nobody heard that it was stored. The feed sent it again, and then it was already there.
    assert line.cut >= 10
    assert answers.count(200) == sent.answers[503] == line.cut
    world.api("another")
    check(world, sent)


def test_11_the_database_is_killed(world, sent, make_sender, feeds):
    world.api()
    world.worker()
    sender = make_sender(world)
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        deliveries = []
        for round in range(3):
            deliveries += [pool.submit(sender.deliver, event) for event in some(feeds, 400, start=400 * round)]
            wait_until("it is in the middle of it", lambda: sum(sent.taken.values()) > 400 * round + 100)
            world.postgres.kill()
            world.postgres.start_again()
        concurrent.futures.wait(deliveries)
    check(world, sent)


def test_11_the_database_hangs(world, sent, make_sender, feeds):
    world.api()
    world.worker()
    sender = make_sender(world)
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        deliveries = [pool.submit(sender.deliver, event) for event in some(feeds, 600)]
        wait_until("it is in the middle of it", lambda: sum(sent.taken.values()) > 100)
        world.postgres.hang()
        time.sleep(10)
        world.postgres.go_on()
        concurrent.futures.wait(deliveries)
    # Nobody waited for an answer until it came. The feeds were told that it was not stored.
    assert sent.answers[503] > 0
    check(world, sent)


def test_11_the_database_hangs_on_an_open_connection(world, sent, feeds, seed):
    """A new connection ends by itself when nobody answers. One that was open before has to be given up by us."""
    world.api()
    # It waits longer than the platform may, so that an answer can only come from the platform.
    sender = Sender(Courier(world.ports, seed, timeout=60), sent, seed)
    before, during = some(feeds, 2)
    assert sender.deliver(before) == 202
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        world.postgres.hang()
        try:
            pool.submit(sender.deliver, during)
            wait_until("the feed was told that it was not stored", lambda: sent.answers[503], 20)
        finally:
            world.postgres.go_on()
    world.worker()
    check(world, sent)


@pytest.mark.parametrize("pause", [0, 1.2])
@pytest.mark.parametrize("innocent", [3, 60])
def test_12_an_event_kills_every_worker_that_touches_it(world, sent, make_sender, feeds, innocent, pause):
    """Also with a pause that is longer than any claim, so that all that went down together comes back together."""
    world.settings["PLATFORM_LEASE_MS"] = "500"
    world.api()
    sender = make_sender(world)
    sender.deliver(order(feeds[0], "N-poison", sku="POISON", ends="aside"))
    for event in some(feeds, innocent):
        sender.deliver(event)
    slots = [Slot(world, "worker", f"worker-{letter}", lambda: [POISON], pause) for letter in "ab"[: 1 if pause else 2]]
    for slot in slots:
        slot.start()
    try:
        world.settle()
        said(world)
    finally:
        for slot in slots:
            slot.finish()
    # Five times, seen from outside, and then nobody touched it any more.
    assert [rest for _, *rest in world.reached()] == [["worker.read.POISON", "kill"]] * 5
    assert world.one("select attempts from inbox where event_id = 'evt_N-poison'") == 5
    # Whatever else was with the worker went down with it once at the most, and is done.
    assert world.one("select count(*) from inbox where event_id <> 'evt_N-poison' and (state <> 'done' or attempts > 2)") == 0
    check(world, sent)


def order_twice():
    """A whole order, but one name in it stands twice, and it is not clear which one counts."""
    return b'{"type":"order.placed","order":"N-1","sku":"BOX-S","quantity":1,"cents":1,"cents":2,"placed_at":"2026-11-24T10:00:00Z"}'


UNREADABLE = [b"", b"{", b"[]", b"\xff\xfe", b"\x00", b'{"type":"order.eaten"}', b'{"type":"order.placed"}', order_twice(), b"[" * 5000]


def test_13_an_event_cannot_be_understood(world, sent, make_sender, feeds):
    world.api()
    world.worker()
    sender = make_sender(world)
    for number, body in enumerate(UNREADABLE):
        sender.deliver(Event(feeds[0], f"evt_bad-{number}", body, "aside"))
        sender.deliver(order(feeds[0], f"N-{number}"))
    check(world, sent)
    # At once, and not after five times.
    assert world.one("select max(attempts) from inbox where state = 'aside'") == 1


def test_14_somebody_holds_what_an_event_needs(world, sent, make_sender, feeds):
    """For longer than five attempts take. That is not the fault of the event."""
    world.api()
    world.worker()
    sender = make_sender(world)
    sender.deliver(order(feeds[0], "N-first"))
    world.settle()
    with world.postgres.connect(world.database) as connection, connection.transaction():
        connection.execute("select 1 from totals where shop = 'north' for update")
        sender.deliver(order(feeds[0], "N-waits"))
        sender.deliver(order(feeds[2], "N-elsewhere"))
        wait_until("it is tried again", lambda: world.one("select reason from inbox where event_id = 'evt_N-waits'"))
        # The other shop does not wait for it.
        wait_until("the other shop's order is done", lambda: world.one("select state from inbox where event_id = 'evt_N-elsewhere'") == "done")
        time.sleep(13)
    check(world, sent)


def test_15_a_worker_is_asked_to_stop(world, sent, make_sender, feeds):
    world.api()
    sender = make_sender(world)
    for event in some(feeds, 400):
        sender.deliver(event)
    worker = world.worker()
    wait_until("it is busy", lambda: world.one("select count(*) from inbox where state = 'done'") > 10, pause=0.005)
    worker.ask_to_stop()
    assert worker.wait() == 0
    # It left nothing half done, and it did not simply work through all of it.
    assert world.one("select count(*) from inbox where state = 'working'") == 0
    assert world.one("select count(*) from inbox where state = 'waiting'") > 0
    world.worker("next")
    check(world, sent)


def test_16_the_standby_does_not_answer(world, sent, make_sender, feeds):
    """A commit then waits for it. Whoever gives up waiting must not take that for a yes."""
    world.api()
    world.worker()
    sender = make_sender(world)
    sender.deliver(order(feeds[0], "N-before"))
    world.settle()
    answers, seen = [], []
    with world.postgres.without_its_standby():
        start = time.monotonic()
        feed = threading.Thread(target=lambda: answers.append(sender.deliver(order(feeds[0], "N-waits"))))
        feed.start()

        def look():
            if not seen or seen[-1][1] != sent.answers:
                seen.append((round(time.monotonic() - start, 1), dict(sent.answers)))
            return answers or sent.answers[503] + sent.answers[None] >= 2

        # A yes ends it at once, so that it is seen as a yes and not as a wait that took too long.
        wait_until("somebody gave up waiting, or said yes", look, 40, saw=lambda: seen)
        assert not answers, seen
    feed.join(60)
    # It was there all the time, but only now it is certain.
    assert answers == [200]
    check(world, sent)


@pytest.mark.parametrize(("what", "code"), [("drop table conflicts, inbox", b"42P01"), ("revoke all on inbox from {user}", b"42501")])
def test_17_a_table_or_a_right_is_missing(world, what, code):
    api = world.api()
    world.ask(what.format(user=world.user))
    worker = world.worker()
    # Not an outage, so nobody waits for the end of it.
    assert worker.wait(20) == 1
    assert code in worker.log.read_bytes()
    assert api.alive and not world.is_ready(world.ports[0])


def test_18_two_channels_use_the_same_number(make_world, sent, make_sender, feeds):
    """Each channel counts its own orders. With the safety net, because it is the one that could mix them up."""
    world = make_world(net=True)
    world.api()
    world.worker()
    sender = make_sender(world)
    web, app, south = feeds[:3]
    for feed, cents in ((web, 700), (app, 50), (south, 3)):
        sender.deliver(order(feed, "N-1", id=f"evt_{feed.name}", cents=cents))
    world.settle()
    # An id of its own does not make another order of it.
    sender.deliver(order(web, "N-1", id="evt_again", cents=9, ends="aside"))
    check(world, sent)


def test_a_worker_that_breaks_says_so(make_world, tmp_path, monkeypatch):
    """One of its loops meets what should never happen. The others must not go on as if all was well."""
    folder = tmp_path / "sql"
    shutil.copytree(ROOT / "db", folder)
    (folder / "claim.sql").write_text("with once as (select nextval('once') as call where @max_attempts > 0 and not @only_waiting is null) select 'a', 'b', 'c', ''::bytea, 1 from once, generate_series(1, 2) where call = 3", encoding="utf-8")
    monkeypatch.setenv("PLATFORM_SQL", str(folder))
    world = make_world()
    world.ask("create sequence once")
    world.ask(f"grant usage on sequence once to {world.user}")
    worker = world.worker()
    assert worker.wait(20) == 1
    assert b"more than one row" in worker.log.read_bytes()


def test_the_safety_net_alone(make_world, sent, make_sender, feeds, tmp_path, monkeypatch):
    """With an inbox that decides nothing at all, an order still counts once."""
    folder = tmp_path / "sql"
    shutil.copytree(ROOT / "db", folder)
    (folder / "claim.sql").write_text("select feed, event_id, shop, body, 1 from inbox where state <> 'done' or random() < 0.5 order by random() limit 1", encoding="utf-8")
    (folder / "finish.sql").write_text("update inbox set state = 'done' where feed = @feed and event_id = @event_id", encoding="utf-8")
    (folder / "give_back.sql").write_text("select 'waiting' where false", encoding="utf-8")
    (folder / "set_aside.sql").write_text("select 'a', 'b', 1, 'c' where false", encoding="utf-8")
    monkeypatch.setenv("PLATFORM_SQL", str(folder))
    world = make_world(net=True)
    world.api()
    sender = make_sender(world)
    for event in some(feeds, 60):
        sender.deliver(event)
    workers = [world.worker("worker-a"), world.worker("worker-b")]
    wait_until("each order is there", lambda: world.one("select count(*) from orders") == 60)
    caught = wait_until("the net had something to catch", lambda: sum(worker.log.read_bytes().count(b"the order is already there") for worker in workers) > 100)
    assert caught
    assert count.effects(world, sent) == []


def test_the_build_for_production_has_no_faults(make_world, sent, make_sender, feeds):
    """The same settings that kill a test build do nothing to the one that is meant to be used."""
    for kind, point in (("api", "api.after_store"), ("worker", "worker.after_claim")):
        dll = f"{kind}/Platform.{kind.capitalize()}.dll"
        assert point.encode("utf-16-le") in (BUILD / "faults" / dll).read_bytes()
        assert point.encode("utf-16-le") not in (BUILD / "release" / dll).read_bytes()
    world = make_world("release")
    api = world.api("api", ["api.after_verify:kill:1", "api.after_store:kill:1"])
    worker = world.worker("worker", [f"worker.after_{step}:kill:1" for step in ("claim", "mark", "effect", "commit")] + [POISON])
    sender = make_sender(world)
    for event in some(feeds, 5) + [order(feeds[0], "N-poison", sku="POISON")]:
        assert sender.deliver(event) == 202
    check(world, sent)
    assert world.reached() == []
    assert api.alive and worker.alive
