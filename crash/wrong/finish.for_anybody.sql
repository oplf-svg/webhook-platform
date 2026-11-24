-- wrong: marks it as done for anybody
-- noticed by: quick steps:test_8
update inbox
   set state = 'done', token = null, reason = null
 where feed = @feed and event_id = @event_id
