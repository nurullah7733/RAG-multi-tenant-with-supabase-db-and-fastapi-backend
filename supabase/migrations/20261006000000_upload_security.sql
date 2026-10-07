begin;

-- NOT VALID preserves legacy rows; PostgreSQL enforces these on new writes.
-- Review old rows before a later VALIDATE CONSTRAINT; never silently delete data.
alter table public.knowledge_documents
    add constraint knowledge_documents_upload_format check (
        mime_type in ('application/pdf', 'text/plain', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        and size_bytes <= 20971520
    ) not valid,
    add constraint knowledge_documents_storage_binding check (
        storage_path = organization_id::text || '/' || id::text || '/' || id::text ||
        case mime_type
            when 'application/pdf' then '.pdf'
            when 'text/plain' then '.txt'
            when 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' then '.docx'
            else '/unsupported'
        end
    ) not valid;

-- AND with existing permissive policies: future broad grants cannot bypass isolation.
create policy knowledge_documents_tenant_boundary on public.knowledge_documents
as restrictive for all to public
using (exists (
    select 1 from public.organization_members m
    where m.organization_id = knowledge_documents.organization_id and m.user_id = (select auth.uid())
))
with check (exists (
    select 1 from public.organization_members m
    where m.organization_id = knowledge_documents.organization_id and m.user_id = (select auth.uid())
));

create policy knowledge_documents_upload_state on public.knowledge_documents
as restrictive for insert to public
with check (created_by = (select auth.uid()) and status = 'uploaded' and processing_error is null);

-- Do not allow an accidentally public bucket or broader MIME/size settings.
update storage.buckets set public = false, file_size_limit = 20971520,
    allowed_mime_types = array['application/pdf', 'text/plain', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document']
where id = 'knowledge-documents';

create policy knowledge_objects_tenant_boundary on storage.objects
as restrictive for all to public
using (
    bucket_id <> 'knowledge-documents' or exists (
        select 1 from public.organization_members m
        where m.user_id = (select auth.uid()) and m.organization_id::text = split_part(name, '/', 1)
    )
)
with check (
    bucket_id <> 'knowledge-documents' or (
        exists (
            select 1 from public.organization_members m
            where m.user_id = (select auth.uid()) and m.organization_id::text = split_part(name, '/', 1)
        )
        and name ~ '^[0-9a-f-]{36}/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/[0-9a-f-]{36}\.(pdf|docx|txt)$'
        and split_part(name, '/', 3) in (
            split_part(name, '/', 2) || '.pdf',
            split_part(name, '/', 2) || '.docx',
            split_part(name, '/', 2) || '.txt'
        )
    )
);

commit;
