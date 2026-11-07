# Move the SpaceIO database from Sydney to Mumbai

Goal: cut DB round trip from ~300 ms (ap-southeast-2) to ~5-20 ms (ap-south-1).
Supabase cannot change a project's region in place, so this is a new project plus a dump/restore.
The app uses its own JWT auth and its own tables (no Supabase Auth), so only the `public` schema needs to move.

Expected downtime: 10-30 minutes, depending on data size. Do it at a quiet time.

## 0. Before you start
- [ ] Note the old project's Postgres major version (Dashboard -> Settings -> Infrastructure).
- [ ] Install a matching `pg_dump` / `psql` (`brew install libpq` or `brew install postgresql@<major>`).
- [ ] Keep the old project running and untouched until the end: it is your rollback.
- [ ] Check cost: a new project on a paid plan is billed separately while both exist.

## 1. Create the new project
1. Supabase Dashboard -> New project, **Region: South Asia (Mumbai) `ap-south-1`**, same org.
2. Pick a strong DB password and store it in your password manager.
3. Dashboard -> Connect -> copy the **Transaction pooler** string (port 6543). Host looks like
   `aws-0-ap-south-1.pooler.supabase.com`; use exactly what the dashboard shows.
4. Also copy the **Session pooler / direct** string (port 5432) for the dump and restore. Use that one for step 3.

## 2. Freeze writes on the old DB
Stop the backend (or put it in maintenance) so no rows change during the copy.

## 3. Dump and restore (public schema only)
Use the session/direct (5432) URLs, not the 6543 transaction pooler.

```bash
export OLD_URL='postgresql://postgres.<old-ref>:<old-password>@<old-session-host>:5432/postgres'
export NEW_URL='postgresql://postgres.<new-ref>:<new-password>@<new-session-host>:5432/postgres'

pg_dump "$OLD_URL" --schema=public --no-owner --no-privileges --format=custom -f spacio.dump
```

```bash
pg_restore --dbname="$NEW_URL" --no-owner --no-privileges --clean --if-exists spacio.dump
```

Don't commit `spacio.dump`: it contains user data and password hashes. Delete it after verifying.

## 4. Verify the copy
Compare row counts on both databases for every table (users, clients, projects, rooms, quotations,
quotation_items, payments, categories, items, ...):

```bash
for U in "$OLD_URL" "$NEW_URL"; do
  psql "$U" -At -c "select relname, n_live_tup from pg_stat_user_tables order by 1"
  echo ---
done
```

`n_live_tup` is approximate. For the key tables also run `select count(*) from <table>` on both.
Then run `migrate.py` only if it is idempotent and you need it; check its contents first.

## 5. Point the app at the new DB
In `.env` (not committed), replace `supabase_pooler_url` with the new **transaction pooler (6543)** URL.
Delete the old commented-out URL lines that contain the old password.

```bash
# restart the backend, then check
curl -s -o /dev/null -w "%{time_total}s\n" http://127.0.0.1:8000/
```

Expected: authenticated GETs drop from ~290 ms to roughly 20-60 ms, depending on your distance to Mumbai.

## 6. Smoke test
Log in, open clients, projects and quotations, create and edit a client, generate a quotation PDF,
and run one Razorpay test flow if you have test keys.

## 7. Cut over and clean up
- Rotate the new DB password if it was ever pasted into chat or logs.
- Keep the old project paused (not deleted) for ~1 week, then delete it.
- Delete `spacio.dump`.

## Rollback
Put the old `supabase_pooler_url` back in `.env` and restart. Any rows written to the new DB after
cutover would need to be copied back manually, so keep the freeze window short and verify before reopening.

## Optional extra gains once the DB is local
- Deploy the backend in/near Mumbai too, if it isn't already, so the browser-to-API hop is short.
- `DB_SSL_CA`: download Supabase's CA cert and set it, so TLS is verified (the app warns about this).
