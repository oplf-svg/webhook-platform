-- One loop of each worker runs this all the time, so it has to be cheap: the index on attempts finds nothing, at once.
with used_up as (
  select feed, event_id from inbox
   where state in ('waiting', 'working') and attempts >= @max_attempts and due_at <= now()
   for update skip locked
)
update inbox i
   set state = 'aside', token = null,
       reason = case i.state when 'working' then 'its worker died or stopped ' || i.attempts || ' times' else i.reason end
  from used_up where (i.feed, i.event_id) = (used_up.feed, used_up.event_id)
returning i.feed, i.event_id, i.attempts, i.reason
