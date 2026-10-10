# Third-party software

Rentbook is built only on open-source software. No paid service, API, or
commercial licence is needed to run it. Licences below were read from each
package's installed metadata (versions as of the initial release). Each licence
has its own conditions. Most notably, Apache-2.0 requires keeping its NOTICE
and licence text when you redistribute the software. Check each project's
licence before redistributing a modified copy.

## Backend (Python)

| Package | Version | Licence | Used for |
|---|---|---|---|
| FastAPI | 0.143.0 | MIT | Web API framework |
| Starlette | 1.7.0 | BSD-3-Clause | ASGI toolkit under FastAPI |
| Uvicorn | 0.54.0 | BSD-3-Clause | Application server |
| SQLAlchemy | 2.1.4 | MIT | Database access (ORM, parameterised queries) |
| Alembic | 1.20.0 | MIT | Database migrations |
| Mako, MarkupSafe | 1.4.3 / 3.0.4 | MIT / BSD-3-Clause | Alembic templates |
| Pydantic (+ pydantic-core) | 2.14.0 / 2.50.0 | MIT | Input validation |
| argon2-cffi (+ bindings, cffi) | 25.1.0 / 26.1.0 / 2.1.1 | MIT / MIT / MIT-0 | Argon2id PIN hashing |
| python-multipart | 0.0.32 | Apache-2.0 | File-upload parsing |
| openpyxl (+ et-xmlfile) | 3.1.5 / 2.0.0 | MIT | Read XLSX statements, write XLSX reports |
| xlrd | 2.0.2 | BSD | Read legacy XLS statements |
| anyio, h11, click, annotated-types | various | MIT / MIT / BSD-3-Clause / MIT | Transitive runtime dependencies |
| typing-extensions | 4.16.0 | PSF-2.0 | Transitive runtime dependency |
| SQLite | (bundled with Python) | Public domain | Database engine |

Development and test only: pytest (MIT), httpx (BSD-3-Clause), xlwt 1.3.0 (BSD; writes synthetic legacy-XLS test fixtures).

## Frontend (JavaScript / TypeScript)

| Package | Version | Licence | Used for |
|---|---|---|---|
| React, React DOM | 19.3.0 | MIT | User interface |
| React Router | 8.4.0 | MIT | Page navigation |
| lucide-react | 1.54.0 | ISC | Icons |
| Tailwind CSS (+ @tailwindcss/vite) | 4.3.3 | MIT | Styling (build time) |
| Vite (+ @vitejs/plugin-react) | 8.3.4 / 6.1.2 | MIT | Build tool (build time) |
| TypeScript | 7.0.2 | Apache-2.0 | Type checking (build time) |

Development and test only: Vitest (MIT), jsdom (MIT), Testing Library (react, user-event, jest-dom; MIT).

The production build bundles React, React DOM, React Router and lucide-react
into `frontend/dist`. Their MIT/ISC licences require keeping the copyright
notice, and Vite keeps licence comments in the bundle.

## Runtime

Python (PSF License), Node.js (MIT, only needed to build the frontend), and
optionally Docker Engine (Apache-2.0). Docker Desktop has its own commercial
terms, but it is **not** required: the app runs directly with Python.
