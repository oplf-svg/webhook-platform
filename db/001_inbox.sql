create table inbox (
  feed        text        not null,
  event_id    text        not null,
  shop        text        not null,
  body        bytea       not null,
  received_at timestamptz not null default now(),
  deliveries  integer     not null default 1 check (deliveries >= 1),
  state       text        not null default 'waiting' check (state in ('waiting', 'working', 'done', 'aside')),
  attempts    integer     not null default 0 check (attempts >= 0),
  -- For a waiting event: not before this. For one that is worked on: when the claim ends.
  due_at      timestamptz not null default now(),
  token       uuid,
  reason      text,
  primary key (feed, event_id),
  constraint a_token_exactly_while_working check ((state = 'working') = (token is not null)),
  constraint aside_has_a_reason check (state <> 'aside' or reason is not null)
);

-- The only rows that a worker ever looks for, by when they are due and by how often they were tried.
create index inbox_due on inbox (due_at) where state in ('waiting', 'working');
create index inbox_attempts on inbox (attempts) where state in ('waiting', 'working');

create table conflicts (
  feed        text        not null,
  event_id    text        not null,
  body        bytea       not null,
  body_hash   bytea       not null generated always as (sha256(body)) stored,
  received_at timestamptz not null default now(),
  deliveries  integer     not null default 1 check (deliveries >= 1),
  primary key (feed, event_id, body_hash),
  foreign key (feed, event_id) references inbox
);
