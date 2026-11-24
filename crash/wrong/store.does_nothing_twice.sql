-- wrong: skips what is there and then looks for it, and does not see what somebody else has only just stored
-- noticed by: quick
with landed as (
  insert into inbox (feed, event_id, shop, body)
  values (@feed, @event_id, @shop, @body)
  on conflict (feed, event_id) do nothing
  returning 1
),
known as (
  select body = @body as is_same from inbox
   where feed = @feed and event_id = @event_id and not exists (select from landed)
),
counted as (
  update inbox set deliveries = deliveries + 1
   where feed = @feed and event_id = @event_id and body = @body and exists (select from known)
),
beside as (
  insert into conflicts (feed, event_id, body)
  select @feed, @event_id, @body from known where not is_same
  on conflict do nothing
)
select 'stored' from landed
union all
select case when is_same then 'already' else 'conflict' end from known
