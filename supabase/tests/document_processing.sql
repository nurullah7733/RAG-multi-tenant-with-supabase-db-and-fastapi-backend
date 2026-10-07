-- Run after all migrations as postgres on a disposable Supabase database.
-- Phase 2.3 assertions: only pipeline columns are writable and only by members.
begin;
insert into auth.users(id) values
('43000000-0000-0000-0000-000000000001'),
('43000000-0000-0000-0000-000000000002');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"43000000-0000-0000-0000-000000000001","role":"authenticated"}', true);
select set_config('test.processing_org_a', public.create_organization('Process A')->>'id', true);
select set_config('request.jwt.claims', '{"sub":"43000000-0000-0000-0000-000000000002","role":"authenticated"}', true);
select set_config('test.processing_org_b', public.create_organization('Process B')->>'id', true);
select set_config('request.jwt.claims', '{"sub":"43000000-0000-0000-0000-000000000001","role":"authenticated"}', true);

insert into public.knowledge_documents(id, organization_id, filename, storage_path, mime_type, size_bytes, created_by)
values (
    '44000000-0000-0000-0000-000000000001',
    current_setting('test.processing_org_a')::uuid,
    'guide.txt',
    current_setting('test.processing_org_a') || '/44000000-0000-0000-0000-000000000001/44000000-0000-0000-0000-000000000001.txt',
    'text/plain', 10, auth.uid()
);

do $$
declare affected integer;
begin
    update public.knowledge_documents
    set status = 'processing' where id = '44000000-0000-0000-0000-000000000001';
    if (select status from public.knowledge_documents where id = '44000000-0000-0000-0000-000000000001') <> 'processing'
    or (select updated_at from public.knowledge_documents where id = '44000000-0000-0000-0000-000000000001') < (select created_at from public.knowledge_documents where id = '44000000-0000-0000-0000-000000000001') then
        raise exception 'Processing state update or updated_at maintenance failed';
    end if;

    update public.knowledge_documents
    set status = 'failed', processing_error = 'No extractable text was found.'
    where id = '44000000-0000-0000-0000-000000000001';
    if (select processing_error from public.knowledge_documents where id = '44000000-0000-0000-0000-000000000001') is null then
        raise exception 'Processing error update failed';
    end if;

    begin
        update public.knowledge_documents set filename = 'renamed.txt'
        where id = '44000000-0000-0000-0000-000000000001';
        raise exception 'Metadata column update accepted';
    exception when insufficient_privilege then null;
    end;

    begin
        update public.knowledge_documents set storage_path = 'other/path.txt'
        where id = '44000000-0000-0000-0000-000000000001';
        raise exception 'Storage path update accepted';
    exception when insufficient_privilege then null;
    end;

    -- Member of another organization cannot update anything.
    select set_config('request.jwt.claims', '{"sub":"43000000-0000-0000-0000-000000000002","role":"authenticated"}', true);
    update public.knowledge_documents
    set status = 'failed' where id = '44000000-0000-0000-0000-000000000001';
    get diagnostics affected = row_count;
    if affected <> 0 then raise exception 'Other tenant updated A document'; end if;

    select set_config('request.jwt.claims', '{"sub":"43000000-0000-0000-0000-000000000001","role":"authenticated"}', true);
end;
$$;

set local role anon;
do $$
begin
    begin
        update public.knowledge_documents set status = 'ready'
        where id = '44000000-0000-0000-0000-000000000001';
        raise exception 'Anonymous update accepted';
    exception when insufficient_privilege then null;
    end;
end;
$$;
rollback;
