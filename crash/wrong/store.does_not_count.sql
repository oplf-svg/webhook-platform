-- wrong: does not count how often an event came
-- noticed by: quick storm
with landed as (
  insert into inbox as i (feed, event_id, shop, body)
  values (@feed, @event_id, @shop, @body)
  on conflict (feed, event_id) do update
     set deliveries = i.deliveries
  returning old.event_id is null as is_new, new.body = @body as is_same
),
beside as (
  insert into conflicts (feed, event_id, body)
  select @feed, @event_id, @body from landed where not is_same
  on conflict do nothing
)
select case when is_new then 'stored' when is_same then 'already' else 'conflict' end from landed
