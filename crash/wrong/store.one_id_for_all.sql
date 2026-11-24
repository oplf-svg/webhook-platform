-- wrong: looks for the id in all feeds
-- noticed by: quick
with seen as (
  select body from inbox where event_id = @event_id limit 1
),
landed as (
  insert into inbox as i (feed, event_id, shop, body)
  select @feed, @event_id, @shop, @body where not exists (select from seen)
  on conflict (feed, event_id) do update set deliveries = i.deliveries + 1
  returning 1
)
select case when exists (select from landed) then 'stored' else 'already' end
