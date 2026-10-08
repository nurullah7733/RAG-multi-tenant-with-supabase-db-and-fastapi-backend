-- Run after all migrations as postgres on a disposable Supabase database.
-- Phase 2.6 assertions: knowledge_chunks isolation, constraints and the
-- tenant-scoped vector search function.
set client_encoding = 'UTF8';
begin;
insert into auth.users(id) values
('47000000-0000-0000-0000-000000000001'),
('47000000-0000-0000-0000-000000000002');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"47000000-0000-0000-0000-000000000001","role":"authenticated"}', true);
select set_config('test.chunks_org_a', public.create_organization('Chunks A')->>'id', true);
select set_config('request.jwt.claims', '{"sub":"47000000-0000-0000-0000-000000000002","role":"authenticated"}', true);
select set_config('test.chunks_org_b', public.create_organization('Chunks B')->>'id', true);
select set_config('request.jwt.claims', '{"sub":"47000000-0000-0000-0000-000000000001","role":"authenticated"}', true);

reset role;
insert into public.knowledge_documents(id, organization_id, filename, storage_path, mime_type, size_bytes, created_by)
values
(
    '48000000-0000-0000-0000-000000000001',
    current_setting('test.chunks_org_a')::uuid,
    'pricing.pdf',
    current_setting('test.chunks_org_a') || '/48000000-0000-0000-0000-000000000001/48000000-0000-0000-0000-000000000001.pdf',
    'application/pdf', 10, auth.uid()
),
(
    '48000000-0000-0000-0000-000000000002',
    current_setting('test.chunks_org_b')::uuid,
    'pricing.pdf',
    current_setting('test.chunks_org_b') || '/48000000-0000-0000-0000-000000000002/48000000-0000-0000-0000-000000000002.pdf',
    'application/pdf', 10, '47000000-0000-0000-0000-000000000002'::uuid
);

insert into public.knowledge_chunks(document_id, organization_id, chunk_index, content, page_number, embedding, embedding_provider, embedding_model)
values
(
    '48000000-0000-0000-0000-000000000001',
    current_setting('test.chunks_org_a')::uuid,
    0,
    'A: Professional plan costs $49 per month.', 2,
    ('[' || array_to_string(array_fill(0.5::float4, ARRAY[1536]), ',') || ']')::extensions.vector,
    'openai', 'text-embedding-3-small'
),
(
    '48000000-0000-0000-0000-000000000001',
    current_setting('test.chunks_org_a')::uuid,
    1,
    'A: Refunds accepted before work starts.', 3,
    ('[' || array_to_string(array_fill(0.5::float4, ARRAY[1536]), ',') || ']')::extensions.vector,
    'openai', 'text-embedding-3-small'
),
(
    '48000000-0000-0000-0000-000000000002',
    current_setting('test.chunks_org_b')::uuid,
    0,
    'B: Professional plan costs $99 per month.', 1,
    ('[' || array_to_string(array_fill(-0.5::float4, ARRAY[1536]), ',') || ']')::extensions.vector,
    'openai', 'text-embedding-3-small'
);

update public.knowledge_documents set status = 'ready';
set local role authenticated;
set local search_path = public, pg_catalog;
do $$
declare
 v extensions.vector := ('[' || array_to_string(array_fill(0.5::float4, ARRAY[1536]), ',') || ']')::extensions.vector;
 n integer;
begin
 select count(*) into n from public.match_knowledge_chunks(current_setting('test.chunks_org_a')::uuid, v, 'openai', 'text-embedding-3-small');
 if n <> 2 then raise exception 'Expected two matching chunks, got %', n; end if;
 select count(*) into n from public.match_knowledge_chunks(current_setting('test.chunks_org_a')::uuid, v, 'openai', 'different-model');
 if n <> 0 then raise exception 'Mismatched embedding model returned'; end if;
 begin
  perform 1 from public.match_knowledge_chunks(current_setting('test.chunks_org_b')::uuid, v, 'openai', 'text-embedding-3-small');
  raise exception 'Cross tenant RPC accepted';
 exception when insufficient_privilege then null;
 end;
 begin
  perform 1 from public.match_knowledge_chunks(current_setting('test.chunks_org_a')::uuid, v, 'openai', 'text-embedding-3-small', 101);
  raise exception 'Unbounded limit accepted';
 exception when invalid_parameter_value then null;
 end;
end;
$$;
reset role;
update public.knowledge_documents set status = 'failed';
set local role authenticated;
do $$
declare v extensions.vector := ('[' || array_to_string(array_fill(0.5::float4, ARRAY[1536]), ',') || ']')::extensions.vector;
begin
 if exists(select 1 from public.match_knowledge_chunks(current_setting('test.chunks_org_a')::uuid, v, 'openai', 'text-embedding-3-small')) then
  raise exception 'Failed document chunks returned';
 end if;
end;
$$;
reset role;
set local role anon;
do $$
begin
 if exists(select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public' and p.proname = 'match_knowledge_chunks' and has_function_privilege('anon', p.oid, 'execute')) then
  raise exception 'Anonymous RPC execution permitted';
 end if;
end;
$$;
rollback;
