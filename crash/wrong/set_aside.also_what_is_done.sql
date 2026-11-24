-- wrong: takes what is done as well
-- noticed by: quick
with used_up as (
  select feed, event_id from inbox
   where state in ('waiting', 'working', 'done') and attempts >= @max_attempts and due_at <= now()
   for update skip locked
)
update inbox i
   set state = 'aside', token = null, reason = case i.state when 'working' then 'its worker died or stopped' else i.reason end
  from used_up where (i.feed, i.event_id) = (used_up.feed, used_up.event_id)
returning i.feed, i.event_id, i.attempts, i.reason
