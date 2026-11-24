-- wrong: marks it as done for a worker that lost it
-- noticed by: quick
update inbox
   set state = 'done', token = null, reason = null
 where feed = @feed and event_id = @event_id and state = 'working'
