begin;

create extension if not exists vector with schema extensions;

create table public.knowledge_chunks (
    id uuid primary key default gen_random_uuid(),
    organization_id uuid not null references public.organizations(id) on delete cascade,
    document_id uuid not null references public.knowledge_documents(id) on delete cascade,
    chunk_index integer not null check (chunk_index >= 0),
    content text not null check (char_length(btrim(content)) > 0),
    page_number integer,
    section_title text,
    -- Dimension fixed to the initially selected embedding model
    -- (text-embedding-3-small, 1536). Changing dimensions later requires a
    -- controlled re-embedding migration, not an in-place type change.
    embedding extensions.vector(1536),
    embedding_provider text,
    embedding_model text,
    created_at timestamptz not null default now()
);

-- Chunks are immutable and belong to exactly one rewriting order per document.
create unique index knowledge_chunks_document_index_uq
    on public.knowledge_chunks(document_id, chunk_index);

create index knowledge_chunks_organization_created_idx
    on public.knowledge_chunks(organization_id, created_at desc);

-- Cosine similarity is the retrieval strategy; HNSW keeps candidate scans bounded.
create index knowledge_chunks_embedding_hnsw
    on public.knowledge_chunks using hnsw (embedding extensions.vector_cosine_ops);

alter table public.knowledge_chunks enable row level security;

revoke all on public.knowledge_chunks from public, anon, authenticated;
grant select, insert, delete on public.knowledge_chunks to authenticated;

create policy knowledge_chunks_member_read on public.knowledge_chunks
    for select to authenticated
    using (private.is_organization_member(organization_id));

create policy knowledge_chunks_member_insert on public.knowledge_chunks
    for insert to authenticated
    with check (
        private.is_organization_member(organization_id)
        and exists (
            select 1 from public.knowledge_documents d
            where d.id = document_id
              and d.organization_id = knowledge_chunks.organization_id
        )
    );

create policy knowledge_chunks_member_delete on public.knowledge_chunks
    for delete to authenticated
    using (private.is_organization_member(organization_id));

-- AND with existing permissive policies: future broad grants cannot bypass isolation.
create policy knowledge_chunks_tenant_boundary on public.knowledge_chunks
    as restrictive for all to public
    using (exists (
        select 1 from public.organization_members m
        where m.organization_id = knowledge_chunks.organization_id
          and m.user_id = (select auth.uid())
    ))
    with check (exists (
        select 1 from public.organization_members m
        where m.organization_id = knowledge_chunks.organization_id
          and m.user_id = (select auth.uid())
    ));

-- Tenant-scoped semantic similarity search. The organization parameter is
-- mandatory and the caller's own RLS also applies (security invoker), so no
-- sql path can read another organization's chunks.
create function private.match_knowledge_chunks(
    p_organization_id uuid,
    p_query_embedding extensions.vector,
    p_match_count integer default 10,
    p_minimum_similarity float default 0.15
)
returns table (
    id uuid,
    document_id uuid,
    chunk_index integer,
    content text,
    page_number integer,
    similarity float
)
language plpgsql
stable
security invoker
set search_path = ''
as $$
begin
    if p_organization_id is null then
        raise exception 'organization is required for vector search'
        using errcode = '22023';
    end if;
    return query
    select
        chunk.id,
        chunk.document_id,
        chunk.chunk_index,
        chunk.content,
        chunk.page_number,
        1 - (chunk.embedding OPERATOR(extensions.<=>) p_query_embedding)
    from public.knowledge_chunks as chunk
    where chunk.organization_id = p_organization_id
      and chunk.embedding is not null
      and 1 - (chunk.embedding OPERATOR(extensions.<=>) p_query_embedding) >= p_minimum_similarity
    order by chunk.embedding OPERATOR(extensions.<=>) p_query_embedding
    limit greatest(p_match_count, 1);
end;
$$;

revoke all on function
    private.match_knowledge_chunks(uuid, extensions.vector, integer, float)
    from public, anon, authenticated;
grant execute on function
    private.match_knowledge_chunks(uuid, extensions.vector, integer, float)
    to authenticated;

commit;
