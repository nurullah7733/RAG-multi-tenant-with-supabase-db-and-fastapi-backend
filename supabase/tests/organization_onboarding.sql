-- Execute after both migrations on a disposable database; no fixtures persist.
begin;
insert into auth.users(id) values ('30000000-0000-0000-0000-000000000001');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"30000000-0000-0000-0000-000000000001","role":"authenticated"}', true);
do $$
declare
    result jsonb;
    previous_count bigint;
begin
    result := public.create_organization('Example', 'Custom industry', 'bd', 'Asia/Dhaka', 'bn-BD', 'bdt');
    if result->>'country_code' <> 'BD' or result->>'currency_code' <> 'BDT'
       or result->>'default_language' <> 'bn-BD' or result->>'organization_type' <> 'Custom industry' then
        raise exception 'Metadata round trip failed';
    end if;
    if not exists (select 1 from public.organization_members where organization_id = (result->>'id')::uuid and user_id = auth.uid() and role = 'owner') then
        raise exception 'Owner missing';
    end if;
    result := public.create_organization('Legacy');
    if result->>'default_language' <> 'en' or result->>'country_code' is not null then
        raise exception 'Legacy defaults changed';
    end if;
    select count(*) into previous_count from public.organizations;
    begin
        perform public.create_organization('Invalid', timezone => 'Mars/Olympus');
        raise exception 'Invalid timezone accepted';
    exception when invalid_parameter_value then null;
    end;
    begin
        perform public.create_organization('Invalid', currency_code => '123');
        raise exception 'Invalid currency accepted';
    exception when check_violation then null;
    end;
    if (select count(*) from public.organizations) <> previous_count then
        raise exception 'Invalid creation left a partial organization';
    end if;
    begin
        update public.organizations set country_code = 'US';
        raise exception 'Direct metadata update accepted';
    exception when insufficient_privilege then null;
    end;
end;
$$;
set local role anon;
do $$
begin
    begin
        perform public.create_organization('Anonymous', 'Other', 'US', 'UTC', 'en', 'USD');
        raise exception 'Anonymous onboarding accepted';
    exception when insufficient_privilege then null;
    end;
end;
$$;
rollback;
