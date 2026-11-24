-- wrong: does not count its attempts, so an event can kill workers for ever
-- noticed by: quick steps:test_12 storm
with next as (
  select feed, event_id from inbox
   where state in ('waiting', 'working') and due_at <= now() and attempts < @max_attempts
     and (state = 'waiting' or not @only_waiting)
   order by due_at
   limit 1
   for update skip locked
)
update inbox i
   set state = 'working', token = @token, due_at = now() + @lease
  from next where (i.feed, i.event_id) = (next.feed, next.event_id)
returning i.feed, i.event_id, i.shop, i.body, i.attempts
