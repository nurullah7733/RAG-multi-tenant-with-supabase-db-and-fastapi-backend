begin;

create table public.knowledge_documents (
    id uuid primary key default gen_random_uuid(),
    organization_id uuid not null references public.organizations(id) on delete cascade,
    filename text not null check (char_length(btrim(filename)) between 1 and 255),
    storage_path text not null unique,
    mime_type text not null,
    size_bytes bigint not null check (size_bytes > 0),
    status text not null default 'uploaded' check (status in ('uploaded', 'processing', 'ready', 'failed')),
    processing_error text,
    created_by uuid not null references auth.users(id) on delete restrict,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index knowledge_documents_organization_created_idx
    on public.knowledge_documents(organization_id, created_at desc);

alter table public.knowledge_documents enable row level security;

revoke all on public.knowledge_documents from public, anon, authenticated;
grant select, insert, delete on public.knowledge_documents to authenticated;

create policy knowledge_documents_member_read on public.knowledge_documents
    for select to authenticated
    using (private.is_organization_member(organization_id));

create policy knowledge_documents_member_insert on public.knowledge_documents
    for insert to authenticated
    with check (
        private.is_organization_member(organization_id)
        and created_by = (select auth.uid())
    );

create policy knowledge_documents_member_delete on public.knowledge_documents
    for delete to authenticated
    using (private.is_organization_member(organization_id));

-- Private Storage bucket holding original uploaded files. The backend
-- uploads with the authenticated user's token, so access is granted
-- through policies rather than a service-role key.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'knowledge-documents',
    'knowledge-documents',
    false,
    20971520,
    array[
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'text/plain'
    ]
)
on conflict (id) do update
    set public = excluded.public,
        file_size_limit = excluded.file_size_limit,
        allowed_mime_types = excluded.allowed_mime_types;

-- Objects are stored as "<organization_id>/<document_id>/<uuid><ext>".
-- Folder-scoped policies make cross-tenant paths impossible.
create policy knowledge_objects_member_insert on storage.objects
    for insert to authenticated
    with check (
        bucket_id = 'knowledge-documents'
        and exists (
            select 1 from public.organization_members as m
            where m.user_id = (select auth.uid())
              and m.organization_id::text = (storage.foldername(name))[1]
        )
    );

create policy knowledge_objects_member_read on storage.objects
    for select to authenticated
    using (
        bucket_id = 'knowledge-documents'
        and exists (
            select 1 from public.organization_members as m
            where m.user_id = (select auth.uid())
              and m.organization_id::text = (storage.foldername(name))[1]
        )
    );

create policy knowledge_objects_member_delete on storage.objects
    for delete to authenticated
    using (
        bucket_id = 'knowledge-documents'
        and exists (
            select 1 from public.organization_members as m
            where m.user_id = (select auth.uid())
              and m.organization_id::text = (storage.foldername(name))[1]
        )
    );

commit;
