-- wrong: counts trouble with the database against the event
-- noticed by: quick steps:test_14
update inbox
   set state = case when @for_good or attempts >= @max_attempts then 'aside' else 'waiting' end,
       attempts = attempts, token = null, reason = @reason, due_at = now() + @delay
 where feed = @feed and event_id = @event_id and state = 'working' and token = @token
returning state
