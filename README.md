# Rentbook — family rent & maintenance records

A private web app for two buildings (Pink: 6 flats, Blue: 4 flats). It tracks:
- tenants and monthly rent dues;
- cash and online rent payments, late, partial or extra;
- HDFC bank statement reconciliation;
- a family cashbook;
- the caretaker's ₹15,000 maintenance top-ups;
- the caretaker's separate ₹15,000 salary;
- reports, exports and backups.

Everything runs on your own computer with open-source software. There are no
paid services, no cloud accounts and no AI services. Bank matching uses fixed
rules plus the sender names you teach it.

---

## 1. What you need

| Tool | Version | Why |
|---|---|---|
| Python | 3.12 or newer | Runs the app |
| [uv](https://docs.astral.sh/uv/) | any recent | Installs the Python packages |
| Node.js | 22 or newer | Only to build the web pages (once, and after updates) |

(Docker is optional; see §8.)

## 2. First-time setup

```powershell
# from the project folder (the one containing this README)
cd backend
uv sync                         # installs Python packages into backend/.venv
cd ../frontend
npm ci                          # installs the web-page build tools
npm run build                   # creates frontend/dist
cd ../backend
uv run python -m app.cli create-user --name "Your Name"     # asks for a 6–12 digit PIN
uv run python -m app.cli create-user --name "Second Person"  # the other family member
```

`create-user` also creates the database at `data/rentbook.db` and applies all migrations.
It also sets up the two buildings with ten flats. Their labels (P-G1 … B-3) are
placeholders, so rename them in **Settings → Buildings & flats**. **No tenant or
money data is invented**; you enter everything yourself.

## 3. Starting the app (everyday use)

```powershell
cd backend
uv run python -m app.cli serve            # http://127.0.0.1:8000  (this computer only)
```

Open <http://127.0.0.1:8000> and log in with your name and PIN. Stop with `Ctrl+C`.

To let the second family member use it from a phone or laptop on the **same home Wi-Fi**:

```powershell
$env:RENTBOOK_ENV="production"; $env:RENTBOOK_COOKIE_SECURE="false"
uv run python -m app.cli serve --host 0.0.0.0 --port 8000
```

Then open `http://<this-computer's-IP>:8000` on the other device (find the IP with `ipconfig`).
Windows will ask to allow Python through the firewall: allow it for **Private** networks only.
See §9 for what this means for security.

## 4. Development mode (live reload)

```powershell
# terminal 1
cd backend;  uv run uvicorn app.main:app --reload --port 8000
# terminal 2
cd frontend; npm run dev        # http://localhost:5173 (proxies /api to port 8000)
```

API docs are at <http://127.0.0.1:8000/api/docs> outside production.

## 5. Tests

```powershell
cd backend;  uv run pytest           # 100 tests: rent, bank (incl. HDFC legacy-XLS layout), maintenance, cash, security, backups, migrations
cd frontend; npm test                # 9 tests: money parsing, payment form workflows, status badges
cd frontend; npm run typecheck
```

Tests use their own temporary database with made-up names, never your data.

## 6. Getting started with your real data

1. **Settings → Buildings & flats**: rename flats and floors to match your sketches.
2. For each let flat: **Buildings → flat → "New tenant moves in"**. Enter:
   - move-in date, rent and due day;
   - **"Track rent in this app from"**: use the current month for long-standing tenants, so the app does not invent years of old dues.
3. **Settings → Opening balances**: enter anything owed or held *before* you started:
   - tenant arrears;
   - cash the caretaker holds;
   - cash the family holds.

   These are clearly labelled as manual balances. No fake payments are created.
4. **Tenants**: add the names that appear on each tenant's bank transfers (for example a relative's name).
5. Family-occupied flat: open it and choose **"Family lives here"**. No rent is generated.

### How the money rules work

- **Rent due**: one record per tenancy per month, priced from the rent in force that month.
  - Changing the rent "from November" never alters October or earlier.
  - Part-month rules are configurable (Settings → Rates & rules). The default charges a month when the tenancy covers its due date. Prorating is used only if you choose it.
- **Outstanding** = rent due − *confirmed* payments. *Pending verification* payments are shown but never counted.
- **Late payments**: the rent month and the date received are separate. "October rent paid on 8 November" settles October and is flagged as late.
- **Extra money** is never placed silently. You choose one of:
  - next month(s);
  - older arrears;
  - keep as credit;
  - to be refunded.
- **Corrections**: payments are never deleted. They are rejected or reversed with a reason, and corrected payments link to the original. Everything is in **Settings → Audit history**.
- **Maintenance**: top-up needed = max(0, ₹15,000 − cash the caretaker held at the start of the month).
  - Unspent cash carries forward.
  - Expenses beyond his cash are recorded as **paid by family**, never by quietly raising the budget.
  - A negative balance is shown as a discrepancy, never hidden as zero.
- **Salary** has its own page and never touches maintenance cash.
- **Cash**: a cash rent receipt is counted once. Handing that cash to the caretaker is a separate *movement*, optionally linked to the receipt, and never a second receipt.
- **Effective-dated amounts**: the ₹15,000 maintenance target and ₹15,000 salary can be changed "from month X" without rewriting history.

### Bank statements (HDFC)

**Bank Reconciliation → Import statement**: choose the CSV/XLSX/XLS downloaded from HDFC NetBanking.

1. Columns are detected automatically (you can change them, choose a sheet, and save the layout).
   The account-holder block at the top of the file (name, address, account and customer numbers) is never sent back to the browser.
2. Review the preview: counts, totals, rows already imported, unreadable rows, and **Statement checks**. The importer reads the bank's own summary at the end of the file (period, opening/closing balance, total debits/credits, Dr/Cr counts) and confirms:
   - the number of debits and credits matches the summary;
   - both totals match the summary;
   - opening − debits + credits = closing;
   - every row's closing balance follows on from the previous row.

   A failure means a row is missing or a column is mapped wrongly. Fix the mapping before importing.
3. Import, then go to **Review credits**. Each credit gets a suggestion (strong / possible / ambiguous / no match / possible duplicate) with the reasons. **Nothing is marked paid until you review and confirm.**

You can split one credit across months or flats, hold the extra as credit, mark it **not rent**, link it to a payment you already entered by hand, or leave it for later.

**Rows dated just outside the statement period.** HDFC lists transactions by *value date*. A statement for 1 Jul–30 Sep can therefore contain a row dated 30 Jun (value date 1 Jul), or an interest credit dated 1 Oct (value date 30 Sep). These are real entries, so they are **imported, not dropped**, and marked "dated outside statement period". If the neighbouring statement also contains the same entry, it is recognised as a duplicate. A row whose value date is *also* outside the period is imported with a warning so you can check it.

Re-importing the same or an overlapping statement never creates duplicates, even when one month was downloaded as XLS and the next as CSV. Each row is identified by date, narration, reference, amounts and closing balance. If a statement has no balance column, identical rows in one file are flagged for you to check.

## 7. Backups and restore

**Git is not a backup** — the database and documents are deliberately not in Git.

- **Create**: Settings → Backup → *Create backup now*, or `uv run python -m app.cli backup`.
  - It makes a zip in `data/backups/` containing the database, all uploaded documents, and plain CSV copies of every table.
  - SQLite is copied with its online-backup API, so it is safe while the app runs.
- **Keep copies elsewhere**: download the zip (or copy `data/backups/`) to a USB drive or another computer, at least monthly.
- **Check a backup**: `uv run python -m app.cli verify-backup data/backups/<file>.zip`.
- **Restore** (stop the app first):
  ```powershell
  uv run python -m app.cli restore data/backups/<file>.zip --yes
  ```
  Before replacing anything, the current state is saved as a `before-restore` backup, and the old uploads folder is kept as `data/uploads.before-restore-…`.
- **Records without the app**: Reports → *Download records* gives every table as CSV. Money columns ending `_paise` are in paise (÷100 = rupees).
- **PostgreSQL** (only if you switch to it): use `pg_dump -Fc rentbook > rentbook.dump` and `pg_restore -d rentbook rentbook.dump`, plus a copy of `data/uploads/`.

## 8. Docker (optional)

Tested with Docker 29.7 / Compose v5.5. Run these from the project folder:

```powershell
docker compose up -d --build                                           # build and start (http://127.0.0.1:8000)
docker compose exec rentbook python -m app.cli create-user --name "Your Name"   # once per family member
docker compose ps                                                      # should show "(healthy)"
docker compose logs -f rentbook                                        # follow the logs (Ctrl+C to stop following)
docker compose stop                                                    # stop; `docker compose start` to resume
docker compose down                                                    # stop and remove the container; data is kept
```

**Where the data lives.** The database, uploaded documents and backups are in the named Docker volume `rent_rentbook-data`, mounted at `/data`, not inside the container.
- Restarts, `docker compose down` / `up`, and rebuilding after an update all keep the data.
- **Never run `docker compose down -v`**: `-v` deletes the volume, which means all records.
- The Docker copy and the non-Docker copy (§2–3, folder `data/`) are separate databases. Use one or the other.

**Settings.** The data location is fixed in `docker-compose.yml`, so a `.env` file cannot move the database into the disposable container. On start-up the container:
1. applies any pending migrations, after making an automatic `pre-migration` backup;
2. never resets existing data.

Production mode is on: secure session settings apply, and API docs are hidden. Set `RENTBOOK_PORT` to use a port other than 8000.

**Backups with Docker.**

```powershell
docker compose exec rentbook python -m app.cli backup                 # or Settings -> Backup in the app
docker compose cp rentbook:/data/backups/. ./docker-backups/          # copy all backup zips into the docker-backups folder (git-ignored)
```

**Restore with Docker** (replace the file name):

```powershell
docker compose cp ./docker-backups/rentbook-backup-XXXX.zip rentbook:/data/backups/   # only if it is not already in the volume
docker compose stop
docker compose run --rm rentbook python -m app.cli restore /data/backups/rentbook-backup-XXXX.zip --yes
docker compose start
```

The restore keeps a `before-restore` backup of what it replaced.

> Using Git Bash instead of PowerShell? Prefix `docker compose exec/run` commands with `MSYS_NO_PATHCONV=1`, or Git Bash rewrites the `/data/...` paths.

## 9. Security notes

- **Logins**: a name plus a 6–12 digit PIN.
  - PINs are stored only as Argon2id hashes.
  - After 5 failed attempts from one address, login is blocked for 15 minutes.
  - Sessions are random server-side tokens in an HttpOnly, SameSite=Strict cookie, and every change needs a CSRF token.
  - Every `/api` route except health and login requires a session, including document downloads.
  - Users are created from the command line, so there is no "first visitor becomes admin" screen. Add or change logins in Settings → Login & PIN.
- **Uploaded files**:
  - stored under `data/uploads/` with random names;
  - type-checked by content;
  - only served to logged-in users.
- **Home network (plain http)**:
  - Anyone on your Wi-Fi can reach the login page, and traffic is not encrypted on the network.
  - Use a strong Wi-Fi password and keep the firewall rule set to Private networks.
- **Do not forward the port on your router.** For access away from home, use a private VPN such as WireGuard or Tailscale's open-source client, or put the app behind an HTTPS reverse proxy (for example Caddy) with `RENTBOOK_ENV=production` and `RENTBOOK_COOKIE_SECURE=true`.
- **Logs**: no request bodies, PINs or bank details are logged. Error responses to the browser are generic.

## 10. Updating the app

```powershell
uv run python -m app.cli backup          # always back up first
git pull                                 # or copy in the new files
cd backend;  uv sync
cd ../frontend; npm ci; npm run build
cd ../backend; uv run python -m app.cli migrate   # also runs automatically at start-up
```

Migrations never delete data. When a migration is needed, a `pre-migration` backup is created automatically first.

## 11. Health check

`GET /api/health` → `{"status": "ok"}` (no login needed; it checks the database connection).

## 12. Project layout

```
backend/   FastAPI app (app/), Alembic migrations (alembic/), tests (tests/)
  app/services/   business rules: rent.py, bank.py, bank_parse.py, cash.py, maintenance.py, opening.py …
  app/routers/    HTTP endpoints
frontend/  React + TypeScript + Vite + Tailwind UI (src/pages, src/components)
data/      your database, uploads and backups (created on first run, never committed)
```

## 13. Known limitations

- **Statement formats**: CSV, XLSX and genuine XLS are supported. HDFC's legacy `.xls` NetBanking export has been validated against a real statement: all rows read, counts and balances reconciled. Some banks export "XLS" files that are really HTML. Re-save those as XLSX/CSV, or download CSV from NetBanking. PDF statements are not read.
- **Identical rows without a balance column**: if two truly identical transactions appear on the same day *and* the statement has no closing-balance column, an overlapping statement that cuts between them could hide the second one. Such rows are flagged; HDFC statements include a balance, which prevents this.
- **Placeholders**: flat labels (P-G1 … B-3) are temporary; rename them in Settings.
- **PostgreSQL** is supported by the code (SQLAlchemy), but the built-in backup button is SQLite-only and the PostgreSQL driver is not installed by default.
- **Device times**: dates use the server computer's local clock and time zone.
