begin;

alter table public.organizations
    add column organization_type text check (char_length(organization_type) between 1 and 100 and organization_type = btrim(organization_type)),
    add column country_code text check (country_code ~ '^[A-Z]{2}$'),
    add column timezone text check (char_length(timezone) between 1 and 100),
    add column default_language text not null default 'en' check (char_length(default_language) <= 35 and default_language ~ '^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$'),
    add column currency_code text check (currency_code ~ '^[A-Z]{3}$');

-- Replace, rather than overload, to keep PostgREST RPC resolution unambiguous.
-- Defaults preserve Phase 1.3 name-only callers and existing organization rows.
drop function public.create_organization(text);
create function public.create_organization(
    organization_name text,
    organization_type text default null,
    country_code text default null,
    timezone text default null,
    default_language text default 'en',
    currency_code text default null
)
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
    if timezone is not null and not exists (
        select 1 from pg_catalog.pg_timezone_names as zones where zones.name = timezone
    ) then
        raise exception 'Invalid timezone' using errcode = '22023';
    end if;
    insert into public.organizations(name, organization_type, country_code, timezone, default_language, currency_code)
    values (btrim(organization_name), btrim(organization_type), upper(btrim(country_code)), timezone, default_language, upper(btrim(currency_code)))
    returning * into created_organization;
    insert into public.organization_members(organization_id, user_id, role)
    values (created_organization.id, current_user_id, 'owner');
    return to_jsonb(created_organization);
end;
$$;
revoke all on function public.create_organization(text,text,text,text,text,text) from public, anon, authenticated;
grant execute on function public.create_organization(text,text,text,text,text,text) to authenticated;

commit;

