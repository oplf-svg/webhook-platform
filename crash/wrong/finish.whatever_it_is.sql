-- wrong: marks whatever that worker holds
-- noticed by: quick
update inbox
   set state = 'done', token = null, reason = null
 where state = 'working' and token = @token
