# OpsLens AI — Codebase Skill Reference

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 14.2.5 · React 18 · TypeScript · TailwindCSS |
| Backend | FastAPI (Python 3.11+) · async SQLAlchemy |
| Task Queue | Celery + Redis |
| Database | PostgreSQL 15 (relational) · Qdrant (vector search) |
| Auth | Clerk (JWT RS256 · RBAC) |
| UI Primitives | Radix UI · Sonner (toasts) · Lucide icons · Recharts |
| Data Fetching | SWR |
| Deployment | Railway.io (monorepo: web, api, worker, beat services) |

## Repository Layout

```
/home/user/opslens-ai/
├── apps/web/                          # Next.js frontend
│   ├── src/app/                       # App router pages
│   │   ├── _components/LandingPage.tsx  # Public landing page
│   │   ├── _components/BookDemoModal.tsx # Book Demo form modal
│   │   ├── api/book-demo/route.ts     # Next.js API — demo form handler
│   │   ├── sign-in / sign-up          # Clerk auth pages
│   │   ├── chat/                      # Main app chat view
│   │   ├── settings/                  # Team/member management
│   │   ├── alerts/                    # Alert rules CRUD
│   │   ├── integrations/              # Integration management
│   │   ├── routing-rules/             # Alert routing rules
│   │   ├── platform/                  # Platform overview
│   │   ├── admin/                     # Admin: users & teams
│   │   └── onboarding / join          # Invite redemption
│   ├── src/components/
│   │   ├── alerts/AlertRuleForm.tsx   # Form component pattern reference
│   │   └── ...
│   ├── src/lib/api.ts                 # Centralized fetch wrapper (all API calls)
│   ├── src/types/index.ts             # Shared TypeScript types
│   └── package.json
└── opslens_backend/apps/api/
    ├── main.py                        # FastAPI entry point
    ├── routers/                       # Route modules (alerts, chat, insights, etc.)
    └── ...
```

## Frontend Conventions

### Styling
- Landing page uses **CSS-in-JS string** injected via `<style dangerouslySetInnerHTML>` — no Tailwind on landing
- App pages use **TailwindCSS** utility classes
- Color palette: `--teal: #14B8A6`, `--navy: #0F172A`, `--navy-mid: #1E293B`, `--text: #F1F5F9`

### Form Pattern (controlled components)
```tsx
const [field, setField] = useState('');
// controlled <input value={field} onChange={e => setField(e.target.value)} />
// disable submit button on loading or missing required fields
// loading state: disable button + show "Saving…" / "Sending…" text
```

### API Calls
```ts
// src/lib/api.ts — centralized wrapper
// Prefixes /api/v1 for backend routes (proxied via next.config.ts)
// Auto-attaches Clerk JWT: const token = await getToken(); 
// Throws ApiError on non-2xx
```

### Next.js API Routes
- Placed in `apps/web/src/app/api/<route>/route.ts`
- Use `NextRequest` / `NextResponse` from `next/server`
- No auth required for public endpoints (e.g. book-demo form)

## Key Environment Variables (web)

| Variable | Purpose |
|----------|---------|
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Clerk frontend key |
| `CLERK_SECRET_KEY` | Clerk backend key |
| `NEXT_PUBLIC_API_URL` | FastAPI URL (public, for client fetches) |
| `API_URL` | FastAPI URL (server-side) |
| `RESEND_API_KEY` | Resend — used by `api/book-demo` route to email demo requests |
| `DEMO_REQUEST_EMAIL` | Override destination email (default: `admin@opslensai.com`) |

## Book Demo Flow

1. User clicks "Book a Demo" on landing page (nav, hero, or CTA section)
2. `BookDemoModal` (`_components/BookDemoModal.tsx`) opens as overlay
3. Fields: Full Name*, Work Email*, Company*, Role, Team Size, Message
4. `POST /api/book-demo` → `app/api/book-demo/route.ts`
5. If `RESEND_API_KEY` set → sends email via Resend to `DEMO_REQUEST_EMAIL`
6. If no key → logs to console (dev/staging fallback)
7. Modal shows success state on 2xx, inline error on failure

## Git

- Main branch: `main`
- Active feature branches follow: `claude/<feature>-<id>`
- Current feature branch: `claude/add-book-demo-form-4ixL1`
- Always push to designated branch; never push to main directly

## Backend API Modules (FastAPI routers)

Located in `opslens_backend/apps/api/routers/`:
- `alerts` — alert rule CRUD
- `chat` — AI chat interface
- `insights` — log insights
- `integrations` — Slack, GitHub, Jira connectors
- No lead/demo capture in backend — handled by Next.js API route

## Common Gotchas

- `LandingPage.tsx` is a `'use client'` component with inline CSS; Tailwind classes don't apply here
- The FastAPI backend is proxied at `/api/v1/*` via `next.config.mjs` rewrites — Next.js API routes must live outside `/api/v1/` (e.g. `/api/book-demo`) to avoid being swallowed by the rewrite
- Clerk `getToken()` must be awaited before every authenticated API call
- `btn-primary` and `btn-secondary` are CSS classes defined in `LandingPage.tsx`'s inline `CSS` string — they work on both `<a>` and `<button>` elements
