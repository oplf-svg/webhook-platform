-- wrong: takes the event away from whoever has it now
-- noticed by: quick
update inbox
   set state = case when @for_good or (@counts and attempts >= @max_attempts) then 'aside' else 'waiting' end,
       attempts = attempts - case when @counts then 0 else 1 end, token = null, reason = @reason, due_at = now() + @delay
 where feed = @feed and event_id = @event_id and state = 'working'
returning state
