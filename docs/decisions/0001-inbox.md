# 1. The inbox

*Drafted by Claude, confirmed by the owner.*

- **A table and not a queue.** The inbox lies in the same database as the orders, so the mark "done" and the effect are
  one transaction: both happen, or neither. With a queue beside the database there are two systems, and between them
  something is lost or comes twice. We would need this table anyway to know a repeat. It costs speed: three commits for
  each event, and the database does the work of a queue. I take slower and provable over faster.
- **An event is named by its feed and its id.** The sender makes up the id, so two feeds may use the same one. The body
  is not part of the name: another body under a used id is kept beside the first, which stays. The first one wins,
  because it may have been handled already, and a wrong feed must not rewrite what happened.
- **An order is named by shop, feed and number.** Each channel of a shop counts its own orders. With shop and number
  alone, a buyer on the second channel would be set aside and never told. This key is the safety net: the inbox should
  never need it, and the tests take it away to show that.
- **Claiming and finishing are separate.** The claim is committed before the work begins, so the attempt is counted even
  if the event kills its worker. In one transaction, the crash would take the count back, and the same event could kill
  workers for ever. The mark is made at the start of the transaction that makes the effect.
- **Trouble with the database does not count as an attempt.** Otherwise one outage would set aside every good event. An
  event gets five attempts.
- **A token, and not only a time.** A worker that stood still for longer than its claim may come back while another one
  has the event. With a time alone it could finish work that is no longer its own. With the token it changes nothing.
- **A claim ends by itself,** because a dead worker cannot give it back. 30 seconds: longer than the 10 after which the
  database ends any transaction, so a worker that still runs does not lose its claim.
- **One loop of each worker takes what a dead worker had,** one at a time. One of those events may be what killed it. If
  all loops took them, they would go down together again, and the innocent ones would use up their attempts.
- **Not covered.** Effects outside this database. The order in which events of one shop are handled. A stolen secret of
  a feed. What only a single disk has, when there is no standby. A tool for what was set aside. Events are kept for
  ever.
- **What would make me change this.** Ten times as many events: the one total per shop would be the limit first. An
  effect outside the database: it needs an outbox with ids of its own. Shops that need their events in order.
