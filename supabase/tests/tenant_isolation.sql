-- Run AFTER migrations as postgres against a disposable Supabase database.
-- Assertions fail with SQL errors; every fixture is rolled back.
begin;

insert into auth.users(id) values
('10000000-0000-0000-0000-000000000001'),
('10000000-0000-0000-0000-000000000002'),
('10000000-0000-0000-0000-000000000003'),
('10000000-0000-0000-0000-000000000004');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"10000000-0000-0000-0000-000000000001","role":"authenticated"}', true);
select set_config('test.org_a', public.create_organization('Organization A')->>'id', true);
select set_config('test.org_a2', public.create_organization('Organization A second workspace')->>'id', true);

do $$
begin
    if (select count(*) from public.organizations) <> 2 then
        raise exception 'A must own two organizations';
    end if;
    if (select count(*) from public.organization_members where user_id = auth.uid() and role = 'owner') <> 2 then
        raise exception 'Creation must create exactly one owner membership per organization';
    end if;
    begin
        perform public.create_organization(' ');
        raise exception 'Blank names must fail';
    exception when invalid_parameter_value then null;
    end;
    begin
        perform public.create_organization(repeat('x', 101));
        raise exception 'Long names must fail';
    exception when invalid_parameter_value then null;
    end;
    begin
        insert into public.organizations(name) values ('Unauthorized direct insert');
        raise exception 'Direct organization inserts must fail';
    exception when insufficient_privilege then null;
    end;
    begin
        insert into public.organization_members(organization_id, user_id, role)
        values (current_setting('test.org_a')::uuid, '10000000-0000-0000-0000-000000000002', 'owner');
        raise exception 'Arbitrary membership grants must fail';
    exception when insufficient_privilege then null;
    end;
end;
$$;

select set_config('request.jwt.claims', '{"sub":"10000000-0000-0000-0000-000000000002","role":"authenticated"}', true);
select set_config('test.org_b', public.create_organization('Organization B')->>'id', true);
do $$
begin
    if (select count(*) from public.organizations) <> 1 then
        raise exception 'B must see only its own organization';
    end if;
    if exists (select 1 from public.organizations where id = current_setting('test.org_a')::uuid) then
        raise exception 'B must not read A by modifying organization ID';
    end if;
    if exists (select 1 from public.organization_members where organization_id = current_setting('test.org_a')::uuid) then
        raise exception 'B must not read A memberships';
    end if;
    if private.is_organization_member(current_setting('test.org_a')::uuid) then
        raise exception 'Helper must derive the caller, not trust a supplied identity';
    end if;
    begin
        update public.organizations set name = 'hijacked' where id = current_setting('test.org_a')::uuid;
        raise exception 'Organization updates must fail';
    exception when insufficient_privilege then null;
    end;
    begin
        delete from public.organizations where id = current_setting('test.org_a')::uuid;
        raise exception 'Organization deletion must fail';
    exception when insufficient_privilege then null;
    end;
end;
$$;

-- Privileged fixture setup only; no team-management API is introduced.
reset role;
insert into public.organization_members(organization_id, user_id, role) values
(current_setting('test.org_a')::uuid, '10000000-0000-0000-0000-000000000003', 'admin'),
(current_setting('test.org_a')::uuid, '10000000-0000-0000-0000-000000000004', 'staff');
do $$
begin
    begin
        insert into public.organization_members(organization_id, user_id, role)
        values (current_setting('test.org_a')::uuid, '10000000-0000-0000-0000-000000000002', 'platform_admin');
        raise exception 'Unknown roles must fail';
    exception when check_violation then null;
    end;
    begin
        insert into public.organization_members(organization_id, user_id, role)
        values (current_setting('test.org_a')::uuid, '10000000-0000-0000-0000-000000000001', 'staff');
        raise exception 'Duplicate membership must fail';
    exception when unique_violation then null;
    end;
    begin
        delete from auth.users where id = '10000000-0000-0000-0000-000000000001';
        raise exception 'Identity deletion must not silently orphan an organization';
    exception when foreign_key_violation or restrict_violation then null;
    end;
end;
$$;

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"10000000-0000-0000-0000-000000000004","role":"authenticated"}', true);
do $$
begin
    if (select count(*) from public.organizations) <> 1 then
        raise exception 'Staff must see only its organization';
    end if;
    if (select count(*) from public.organization_members) <> 3 then
        raise exception 'Members must read their peers without recursive RLS';
    end if;
    begin
        update public.organization_members set role = 'owner' where user_id = auth.uid();
        raise exception 'Self promotion must fail';
    exception when insufficient_privilege then null;
    end;
    begin
        update public.organization_members set organization_id = current_setting('test.org_b')::uuid
        where user_id = auth.uid();
        raise exception 'Moving membership across tenants must fail';
    exception when insufficient_privilege then null;
    end;
    begin
        delete from public.organization_members where user_id = auth.uid();
        raise exception 'Direct membership deletion must fail';
    exception when insufficient_privilege then null;
    end;
end;
$$;

select set_config('request.jwt.claims', '{"sub":"10000000-0000-0000-0000-000000000003","role":"authenticated"}', true);
do $$
begin
    if (select count(*) from public.organizations) <> 1
       or (select count(*) from public.organization_members) <> 3 then
        raise exception 'Admin must see only its organization and peers';
    end if;
end;
$$;

reset role;
delete from public.organization_members where user_id = '10000000-0000-0000-0000-000000000004';
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"10000000-0000-0000-0000-000000000004","role":"authenticated"}', true);
do $$
begin
    if exists (select 1 from public.organizations) or exists (select 1 from public.organization_members) then
        raise exception 'Revoked membership must immediately lose visibility';
    end if;
end;
$$;

-- Owner membership insert failure must roll back organization creation.
select set_config('request.jwt.claims', '{"sub":"10000000-0000-0000-0000-000000000099","role":"authenticated"}', true);
do $$
begin
    begin
        perform public.create_organization('Should roll back');
        raise exception 'Missing Auth identity must fail';
    exception when foreign_key_violation then null;
    end;
end;
$$;
reset role;
do $$
begin
    if exists (select 1 from public.organizations where name = 'Should roll back') then
        raise exception 'Atomic creation left an orphan organization';
    end if;
end;
$$;

set local role authenticated;
select set_config('request.jwt.claims', '{}', true);
do $$
begin
    if exists (select 1 from public.organizations) then
        raise exception 'Missing UID must have no visibility';
    end if;
    begin
        perform public.create_organization('No authenticated identity');
        raise exception 'RPC must reject missing UID';
    exception when insufficient_privilege then null;
    end;
end;
$$;

set local role anon;
do $$
begin
    begin
        perform * from public.organizations;
        raise exception 'Anonymous reads must fail';
    exception when insufficient_privilege then null;
    end;
    begin
        perform * from public.organization_members;
        raise exception 'Anonymous membership reads must fail';
    exception when insufficient_privilege then null;
    end;
    begin
        perform public.create_organization('Anonymous creation');
        raise exception 'Anonymous RPC execution must fail';
    exception when insufficient_privilege then null;
    end;
    begin
        perform private.is_organization_member(current_setting('test.org_a')::uuid);
        raise exception 'Anonymous helper execution must fail';
    exception when insufficient_privilege then null;
    end;
end;
$$;

reset role;
rollback;
