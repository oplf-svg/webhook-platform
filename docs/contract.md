# What this has to survive

The claim is: **no event is lost or handled twice, and forged ones are refused.** It counts as proven only when tests that
could have failed did not. This page says what that means, so that the tests, the code and the SQL can each be checked
against the same list.

## Words

- A **feed** is one channel of one shop, for example the web shop of `north`. It has its own secrets.
- An **event** is one message of a feed. Its feed and its id together name it.
- An **order** is named by its shop, its feed and its number. Each channel of a shop counts its own orders, so the same
  number from another channel is another order. Its `cents` are the price of the whole order, not of one piece.
- The **effect** of an event is what it changes in the business: here an order and a running total of its shop. Adding to
  a total is not safe to repeat, and that is on purpose.

## Receiving: `POST /hooks/{feed}`

The request follows [Standard Webhooks](https://www.standardwebhooks.com/).

| Header | What it must be |
|---|---|
| `webhook-id` | 1 to 100 of `A-Z a-z 0-9 _ : -`. No dot: the signed text is `id.time.body`, and a dot in the id would let the same signed bytes be cut in two ways |
| `webhook-timestamp` | Seconds since 1970, at most 5 minutes away from our clock |
| `webhook-signature` | `v1,` and the HMAC-SHA256 of `id.time.body` as base64. There may be several, so that secrets can be changed |

The body is at most 256 KB and is stored exactly as it came. Nothing reads it before the signature is right, and the shop
comes from the feed whose secret signed it, never from the text.

| Answer | When | What was done |
|---|---|---|
| 202 | The event is new | Stored, and the database has said so |
| 200 | The same event is already here | Only counted |
| 409 | The same id came with another body | Kept beside the first one, which stays as it is |
| 401 | Wrong signature, wrong time or unknown feed. All three look the same | Nothing |
| 400, 413 | A header is missing or malformed, or the body is too large | Nothing |
| 503 | The database did not answer | Unknown, so the feed has to send it again |

A feed has to repeat an event until it gets 202, 200 or 409. Whatever is refused is said in the log, with the feed and the
reason, but at most once a minute for each: a feed with a wrong secret must not go unnoticed, and nobody may fill the log.

## The inbox

Every event is one row of `inbox`. These columns are fixed, because the tests look at them from outside and the wrong
versions in `crash/wrong` use them. Keys, checks and indexes are free, and so is any further column that has a default.

| Column | Meaning |
|---|---|
| `feed`, `event_id` | Which event it is |
| `shop` | Whose it is |
| `body` | The bytes as they came |
| `state` | `waiting`, `working`, `done`, or `aside` for a person to look at |
| `token` | Which claim holds it. Empty unless it is `working` |
| `due_at` | While it waits: not before this moment. While it is worked on: when the claim ends |
| `attempts` | How often a worker has started on it. What was trouble with the database does not count |
| `deliveries` | How often it arrived with this body |
| `reason` | Why it is set aside, or why it waits again |

The table `conflicts` has the `feed`, `event_id` and `body` of what came with a used id.

Five statements do all the work. They stand in `db/`, each in its own file.

| File | Gets | Returns | Must hold, whoever else runs at the same moment |
|---|---|---|---|
| `store.sql` | `@feed` `@event_id` `@shop` `@body` | One row: `stored`, `already` or `conflict` | Of many that store the same event at once, exactly one gets `stored`. A row that is already there keeps its body, its state and its attempts. Every other body is kept in `conflicts` |
| `set_aside.sql` | `@max_attempts` | `feed, event_id, attempts, reason` of each event that it set aside | It takes what is due and has used up `@max_attempts`, also when that number was larger before, and nothing else. Each has a reason. It does not read everything that waits |
| `claim.sql` | `@token` `@lease` `@max_attempts` `@only_waiting` | No row, or `feed, event_id, shop, body, attempts` | One event is never with two workers. A claim is counted before the work starts. A claim ends by itself after `@lease`, and then the event can be claimed again, but not with `@only_waiting`. What has used up its attempts is not claimed. The event that has waited longest comes first. It does not read everything that waits or is done |
| `finish.sql` | `@feed` `@event_id` `@token` | How many rows it changed: 1 or 0 | It changes the row only for the token that holds it. Whoever lost the claim gets 0 |
| `give_back.sql` | `@feed` `@event_id` `@token` `@reason` `@delay` `@for_good` `@counts` `@max_attempts` | No row, or the new `state` | Only for the token that holds it. It waits for `@delay`, or goes `aside` if it is `@for_good` or the attempts are used up. An attempt that does not count (`@counts` is false) is taken off again, and the event waits |

Nobody waits for a row that somebody else holds, except `store.sql` for the same event. Time in all five is the clock of the
database. The workers' clocks do not matter.

## Working

A worker claims an event. Then, in one transaction, it marks the event as done and makes the effect. The mark comes first
and has to change exactly one row. If it does not, the worker has lost the claim and the whole transaction is taken back.

- An event that cannot be understood goes `aside` at once.
- A step that fails is tried again, each time later, and the event goes `aside` after five times.
- Trouble with the database is not the fault of the event. It is tried again for as long as it takes, and does not count.
- An event whose last claim ended without a word may be what killed its worker. One loop of each worker takes those, one at
  a time, so that the others do not go down with it again and again.
- A mistake that no event can help, for example a table that is missing, ends the worker, and it says so.

There are two layers, and each is tested alone:

1. **The inbox** decides. The crash tests take the safety net away, so that nothing else can.
2. **The safety net** is a unique key on what names the order. With a wrong inbox, it still lets no order count twice.

## What can happen, and what must be true afterwards

| | What happens | What must be true |
|---|---|---|
| 1 | A forged event: wrong secret, another feed's secret, or a changed id, time or body | Refused, and no trace of it is stored |
| 2 | A forged event takes the id of a real one, and comes first | The real one is accepted |
| 3 | A recorded real event is sent again, at once or much later | Counted, or refused. No second effect |
| 4 | The same event comes several times at the same moment, also to different copies | One effect |
| 5 | The same id comes with other bodies | The first stays, the others are kept beside it |
| 6 | The API is killed after the check, or after storing and before the answer | The feed repeats it. One effect |
| 7 | The worker is killed after the claim, after the mark, after the effect, or after the commit | One effect |
| 8 | A worker stops for longer than its claim, and then goes on | One effect, not its own |
| 9 | A worker stops in the middle of its transaction | The database ends it. The others go on |
| 10 | The connection breaks at any moment, also after the database has committed and before it has said so | One effect. For the feed it is already there |
| 11 | The database is killed and starts again, or hangs for a while | Nothing that was accepted is lost |
| 12 | An event kills every worker that touches it | Set aside after five times, and said in the log. All others are done, also those that went down with it |
| 13 | An event cannot be understood | Set aside with a reason. Nothing is lost |
| 14 | Somebody holds what an event needs, for longer than five attempts would take | It waits. Then it is done |
| 15 | A worker is asked to stop | It finishes what it has and claims nothing more |
| 16 | The standby of the database does not answer | Nothing is accepted that only one of the two has |
| 17 | The tables are missing, or a right | The API is not ready, and the worker ends and says so |
| 18 | The same order number comes from two channels of a shop, or again from one of them with another id | From two channels: two orders. Again from one: one order, and the second event is set aside with a reason |

The counting is done from outside, by a program in another language that knows what it sent and reads the tables itself.
