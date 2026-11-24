"""A feed as it would really behave: it signs, repeats until it is answered, and sends some things twice. And a forger."""

import base64
import collections
import dataclasses
import hashlib
import hmac
import http.client
import json
import random
import secrets
import threading
import time

MARK = "FORGED"


@dataclasses.dataclass(frozen=True)
class Feed:
    name: str
    shop: str
    secret: bytes = dataclasses.field(default_factory=lambda: secrets.token_bytes(32), repr=False)

    def written(self):
        return "whsec_" + base64.b64encode(self.secret).decode()


@dataclasses.dataclass(frozen=True)
class Event:
    feed: Feed
    id: str
    body: bytes
    # What the event should end as: "done", or "aside" with no effect.
    ends: str = "done"
    number: str = ""
    sku: str = "BOX-S"
    quantity: int = 1
    cents: int = 0

    @property
    def key(self):
        return self.feed.name, self.id


def order(feed, number, *, id=None, sku="BOX-S", quantity=1, cents=100, ends="done"):
    body = json.dumps({"type": "order.placed", "order": number, "sku": sku, "quantity": quantity, "cents": cents, "placed_at": "2026-11-24T10:00:00Z"}).encode()
    return Event(feed, id or f"evt_{number}", body, ends, number, sku, quantity, cents)


def signed(secret, id, body, at=None):
    at = int(time.time()) if at is None else at
    signature = base64.b64encode(hmac.new(secret, f"{id}.{at}.".encode() + body, hashlib.sha256).digest()).decode()
    return {"webhook-id": id, "webhook-timestamp": str(at), "webhook-signature": f"v1,{signature}"}


class Sent:
    """What was sent and what was answered. It knows nothing of the database."""

    def __init__(self):
        self.lock = threading.Lock()
        self.events = {}
        self.tries = collections.Counter()
        self.taken = collections.Counter()
        self.conflicts = {}
        self.forged = collections.Counter()
        self.answers = collections.Counter()
        self.problems = []

    def problem(self, text):
        with self.lock:
            self.problems.append(text)


class Courier:
    """Brings requests to one of the copies of the API. A copy can die at any moment, and then the answer is None."""

    def __init__(self, ports, seed, timeout=12):
        self.ports = ports
        self.timeout = timeout
        self._random = random.Random(seed)
        self._local = threading.local()

    def post(self, path, headers, body):
        port = self._random.choice(self.ports)
        connections = self._local.__dict__.setdefault("connections", {})
        connection = connections.pop(port, None) or http.client.HTTPConnection("127.0.0.1", port, timeout=self.timeout)
        try:
            connection.request("POST", path, body, headers)
            answer = connection.getresponse()
            text = answer.read()
        except (OSError, http.client.HTTPException):
            connection.close()
            return None, b""
        if answer.will_close:
            connection.close()
        else:
            connections[port] = connection
        return answer.status, text


class Sender:
    """Delivers events as a feed does, and notes every answer."""

    # 500 is no answer that should ever come, so it is not here.
    AGAIN = {None, 503}

    def __init__(self, courier, sent, seed, patience=90.0):
        self.courier, self.sent, self.patience = courier, sent, patience
        self._random = random.Random(seed)

    def deliver(self, event, headers=None):
        """Sends the event, signed anew each time, until it is taken."""
        with self.sent.lock:
            self.sent.events.setdefault(event.key, event)
        end = time.monotonic() + self.patience
        while True:
            with self.sent.lock:
                self.sent.tries[event.key] += 1
            status, _ = self.courier.post(f"/hooks/{event.feed.name}", headers or signed(event.feed.secret, event.id, event.body), event.body)
            with self.sent.lock:
                self.sent.answers[status] += 1
                if status in (200, 202):
                    self.sent.taken[event.key] += 1
            if status in (200, 202):
                return status
            if status not in self.AGAIN:
                return self.sent.problem(f"a real event was answered with {status}: {event.key}")
            if time.monotonic() > end:
                return self.sent.problem(f"a real event was never taken: {event.key}")
            time.sleep(self._random.uniform(0.02, 0.2))

    def replay(self, event):
        """The same bytes twice, as somebody would send them who recorded them on the way."""
        headers = signed(event.feed.secret, event.id, event.body)
        self.deliver(event, headers)
        self.deliver(event, headers)

    def contradict(self, event):
        """The same id with two other bodies, rightly signed. The first body has to stay, and the others are kept beside it."""
        self.deliver(event)
        for number in (1, 2):
            other = order(event.feed, f"{event.number or event.id}-other-{number}", id=event.id, cents=999_990 + number)
            with self.sent.lock:
                self.sent.conflicts.setdefault(event.key, set()).add(other.body)
            self._until(f"/hooks/{event.feed.name}", lambda other=other: (signed(event.feed.secret, event.id, other.body), other.body), "another body", (409,), event.key)

    def forge(self, how, feed, other, id):
        """One forged request. Whatever it is, it has to be refused."""
        path, make, wanted = forgery(how, feed, other, id)
        status = self._until(path, make, how, wanted, (feed.name, id))
        with self.sent.lock:
            self.sent.forged[how, status] += 1

    def _until(self, path, make, what, wanted, key):
        end = time.monotonic() + self.patience
        while True:
            headers, body = make()
            status, _ = self.courier.post(path, headers, body)
            if status in wanted:
                return status
            if status not in self.AGAIN:
                self.sent.problem(f"{what} was answered with {status}: {key}")
                return status
            if time.monotonic() > end:
                self.sent.problem(f"{what} was never answered: {key}")
                return status
            time.sleep(self._random.uniform(0.02, 0.2))


FORGERIES = (
    "a made-up secret", "another feed's secret", "sent to another feed", "a changed body", "a changed id", "a changed time", "half a signature", "only its version", "another kind of signature",
    "no signature", "an empty signature", "too old", "from the future", "a feed that does not exist", "a dot in the id", "too large",
)


def forgery(how, feed, other, id):
    """Returns where it goes, a way to make it, and the answers that are right."""
    path = f"/hooks/{feed.name}"
    body = order(feed, f"{MARK}-{id[:20]}", id=id).body
    refused = (401,)

    def made(secret=feed.secret, id=id, body=body, at=None, sent_body=None, drop=None, written=None, **changed):
        def make():
            headers = signed(secret, id, body, None if at is None else int(time.time()) + at) | changed
            if written:
                headers["webhook-signature"] = written(headers["webhook-signature"])
            if drop:
                del headers[drop]
            return headers, body if sent_body is None else sent_body
        return make

    match how:
        case "a made-up secret":
            return path, made(secret=secrets.token_bytes(32)), refused
        case "another feed's secret":
            return path, made(secret=other.secret), refused
        case "sent to another feed":
            return f"/hooks/{other.name}", made(), refused
        case "a changed body":
            return path, made(body=body.replace(MARK.encode(), b"HONEST"), sent_body=body), refused
        case "a changed id":
            return path, made(id=f"{id}x", **{"webhook-id": id}), refused
        case "a changed time":
            return path, made(at=-20, **{"webhook-timestamp": str(int(time.time()) + 20)}), refused
        case "half a signature":
            return path, made(written=lambda right: "v1," + base64.b64encode(base64.b64decode(right[3:])[:16]).decode()), refused
        case "only its version":
            return path, made(written=lambda right: right[:3]), refused
        case "another kind of signature":
            return path, made(written=lambda right: "v1a," + right[3:]), refused
        case "no signature":
            return path, made(drop="webhook-signature"), (400,)
        case "an empty signature":
            return path, made(**{"webhook-signature": ""}), (400,)
        case "too old":
            return path, made(at=-400), refused
        case "from the future":
            return path, made(at=400), refused
        case "a feed that does not exist":
            return "/hooks/nobody", made(), refused
        case "a dot in the id":
            return path, made(id=f"{id}.1"), (400,)
        case "too large":
            return path, made(body=body + b" " * 256 * 1024), (413,)
    raise ValueError(how)
