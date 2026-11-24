-- "On conflict do update" waits for whoever stores the same event at this moment and then sees their row.
-- "Do nothing" would not return it, and a select beside it would not see it.
with landed as (
  insert into inbox as i (feed, event_id, shop, body)
  values (@feed, @event_id, @shop, @body)
  on conflict (feed, event_id) do update
     set deliveries = i.deliveries + (i.body = excluded.body)::int
  returning old.event_id is null as is_new, new.body = @body as is_same
),
beside as (
  insert into conflicts as c (feed, event_id, body)
  select @feed, @event_id, @body from landed where not is_same
  on conflict (feed, event_id, body_hash) do update set deliveries = c.deliveries + 1
)
select case when is_new then 'stored' when is_same then 'already' else 'conflict' end from landed
