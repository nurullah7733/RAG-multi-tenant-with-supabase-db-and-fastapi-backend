begin;

create table public.organizations (
    id uuid primary key default gen_random_uuid(),
    name text not null check (char_length(btrim(name)) between 1 and 100 and name = btrim(name)),
    created_at timestamptz not null default now()
);

create table public.organization_members (
    organization_id uuid not null references public.organizations(id) on delete cascade,
    user_id uuid not null references auth.users(id) on delete restrict,
    role text not null check (role in ('owner', 'admin', 'staff')),
    created_at timestamptz not null default now(),
    primary key (organization_id, user_id)
);
create index organization_members_user_id_idx on public.organization_members(user_id, organization_id);

alter table public.organizations enable row level security;
alter table public.organization_members enable row level security;

-- Definer helper avoids recursive RLS on organization_members. Not exposed as RPC.
create schema if not exists private;
revoke all on schema private from public, anon, authenticated;
grant usage on schema private to authenticated;

create function private.is_organization_member(target_organization_id uuid)
returns boolean
language sql stable security definer
set search_path = ''
as $$
    select exists (
        select 1 from public.organization_members
        where organization_id = target_organization_id and user_id = (select auth.uid())
    );
$$;
revoke all on function private.is_organization_member(uuid) from public, anon, authenticated;
grant execute on function private.is_organization_member(uuid) to authenticated;

revoke all on public.organizations, public.organization_members from public, anon, authenticated;
grant select on public.organizations, public.organization_members to authenticated;

create policy organizations_member_read on public.organizations
for select to authenticated
using (private.is_organization_member(id));

create policy organization_members_member_read on public.organization_members
for select to authenticated
using (private.is_organization_member(organization_id));

-- Organization + initial owner must commit together. No arbitrary user ID,
-- organization ID or role parameter and no direct client writes are permitted.
create function public.create_organization(organization_name text)
returns jsonb
language plpgsql security definer
set search_path = ''
as $$
declare
    current_user_id uuid := auth.uid();
    created_organization public.organizations;
begin
    if current_user_id is null or auth.role() is distinct from 'authenticated' then
        raise exception 'Authentication required' using errcode = '42501';
    end if;
    if organization_name is null or char_length(btrim(organization_name)) not between 1 and 100 then
        raise exception 'Invalid organization name' using errcode = '22023';
    end if;
    insert into public.organizations(name) values (btrim(organization_name))
    returning * into created_organization;
    insert into public.organization_members(organization_id, user_id, role)
    values (created_organization.id, current_user_id, 'owner');
    return to_jsonb(created_organization);
end;
$$;
revoke all on function public.create_organization(text) from public, anon, authenticated;
grant execute on function public.create_organization(text) to authenticated;

commit;
