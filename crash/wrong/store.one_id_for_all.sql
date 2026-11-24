-- wrong: looks for the id in all feeds
-- noticed by: quick
with elsewhere as (
  select from inbox where event_id = @event_id and feed <> @feed limit 1
),
landed as (
  insert into inbox as i (feed, event_id, shop, body)
  select @feed, @event_id, @shop, @body where not exists (select from elsewhere)
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
union all
select 'already' from elsewhere
