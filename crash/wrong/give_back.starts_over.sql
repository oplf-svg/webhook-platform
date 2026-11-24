-- wrong: forgets the attempts
-- noticed by: quick
update inbox
   set state = case when @for_good or (@counts and attempts >= @max_attempts) then 'aside' else 'waiting' end,
       attempts = 0, token = null, reason = @reason, due_at = now() + @delay
 where feed = @feed and event_id = @event_id and state = 'working' and token = @token
returning state
