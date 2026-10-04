# Backend: Phase 1.3 organization and membership foundation

FastAPI owns business APIs; Supabase Auth owns identities. This phase adds only
organizations, memberships (`owner`, `admin`, `staff`), authenticated API access,
and tenant isolation. No frontend onboarding or team-management UI is included.

## Configuration and startup

Install existing dependencies with Python and the project's virtual environment:

```powershell
cd backend
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If you do not already have `.env`, copy `.env.example`; otherwise merge new names
into your current `.env`. Do not overwrite existing values or commit credentials.

| Variable | Purpose |
| --- | --- |
| `SUPABASE_URL` | Existing setting, now documented/validated; same project origin as frontend |
| `SUPABASE_PUBLISHABLE_KEY` | New setting: project's `sb_publishable_...` key (legacy anon supported) |
| `DEBUG` | Existing boolean setting (`true` or `false`, not `release`) |
| `CORS_ORIGINS` | Existing comma-separated frontend origin allowlist |

Copy the public key value used by frontend `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`
to backend `SUPABASE_PUBLISHABLE_KEY`. No service-role key is required or accepted.
Auth/business endpoints return 503 if the public key is not configured. `/health`
continues to work without that key. `SUPABASE_URL` remains required as before.

```powershell
# Override a conflicting shell DEBUG value if necessary.
$env:DEBUG = 'false'
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` for the typed API contract and Bearer authorization.

## Database migration workflow

All infrastructure lives under `backend/supabase/`. Run the Supabase CLI from
`backend/`; do not initialize a root-level project. The config contains only local
development settings and does not change hosted Auth/provider settings.

### Local Supabase

Install Supabase CLI and a supported local container runtime first. Then:

```text
supabase start
supabase migration up --local
supabase migration list --local
```

The first `start` applies migrations to a new local stack. `migration up` applies
pending migrations on an existing stack. Configure frontend and backend against
the same local project and use its public key. Keep `private` out of Data API
exposed schemas (local config exposes only `public`).

### Hosted development project

Review your project's existing schema before applying the migration. If tables
with these names already exist, reconcile them in a reviewed migration; do not
drop existing data or mark an unapplied migration as applied.

```text
supabase login
supabase link --project-ref YOUR_PROJECT_REF
supabase db push --dry-run
supabase db push
supabase migration list
```

These commands change your selected remote database when you run `db push`.
No remote project was linked, migrated, or reconfigured during implementation.
If CLI installation is unavailable, the version-controlled migration can be run
once in the Supabase SQL editor as postgres, but reconcile migration history
before later using CLI pushes. Prefer the CLI workflow.

Migration `20261003000000_organizations.sql` adds:

- `organizations`: `id`, `name` (trimmed, 1–100 characters), `created_at`.
- `organization_members`: `organization_id`, Supabase Auth `user_id`, constrained
  `role`, `created_at`; composite primary key prevents duplicate memberships.
- User/organization index for membership lookups. A user can join multiple orgs.
- Foreign keys: organization deletion cascades memberships; Auth user deletion
  is restricted while memberships exist. Account-deletion/ownership-transfer
  lifecycle is deferred rather than silently leaving ownerless organizations.
- RLS read policies: members can see their organizations and fellow members'
  IDs/roles, never other organizations. No emails or Auth metadata are exposed.
- Private, fixed-search-path membership helper avoids recursive RLS. Its caller
  identity is always `auth.uid()`, not a function parameter.
- `create_organization(text)` atomically creates an organization and the caller's
  owner membership. It accepts no owner/user/role/organization ID input.
- Explicit least-privilege grants: no anonymous table/function access; no direct
  authenticated insert/update/delete. Authenticated users execute only the create
  RPC and read rows allowed by RLS. No future tables or platform-admin role.

## API contract

All routes below require `Authorization: Bearer <Supabase user access token>`.
The publishable key alone is not user authentication.

| Method and path | Behavior |
| --- | --- |
| `POST /api/v1/organizations` | Body `{"name":"Example Organization"}`; 201 with organization; caller becomes owner |
| `GET /api/v1/organizations?limit=50&offset=0` | Caller memberships as `[{"organization":{...},"role":"owner"}]` |
| `GET /api/v1/organizations/{id}` | Organization after current membership authorization |
| `GET /api/v1/organizations/{id}/members?limit=50&offset=0` | Member IDs/roles for the authorized organization |

Pagination accepts limits 1–100 and nonnegative offsets. All three member roles
can read within their organizations in this phase. Team invitations, role edits,
ownership transfer, organization edits/deletion, and onboarding are not exposed.
User metadata (including any `platform_admin` text) never grants authority.

Missing/invalid/expired authentication returns 401; inaccessible and nonexistent
organizations both return 404; invalid input returns 422; provider failures or
missing backend public-key configuration return 503. No raw provider error body,
JWT, API key or organization name is logged. Authenticated responses are no-store.
Organization creation is not automatically retried: a timeout after commit can
leave an organization created, so check the organization list before retrying.

The backend validates each token with this project's `/auth/v1/user`, then calls
PostgREST with the same user token and public key. Database RLS stays enabled;
there is no privileged service-role bypass or cached membership decision.
The HTTP provider boundary uses a timeout and does not follow redirects.

Future frontend organization screens must call these FastAPI endpoints. The
existing HTTP-only session can supply its access token from a Next.js server
action/component; FastAPI still independently verifies it. Do not query these
tables directly from frontend Supabase clients or expose the token just for UI.

## Verification

```powershell
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.venv\Scripts\python.exe -m compileall -q app
```

Tests replace only the HTTP provider boundary, exercising the real FastAPI auth,
authorization, schemas and repositories. `conftest.py` isolates test configuration
from developer `.env` credentials; tests never call the remote Supabase project.

Run the SQL assertions after migration on a **disposable Supabase database** as
postgres, using psql with connection credentials supplied through your environment
or password prompt (never commit them):

```powershell
psql -v ON_ERROR_STOP=1 -f supabase/tests/tenant_isolation.sql
```

Configure standard `PGHOST`, `PGPORT`, `PGUSER`, `PGDATABASE` and password/SSL
settings for that test database first. The test file creates four test identities,
switches real database roles, asserts allowed and denied operations, and rolls
back fixtures. Do not run it on production. It is plain SQL, not a pgTAP suite.

SQL coverage includes two isolated tenants, multiple organizations per user,
owner/admin/staff reads, recursive-policy prevention, anonymous denial, direct
write denial, self-promotion denial, duplicate/invalid-role constraints, revoked
membership, Auth identity deletion restriction, and atomic creation rollback.

### Live manual API checklist

1. Apply the migration to a development project; enable the Data API and expose
   `public` but not `private`. Configure both applications for the same project.
2. Use two confirmed test users. The current frontend signup action has an
   existing unconditional error return (see verification notes below), so use
   existing confirmed accounts or create test users in Supabase Auth until that
   separate issue is fixed.
3. Obtain each user's Supabase access token in your local API client using the
   Supabase password sign-in endpoint (`POST <SUPABASE_URL>/auth/v1/token?grant_type=password`,
   `apikey: <public key>`, JSON email/password). Keep tokens/passwords private;
   never paste them into logs or commit them. Alternatively use a developer tool
   to inspect your own local HTTP-only Auth session cookie.
4. In FastAPI `/docs`, Authorize with user A's **access token**, not the public key.
   Create two organizations; list should show both with `owner` role.
5. Authorize as B; create one organization. B's list must not include A's orgs.
   Request A's organization and members using its ID: both must return 404.
6. Switch to A: B's organization ID must likewise return 404. A can read its own
   organization/members. Attempt `user_id`, `role` or `organization_id` in a create
   body: 422. Supplying IDs in arbitrary query parameters must not change identity.
7. Remove Authorization or send an expired/forged token: 401 with no data.
   A database outage must fail safely rather than grant access.
8. Use the SQL suite for multi-member/admin/staff and revoked-membership cases;
   no temporary insecure team-management endpoint is needed.

## Verification limits and next task

Implementation checks: 39 backend tests passed, Python compilation passed, and
Ruff lint/format checks passed for the Phase 1.3 Python files. A running FastAPI
server passed health, OpenAPI and unauthenticated-401 smoke checks.

Frontend files were not changed in this phase. Its type check passed; lint
reported two existing unused OAuth-state/action warnings; 9 of 11 auth tests
passed. The failures reflect existing frontend edits: `readCredentials` now adds
`display_name`, while its old test expects email/password only; `signup()` in
`frontend/app/auth/actions.ts` unconditionally returns an error before setting
`signedIn`. That signup defect should be fixed separately before relying on the
frontend signup flow for manual end-to-end testing.

The migration and SQL isolation assertions were executed with temporary PGlite
(PostgreSQL WASM) using minimal Supabase Auth schema/functions for testing. No
application dependency was added for this tool. This validates SQL/permissions
on a real PostgreSQL engine, but does not validate hosted Supabase Auth/PostgREST
integration or a Docker-based local stack. Repeat the SQL and live API checklist
on your configured Supabase project before considering deployment verified.

Next: Phase 1.4 organization onboarding. It has not been implemented.

References: [RLS](https://supabase.com/docs/guides/database/postgres/row-level-security),
[database functions](https://supabase.com/docs/guides/database/functions),
[CLI configuration](https://supabase.com/docs/guides/local-development/cli/config).
