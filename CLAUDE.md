```markdown
# CLAUDE.md — ProDash FastAPI Backend

## Prime directive
Read `agent.md` fully before writing any code. It is the single source of
truth for business rules, API contracts, legacy defects, and conventions.

## Stack
- Python 3.12
- FastAPI + Pydantic v2
- SQLAlchemy 2.x async (mysql+aiomysql for central DB, psycopg for plant DBs)
- Alembic for central-DB migrations (plant DBs are read-only, no migrations)
- JWT (python-jose), passlib[bcrypt] for auth
- pytest + pytest-asyncio for tests
- pydantic-settings for config

## Project layout (target)

prodash-api/  
├── app/  
│ ├── core/ # config, db engine factory, security helpers  
│ ├── models/ # SQLAlchemy ORM models (central DB only)  
│ ├── schemas/ # Pydantic request/response models  
│ ├── services/ # business logic (pure functions, no HTTP concerns)  
│ ├── repositories/ # SQL queries (central DB and plant DB)  
│ ├── api/  
│ │ ├── v1/ # clean typed router (/api/v1/...)  
│ │ └── legacy/ # parity router (/prodash/...) for transition testing  
│ └── main.py  
├── tests/  
│ ├── unit/  
│ └── parity/ # compare legacy PHP output vs new Python output  
├── alembic/  
├── agent.md  
├── CLAUDE.md  
├── pyproject.toml  
└── .env.example


## Critical rules (never break these)
- Business logic lives in `services/` only. Routers call services, never SQL.
- Plant DBs are READ-ONLY. No INSERT/UPDATE/DELETE against plant connections.
- PHP numeric compatibility: use `php_round()` (Decimal ROUND_HALF_UP), not Python built-in `round()`.
- Password hash: bcrypt(base64(sha384(password))) — see agent.md §7.8.
- SQL: always use bound parameters. No f-string SQL.
- Preserve all legacy defects (D-01..D-27) unless agent.md §11 says otherwise.
- Log every plant DB connection attempt with latency.

## Environment variables (never hardcode)
All secrets come from `.env` (gitignored). Use `.env.example` with placeholder values only.

## Definition of done (per feature)
1. Service layer function with full type annotations
2. Unit test (mocked DB)
3. Parity test comparing output to PHP baseline (where possible)
4. OpenAPI doc strings on every route
5. No linting errors (`ruff check .`)

```