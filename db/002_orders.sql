-- What an event changes. The total is on purpose something that must not be repeated: adding twice is visible.

create table orders (
  shop      text        not null,
  number    text        not null,
  feed      text        not null,
  event_id  text        not null,
  sku       text        not null,
  quantity  integer     not null check (quantity between 1 and 999),
  cents     bigint      not null check (cents >= 0),
  placed_at timestamptz not null,
  made_at   timestamptz not null default clock_timestamp(),
  -- The safety net. The inbox should never need it, and the crash tests take it away to show that. Each channel of a shop
  -- counts its own orders, so two of them can use the same number.
  constraint orders_once unique (shop, feed, number)
);

create table totals (
  shop   text   primary key,
  orders bigint not null,
  cents  bigint not null
);
