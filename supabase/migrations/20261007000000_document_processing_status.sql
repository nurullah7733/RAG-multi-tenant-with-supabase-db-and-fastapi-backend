begin;

-- Only the pipeline-maintained columns are writable over PostgREST; document
-- metadata (filename, storage_path, ...) stays read-only for authenticated users.
revoke update on public.knowledge_documents from public, anon, authenticated;
grant update (status, processing_error, updated_at) on public.knowledge_documents to authenticated;

create policy knowledge_documents_member_update on public.knowledge_documents
    for update to authenticated
    using (private.is_organization_member(organization_id))
    with check (
        private.is_organization_member(organization_id)
        and status in ('uploaded', 'processing', 'ready', 'failed')
    );

-- updated_at is maintained by the database so every status write stays auditable
-- no matter where the processing pipeline runs (request path or later worker).
create or replace function private.touch_knowledge_document_updated_at()
    returns trigger
    language plpgsql
    set search_path = ''
as $$
begin
    new.updated_at := clock_timestamp();
    return new;
end;
$$;

create trigger knowledge_documents_updated_at
    before update on public.knowledge_documents
    for each row execute function
        private.touch_knowledge_document_updated_at();

commit;
