-- wrong: lets every loop take what may have killed its worker, and good events go down with it
-- noticed by: quick steps:test_12
with next as (
  select feed, event_id from inbox
   where state in ('waiting', 'working') and due_at <= now() and attempts < @max_attempts and @only_waiting is not null
   order by due_at
   limit 1
   for update skip locked
)
update inbox i
   set state = 'working', token = @token, attempts = i.attempts + 1, due_at = now() + @lease
  from next where (i.feed, i.event_id) = (next.feed, next.event_id)
returning i.feed, i.event_id, i.shop, i.body, i.attempts
