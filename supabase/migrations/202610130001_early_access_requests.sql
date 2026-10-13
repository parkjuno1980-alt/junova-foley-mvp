begin;

create table public.early_access_requests (
  id uuid primary key default gen_random_uuid(),
  name text not null check (char_length(btrim(name)) between 1 and 60),
  contact text not null check (
    char_length(btrim(contact)) between 3 and 254
    and (contact ~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$'
         or contact ~ '^[+0-9() -]{8,30}$')
  ),
  message text not null default '' check (char_length(message) <= 2000),
  created_at timestamptz not null default now()
);

comment on table public.early_access_requests is 'JUNOVA Foley MVP 정식 버전 안내 신청';
alter table public.early_access_requests enable row level security;
alter table public.early_access_requests force row level security;

revoke all privileges on table public.early_access_requests from public, anon, authenticated;
grant usage on schema public to anon, authenticated;
grant insert (name, contact, message) on table public.early_access_requests to anon, authenticated;

create policy "public_can_submit_only"
on public.early_access_requests
for insert
to anon, authenticated
with check (
  char_length(btrim(name)) between 1 and 60
  and char_length(btrim(contact)) between 3 and 254
  and char_length(message) <= 2000
);

-- No SELECT / UPDATE / DELETE policies or privileges are granted.
-- id and created_at can only be populated by database defaults from public clients.
notify pgrst, 'reload schema';
commit;
