begin;

-- Public PostgREST entry point. Caller JWT and RLS remain authoritative.
create function public.match_knowledge_chunks(
    p_organization_id uuid,
    p_query_embedding extensions.vector,
    p_embedding_provider text,
    p_embedding_model text,
    p_match_count integer default 10,
    p_minimum_similarity double precision default 0.15
)
returns table (
    id uuid, organization_id uuid, document_id uuid, chunk_index integer,
    content text, page_number integer, similarity double precision
)
language plpgsql stable security invoker set search_path = ''
as $$
begin
    if p_organization_id is null or not exists (
        select 1 from public.organization_members m
        where m.organization_id = p_organization_id and m.user_id = (select auth.uid())
    ) then
        raise exception 'Organization unavailable' using errcode = '42501';
    end if;
    if p_query_embedding is null or extensions.vector_dims(p_query_embedding) <> 1536
       or p_match_count is null or p_match_count not between 1 and 100
       or p_minimum_similarity is null or p_minimum_similarity not between 0 and 1
       or nullif(btrim(p_embedding_provider), '') is null
       or nullif(btrim(p_embedding_model), '') is null then
        raise exception 'Invalid retrieval parameters' using errcode = '22023';
    end if;
    return query
    select c.id, c.organization_id, c.document_id, c.chunk_index, c.content,
           c.page_number, 1 - (c.embedding OPERATOR(extensions.<=>) p_query_embedding)
    from public.knowledge_chunks c
    join public.knowledge_documents d
      on d.id = c.document_id and d.organization_id = c.organization_id
    where c.organization_id = p_organization_id
      and d.status = 'ready' and c.embedding is not null
      and c.embedding_provider = p_embedding_provider
      and c.embedding_model = p_embedding_model
      and 1 - (c.embedding OPERATOR(extensions.<=>) p_query_embedding) >= p_minimum_similarity
    order by c.embedding OPERATOR(extensions.<=>) p_query_embedding, c.id
    limit p_match_count;
end;
$$;
revoke all on function public.match_knowledge_chunks(uuid, extensions.vector, text, text, integer, double precision)
    from public, anon;
grant execute on function public.match_knowledge_chunks(uuid, extensions.vector, text, text, integer, double precision)
    to authenticated;
notify pgrst, 'reload schema';
commit;
