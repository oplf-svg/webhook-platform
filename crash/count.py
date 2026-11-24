"""Counts from outside: what the database holds at the end, against what was sent. It uses none of the platform's code."""

import collections
import re

from feed import MARK


def problems(world, sent):
    """Every difference, in words. Nothing means that each event had its effect exactly once."""
    return sent.problems + inbox(world, sent) + effects(world, sent)


def inbox(world, sent):
    """What is kept of the events, and how they ended."""
    found = []
    events = sent.events
    rows = {(feed, id): row for feed, id, *row in world.ask("select feed, event_id, shop, body, state, attempts, deliveries, reason from inbox")}

    found += [f"lost: {key} was sent and is not there" for key in events.keys() - rows.keys()]
    found += [f"made up: {key} is there and was never sent" for key in rows.keys() - events.keys()]
    for key in events.keys() & rows.keys():
        event, (shop, body, state, attempts, deliveries, reason) = events[key], rows[key]
        if bytes(body) != event.body:
            found.append(f"changed: {key} has another body")
        if shop != event.feed.shop:
            found.append(f"in the wrong place: {key} is with {shop}")
        if state != event.ends:
            found.append(f"not settled as it should be: {key} is {state}, {reason}")
        if state == "aside" and not reason:
            found.append(f"silent: {key} is aside without a reason")
        if attempts < 1:
            found.append(f"not counted: {key} has no attempt")
        # An answer can get lost after the event was stored, so there can be more deliveries than answers.
        if not sent.taken[key] <= deliveries <= sent.tries[key]:
            found.append(f"not counted: {key} was taken {sent.taken[key]} times, tried {sent.tries[key]} times and counted {deliveries} times")

    beside = collections.defaultdict(set)
    for feed, id, body in world.ask("select feed, event_id, body from conflicts"):
        beside[feed, id].add(bytes(body))
    for key in beside.keys() | sent.conflicts.keys():
        if beside.get(key) != sent.conflicts.get(key):
            found.append(f"the other bodies of {key} are not kept as they should be")

    marked = world.one(
        "select (select count(*) from inbox where position(%s::bytea in body) > 0) + (select count(*) from conflicts where position(%s::bytea in body) > 0)"
        " + (select count(*) from orders where position(%s::text in number) > 0)", MARK.encode(), MARK.encode(), MARK)
    if marked:
        found.append(f"forged: {marked} traces of a forged event are stored")
    return found


def effects(world, sent):
    """What the events have changed."""
    found = []
    events = sent.events
    made = collections.defaultdict(list)
    for feed, id, *order in world.ask("select feed, event_id, shop, number, sku, quantity, cents from orders"):
        made[feed, id].append(tuple(order))
    for key, orders in made.items():
        event = events.get(key)
        wanted = [(event.feed.shop, event.number, event.sku, event.quantity, event.cents)] if event and event.ends == "done" else []
        if len(orders) > 1:
            found.append(f"twice: {key} had its effect {len(orders)} times")
        elif orders != wanted:
            found.append(f"wrong effect: {key} made {orders}")
    found += [f"no effect: {key}" for key, event in events.items() if event.ends == "done" and key not in made]

    # The totals are added up one by one, so they show what the orders cannot: an effect that was half made.
    wanted = collections.defaultdict(lambda: [0, 0])
    for event in events.values():
        if event.ends == "done":
            wanted[event.feed.shop][0] += 1
            wanted[event.feed.shop][1] += event.cents
    totals = {shop: [orders, int(cents)] for shop, orders, cents in world.ask("select shop, orders, cents from totals")}
    if totals != dict(wanted):
        found.append(f"the totals are {totals} and should be {dict(wanted)}")
    return found


def secrets_in_logs(world, sent):
    """Logs get copied to many places, so nothing that could be used again may stand in them."""
    needles = {f"the secret of {feed.name}": feed.written()[6:].encode() for feed in world.feeds}
    needles |= {f"the secret of {feed.name}, as bytes": feed.secret for feed in world.feeds}
    needles |= {"the password of the database": world.password.encode(), "a forged event": MARK.encode(), "a body": b"placed_at", "a signature": b"v1,"}
    # The log is JSON, which writes some characters in another way.
    needles |= {f"{what}, as JSON": needle.replace(b"+", b"\\u002B") for what, needle in needles.items() if b"+" in needle}
    return [f"{what} stands in {name}" for name, text in world.logs().items() for what, needle in needles.items() if needle in text]


def not_said(world):
    """Everything that was set aside waits for a person, so a log has to say so."""
    logs = b"".join(world.logs().values())
    return [f"aside and nobody said so: {id}" for id, in world.ask("select event_id from inbox where state = 'aside'")
            if f"{id}: set aside after attempt".encode() not in logs and not re.search(rb"%s: attempt \d+ ended as aside" % re.escape(id.encode()), logs)]
