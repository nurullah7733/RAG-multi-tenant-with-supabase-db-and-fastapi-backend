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

set local role authenticated;
set local search_path = public, pg_catalog;
do $$
declare
    affected integer;
    result_count integer;
    query_vector extensions.vector;
begin
    query_vector := ('[' || array_to_string(array_fill(0.5::float4, ARRAY[1536]), ',') || ']')::extensions.vector;

    -- Owner A searches its organization: only A's chunks, similarity ordered.
    select count(*) into result_count
    from private.match_knowledge_chunks(current_setting('test.chunks_org_a')::uuid, query_vector, 10, 0.15);
    if result_count <> 2 then raise exception 'Owner A expected both chunks, got %', result_count; end if;
    if exists (
        select 1 from private.match_knowledge_chunks(current_setting('test.chunks_org_a')::uuid, query_vector, 10, 0.15)
        where content not like 'A:%'
    ) then raise exception 'A search returned non-A content'; end if;

    -- match_count is honored.
    select count(*) into result_count
    from private.match_knowledge_chunks(current_setting('test.chunks_org_a')::uuid, query_vector, 1, 0.15);
    if result_count <> 1 then raise exception 'match_count not honored'; end if;

    -- Null organization is rejected outright.
    begin
        perform 1 from private.match_knowledge_chunks(null, query_vector, 10, 0.15);
        raise exception 'Null organization search accepted';
    exception when invalid_parameter_value then null;
    end;

    -- Member B cannot reach A's knowledge even by naming A's organization:
    -- the invoker's RLS filters everything.
    perform set_config('request.jwt.claims', '{"sub":"47000000-0000-0000-0000-000000000002","role":"authenticated"}', true);
    select count(*) into result_count
    from private.match_knowledge_chunks(current_setting('test.chunks_org_a')::uuid, query_vector, 10, -1.0);
    if result_count <> 0 then raise exception 'B retrieved A chunks through search'; end if;

    -- Direct reads of A's rows are invisible to B.
    select count(*) into affected from public.knowledge_chunks
    where organization_id = current_setting('test.chunks_org_a')::uuid;
    if affected <> 0 then raise exception 'B can read A chunks'; end if;

    -- B cannot insert chunks under A's document.
    begin
        insert into public.knowledge_chunks(document_id, organization_id, chunk_index, content, embedding)
        values (
            '48000000-0000-0000-0000-000000000001',
            current_setting('test.chunks_org_a')::uuid,
            9, 'B writes into A',
            ('[' || array_to_string(array_fill(0.5::float4, ARRAY[1536]), ',') || ']')::extensions.vector
        );
        raise exception 'B inserted chunk into A document';
    exception when insufficient_privilege then null;
    end;

    -- Organization/document mismatch is rejected even for B's own doc reference.
    begin
        insert into public.knowledge_chunks(document_id, organization_id, chunk_index, content, embedding)
        values (
            '48000000-0000-0000-0000-000000000001',
            current_setting('test.chunks_org_b')::uuid,
            0, 'Cross document binding',
            ('[' || array_to_string(array_fill(0.5::float4, ARRAY[1536]), ',') || ']')::extensions.vector
        );
        raise exception 'Chunk inserted with mismatched document binding';
    exception when insufficient_privilege then null;
    end;

    -- B cannot delete A chunks.
    delete from public.knowledge_chunks
    where organization_id = current_setting('test.chunks_org_a')::uuid;
    get diagnostics affected = row_count;
    if affected <> 0 then raise exception 'B deleted A chunks'; end if;

    perform set_config('request.jwt.claims', '{"sub":"47000000-0000-0000-0000-000000000001","role":"authenticated"}', true);

    -- Duplicate chunk positions per document are impossible (reprocess safety).
    begin
        insert into public.knowledge_chunks(document_id, organization_id, chunk_index, content, embedding)
        values (
            '48000000-0000-0000-0000-000000000001',
            current_setting('test.chunks_org_a')::uuid,
            0, 'duplicate position',
            ('[' || array_to_string(array_fill(0.5::float4, ARRAY[1536]), ',') || ']')::extensions.vector
        );
        raise exception 'Duplicate document chunk_index accepted';
    exception when unique_violation then null;
    end;

    -- B writes its own document; never seed another tenant as A.
    perform set_config('request.jwt.claims', '{"sub":"47000000-0000-0000-0000-000000000002","role":"authenticated"}', true);
    -- The unique index is per document, not global: B can still write chunk 1.
    insert into public.knowledge_chunks(document_id, organization_id, chunk_index, content, embedding)
    values (
        '48000000-0000-0000-0000-000000000002',
        current_setting('test.chunks_org_b')::uuid,
        1, 'B second chunk',
        ('[' || array_to_string(array_fill(-0.5::float4, ARRAY[1536]), ',') || ']')::extensions.vector
    );

    perform set_config('request.jwt.claims', '{"sub":"47000000-0000-0000-0000-000000000001","role":"authenticated"}', true);
    -- A still sees only its own two chunks after all of the above.
    select count(*) into result_count
    from private.match_knowledge_chunks(current_setting('test.chunks_org_a')::uuid, query_vector, 10, 0.15);
    if result_count <> 2 then raise exception 'A chunk visibility changed'; end if;
end;
$$;

reset role;
do $$
begin
    if not exists (
        select 1 from pg_extension where extname = 'vector'
    ) then
        raise exception 'pgvector extension missing';
    end if;
end;
$$;

set local role anon;
do $$
begin
    begin
        perform 1 from public.knowledge_chunks;
        raise exception 'Anonymous chunk access accepted';
    exception when insufficient_privilege then null;
    end;
end;
$$;
rollback;
