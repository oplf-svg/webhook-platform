-- wrong: takes over the second body
-- noticed by: quick storm
with landed as (
  insert into inbox as i (feed, event_id, shop, body)
  values (@feed, @event_id, @shop, @body)
  on conflict (feed, event_id) do update
     set body = excluded.body, deliveries = i.deliveries + 1
  returning old.event_id is null as is_new, old.body is null or old.body = @body as is_same
),
beside as (
  insert into conflicts (feed, event_id, body)
  select @feed, @event_id, @body from landed where not is_same
  on conflict do nothing
)
select case when is_new then 'stored' when is_same then 'already' else 'conflict' end from landed
