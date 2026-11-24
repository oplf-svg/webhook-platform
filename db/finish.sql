update inbox
   set state = 'done', token = null, reason = null
 where feed = @feed and event_id = @event_id and state = 'working' and token = @token
