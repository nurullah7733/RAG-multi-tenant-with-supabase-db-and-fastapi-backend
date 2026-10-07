-- Run after all migrations as postgres on a disposable Supabase database.
-- These are metadata/RLS assertions; Storage HTTP behavior is checked separately.
begin;
insert into auth.users(id) values
('41000000-0000-0000-0000-000000000001'),
('41000000-0000-0000-0000-000000000002');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"41000000-0000-0000-0000-000000000001","role":"authenticated"}', true);
select set_config('test.upload_org_a', public.create_organization('Upload A')->>'id', true);
select set_config('request.jwt.claims', '{"sub":"41000000-0000-0000-0000-000000000002","role":"authenticated"}', true);
select set_config('test.upload_org_b', public.create_organization('Upload B')->>'id', true);
select set_config('request.jwt.claims', '{"sub":"41000000-0000-0000-0000-000000000001","role":"authenticated"}', true);

insert into storage.objects(bucket_id, name) values ('knowledge-documents', current_setting('test.upload_org_a') || '/42000000-0000-0000-0000-000000000001/42000000-0000-0000-0000-000000000001.txt');
insert into public.knowledge_documents(id, organization_id, filename, storage_path, mime_type, size_bytes, created_by)
values ('42000000-0000-0000-0000-000000000001', current_setting('test.upload_org_a')::uuid, 'notes.txt', current_setting('test.upload_org_a') || '/42000000-0000-0000-0000-000000000001/42000000-0000-0000-0000-000000000001.txt', 'text/plain', 10, auth.uid());

do $$
begin
    if (select count(*) from public.knowledge_documents where id = '42000000-0000-0000-0000-000000000001') <> 1 then
        raise exception 'Owner cannot read own document';
    end if;
    begin
        insert into storage.objects(bucket_id, name) values ('knowledge-documents', current_setting('test.upload_org_a') || '/../secret.txt');
        raise exception 'Noncanonical storage path accepted';
    exception when insufficient_privilege then null;
    end;
    begin
        insert into public.knowledge_documents(organization_id, filename, storage_path, mime_type, size_bytes, created_by)
        values (current_setting('test.upload_org_a')::uuid, 'forged.txt', current_setting('test.upload_org_b') || '/other.txt', 'text/plain', 10, auth.uid());
        raise exception 'Cross-tenant metadata path accepted';
    exception when check_violation then null;
    end;
    begin
        insert into public.knowledge_documents(organization_id, filename, storage_path, mime_type, size_bytes, created_by, status)
        values (current_setting('test.upload_org_a')::uuid, 'fake.txt', 'fake.txt', 'text/plain', 10, auth.uid(), 'ready');
        raise exception 'Client-controlled processing state accepted';
    exception when insufficient_privilege then null;
    end;
    begin
        insert into public.knowledge_documents(organization_id, filename, storage_path, mime_type, size_bytes, created_by)
        values (current_setting('test.upload_org_a')::uuid, 'fake.txt', 'fake.txt', 'text/plain', 10, '41000000-0000-0000-0000-000000000002');
        raise exception 'Creator spoof accepted';
    exception when insufficient_privilege then null;
    end;
    begin
        insert into public.knowledge_documents(id, organization_id, filename, storage_path, mime_type, size_bytes, created_by)
        values ('42000000-0000-0000-0000-000000000002', current_setting('test.upload_org_a')::uuid, 'large.txt', current_setting('test.upload_org_a') || '/42000000-0000-0000-0000-000000000002/42000000-0000-0000-0000-000000000002.txt', 'text/plain', 20971521, auth.uid());
        raise exception 'Oversized metadata accepted';
    exception when check_violation then null;
    end;
end;
$$;

-- Simulate accidental permissive policies: restrictive tenant guards still win.
reset role;
create policy upload_test_broad_storage on storage.objects for all to authenticated using (true) with check (true);
create policy upload_test_broad_documents on public.knowledge_documents for all to authenticated using (true) with check (true);
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"41000000-0000-0000-0000-000000000002","role":"authenticated"}', true);
do $$
declare affected integer;
begin
    if exists (select 1 from public.knowledge_documents where organization_id = current_setting('test.upload_org_a')::uuid) then
        raise exception 'B can read A documents';
    end if;
    if exists (select 1 from storage.objects where bucket_id = 'knowledge-documents' and name like current_setting('test.upload_org_a') || '/%') then
        raise exception 'B can read A objects';
    end if;
    begin
        insert into storage.objects(bucket_id, name) values ('knowledge-documents', current_setting('test.upload_org_a') || '/42000000-0000-0000-0000-000000000002/42000000-0000-0000-0000-000000000002.txt');
        raise exception 'B can upload to A namespace';
    exception when insufficient_privilege then null;
    end;
    begin
        insert into public.knowledge_documents(id, organization_id, filename, storage_path, mime_type, size_bytes, created_by)
        values ('42000000-0000-0000-0000-000000000002', current_setting('test.upload_org_a')::uuid, 'notes.txt', current_setting('test.upload_org_a') || '/42000000-0000-0000-0000-000000000002/42000000-0000-0000-0000-000000000002.txt', 'text/plain', 10, auth.uid());
        raise exception 'B can insert A metadata';
    exception when insufficient_privilege then null;
    end;
    delete from storage.objects where bucket_id = 'knowledge-documents' and name like current_setting('test.upload_org_a') || '/%';
    get diagnostics affected = row_count;
    if affected <> 0 then raise exception 'B can delete A objects'; end if;
end;
$$;

reset role;
do $$
begin
    if not exists (select 1 from storage.buckets where id = 'knowledge-documents' and public = false and file_size_limit = 20971520 and cardinality(allowed_mime_types) = 3) then
        raise exception 'Private bucket limits missing';
    end if;
end;
$$;
set local role anon;
do $$
begin
    begin
        insert into storage.objects(bucket_id, name) values ('knowledge-documents', current_setting('test.upload_org_a') || '/42000000-0000-0000-0000-000000000003/42000000-0000-0000-0000-000000000003.txt');
        raise exception 'Anonymous upload accepted';
    exception when insufficient_privilege then null;
    end;
    begin
        perform 1 from public.knowledge_documents;
        raise exception 'Anonymous document table access accepted';
    exception when insufficient_privilege then null;
    end;
end;
$$;
rollback;
