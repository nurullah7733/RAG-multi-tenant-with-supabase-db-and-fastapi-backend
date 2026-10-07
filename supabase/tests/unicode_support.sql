-- Run after all migrations as postgres on a disposable Supabase database.
-- Phase 2.4 assertions: Bengali/Arabic/multi-script metadata is stored and
-- retrieved unchanged. Requires a UTF-8 client: run psql with -X -f this file.
set client_encoding = 'UTF8';
begin;
insert into auth.users(id) values
('45000000-0000-0000-0000-000000000001');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"45000000-0000-0000-0000-000000000001","role":"authenticated"}', true);
select set_config('test.unicode_org', public.create_organization('Unicode Org')->>'id', true);

insert into public.knowledge_documents(id, organization_id, filename, storage_path, mime_type, size_bytes, created_by)
values (
    '46000000-0000-0000-0000-000000000001',
    current_setting('test.unicode_org')::uuid,
    'দাম তালিকা.pdf',
    current_setting('test.unicode_org') || '/46000000-0000-0000-0000-000000000001/46000000-0000-0000-0000-000000000001.pdf',
    'application/pdf', 10, auth.uid()
),
(
    '46000000-0000-0000-0000-000000000002',
    current_setting('test.unicode_org')::uuid,
    'سياسة-الاشتراك.txt',
    current_setting('test.unicode_org') || '/46000000-0000-0000-0000-000000000002/46000000-0000-0000-0000-000000000002.txt',
    'text/plain', 10, auth.uid()
),
(
    '46000000-0000-0000-0000-000000000003',
    current_setting('test.unicode_org')::uuid,
    'মেনু 🍕.txt',
    current_setting('test.unicode_org') || '/46000000-0000-0000-0000-000000000003/46000000-0000-0000-0000-000000000003.txt',
    'text/plain', 10, auth.uid()
);

do $$
declare
    read_back int;
begin
    select count(*) into read_back from public.knowledge_documents
    where id = '46000000-0000-0000-0000-000000000001' and filename = 'দাম তালিকা.pdf';
    if read_back <> 1 then raise exception 'Bengali filename corrupted'; end if;

    select count(*) into read_back from public.knowledge_documents
    where id = '46000000-0000-0000-0000-000000000002' and filename = 'سياسة-الاشتراك.txt';
    if read_back <> 1 then raise exception 'Arabic filename corrupted'; end if;

    select count(*) into read_back from public.knowledge_documents
    where id = '46000000-0000-0000-0000-000000000003' and filename = 'মেনু 🍕.txt';
    if read_back <> 1 then raise exception 'Emoji filename corrupted'; end if;

    -- Unicode-aware length accounting (char_length counts code points).
    if (select char_length(filename) from public.knowledge_documents
        where id = '46000000-0000-0000-0000-000000000001') <> char_length('দাম তালিকা.pdf') then
        raise exception 'Character length is not Unicode-aware';
    end if;

    update public.knowledge_documents set processing_error = 'সম্ভবত স্ক্যান করা PDF'
    where id = '46000000-0000-0000-0000-000000000001';
    if (select processing_error from public.knowledge_documents
        where id = '46000000-0000-0000-0000-000000000001') <> 'সম্ভবত স্ক্যান করা PDF' then
        raise exception 'Unicode processing_error corrupted';
    end if;
end;
$$;
rollback;
