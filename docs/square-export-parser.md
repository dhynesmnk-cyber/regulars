# Square export parser (`regulars/`)

A standalone tool, separate from the Regulars app in `app/`. It loads Square Transactions + Items Detail exports into SQLite and deliberately drops every customer-identifying column, which the app's importer does not (the app needs email addresses and consent to send).

Loads Square **Transactions CSV** and **Items Detail CSV** exports into SQLite so you can
answer "what sells when, and what does it cost me in fees" per venue. Standard library only
(Python 3.9+), no dependencies.

```bash
python -m regulars demo --out demo                          # (re)write the synthetic demo pair
python -m regulars load demo --db regulars.db --venue "Demo Wine Bar"
python -m regulars check --db regulars.db                   # Transactions vs Items reconciliation
python -m regulars summary --db regulars.db                 # headline numbers
python -m unittest discover -s tests -t .                   # tests
```

`load` takes any mix of CSV files, ZIPs (Square bundles wide date ranges as monthly files)
and folders. Loading the same file twice, or two exports that overlap, never double-counts.

## What it does with the data

| Concern | Behaviour |
|---|---|
| Columns | Matched by **header name**, never position. Reordered, missing or extra columns are fine; unknown columns are listed so you notice when Square adds one. |
| Which file is which | Detected from the headers. The Items **Summary** CSV (aggregated, no timestamps) is recognised and skipped with a message. |
| Money | Integer cents (`*_c` columns), so sums are exact. Understands `$1,234.56`, `-$3.20`, `($3.20)`, `A$5`. |
| Dates | ISO, or slash dates read **day-first** (Australian). Month-first dates are refused rather than guessed. |
| Time | Venue **local wall-clock**, not converted to UTC (hour-of-day analysis wants the venue's clock; DST makes local→UTC ambiguous). `local_dow` is Monday = 0. |
| Joining the files | On **Transaction ID and refund-or-not**, with both sides aggregated first (`v_txn_items`). Joining on Transaction ID alone would attach a refund to the original sale's items and multiply money. |
| Which file owns the money | Fees, tips, tender and totals come from the **Transactions** file only. Categories come from **Items** only. Never sum sale-level values out of item rows. |
| Bad data | A bad cell is blanked and reported; a bad date/time skips that row only. One odd row never aborts an import. |
| Reload safety | Every row has a stable identity (transaction, payment, event type, timestamp, repeat count). One transaction per file: a failure part-way leaves nothing behind. |
| Memory | Rows stream into SQLite in chunks. A 2-year, 76 MB export pair (184k rows) loads in ~10 s using ~290 MB. |

### Views

`v_hourly` and `v_daily` (sales, tips, fees), `v_category_hourly` (what sold when),
`v_txn_items` (one row per transaction with fees **and** categories).

## Privacy: what is never stored

These columns are recognised and dropped at ingestion. There is no switch to keep them:
`Customer ID`, `Customer Name`, `Customer Reference ID`, `PAN Suffix`, `Tender Note`,
`Fulfillment Note`, `Notes`. A test plants customer data in the demo files and asserts it
appears nowhere in the database, and `load` prints which columns it dropped.

Still stored, because analysis needs them: staff names/IDs, item names, `Refund Reason`
(staff-typed free text, so it could contain a name; drop it in `regulars/schema.py` if you
want a hard guarantee).

## Read this before trusting it on real data

**Verified:** column *names* in the reference header rows (`regulars/schema.py`) came from
real **US** seller exports posted publicly, and independent forum posts agree on several
column positions. The code and tests are exercised end to end on synthetic data.

**Not verified, and the first thing to do with a real Australian export:**

- Whether AU exports match those headers (Cash App etc. may be absent) and whether Items
  Detail really has a `Category` column. One forum thread suggests category may be missing
  from some exports; `check` and the `unmapped columns` line will tell you.
- Value formats: currency symbol, date format, time-zone strings, and whether refunds are
  negative. The parser keeps values as exported and reports disagreements instead of
  assuming.
- How Square represents refunds and split payments. The join is built to survive either
  convention, but run `python -m regulars check` on a real file and read the
  `net_mismatch`, `multi_payment_unchecked` and `*_without_*` counts. Some are expected
  (gift-card sales, partial refunds); they just shouldn't be a surprise.
- Whether a single huge file is possible. Memory is bounded per chunk of rows, but the file
  itself is read whole, so prefer Square's monthly files.

## Layout

```
regulars/schema.py   column knowledge: field maps, private/ignored sets, file detection
regulars/values.py   money / date / time parsers
regulars/reader.py   CSV, ZIP and folder reading -> rows + issues
regulars/db.py       SQLite schema, idempotent load, views, reconcile, summary
regulars/demo.py     deterministic synthetic export generator
regulars/cli.py      python -m regulars {load,check,summary,demo}
demo/                the generated synthetic pair (see demo/README.md)
tests/               unittest suite
```
