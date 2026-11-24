-- wrong: says that there is a conflict and does not keep the other body
-- noticed by: quick storm
with landed as (
  insert into inbox as i (feed, event_id, shop, body)
  values (@feed, @event_id, @shop, @body)
  on conflict (feed, event_id) do update
     set deliveries = i.deliveries + (i.body = excluded.body)::int
  returning old.event_id is null as is_new, new.body = @body as is_same
)
select case when is_new then 'stored' when is_same then 'already' else 'conflict' end from landed
