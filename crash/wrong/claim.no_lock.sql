-- wrong: chooses an event without locking it, and two workers choose the same
-- noticed by: quick
with next as (
  select feed, event_id from inbox
   where state in ('waiting', 'working') and due_at <= now() and attempts < @max_attempts
     and (state = 'waiting' or not @only_waiting)
   order by due_at
   limit 1
)
update inbox i
   set state = 'working', token = @token, attempts = i.attempts + 1, due_at = now() + @lease
  from next where (i.feed, i.event_id) = (next.feed, next.event_id)
returning i.feed, i.event_id, i.shop, i.body, i.attempts
