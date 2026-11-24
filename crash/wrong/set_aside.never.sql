-- wrong: does nothing, and what has no attempt left stays for ever
-- noticed by: quick steps:test_12 storm
select feed, event_id, attempts, reason from inbox where false and @max_attempts > 0
