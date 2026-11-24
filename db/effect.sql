with placed as (
  insert into orders (shop, number, feed, event_id, sku, quantity, cents, placed_at)
  values (@shop, @number, @feed, @event_id, @sku, @quantity, @cents, @placed_at)
  returning shop, cents
)
insert into totals as total (shop, orders, cents)
select shop, 1, cents from placed
on conflict (shop) do update
   set orders = total.orders + 1,
       cents  = total.cents + excluded.cents
