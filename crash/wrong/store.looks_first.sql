-- wrong: looks whether it is there and then stores it, and two at once both see nothing
-- noticed by: quick
with seen as (
  select body from inbox where feed = @feed and event_id = @event_id
),
landed as (
  insert into inbox (feed, event_id, shop, body)
  select @feed, @event_id, @shop, @body where not exists (select from seen)
  returning 1
),
counted as (
  update inbox set deliveries = deliveries + 1
   where feed = @feed and event_id = @event_id and body = @body and exists (select from seen)
),
beside as (
  insert into conflicts (feed, event_id, body)
  select @feed, @event_id, @body from seen where body <> @body
  on conflict do nothing
)
select case when exists (select from landed) then 'stored' when (select body from seen) = @body then 'already' else 'conflict' end
