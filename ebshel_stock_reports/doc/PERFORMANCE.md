# Performance

## Benchmark command

On a throw-away database with demo data (`--with-demo`), from an Odoo shell:

```python
timings = env['asr.benchmark']._run(n_moves=1000000, n_products=20000)
env.cr.commit()
print(timings)
```

`_run` generates the volume with SQL (receipts and deliveries over two years
on AVCO products, quants rebuilt from the moves), then times:

* `full_rebuild`: a complete rebuild of both daily tables for the company;
* `incremental_50_products`: one value write on one move of fifty products,
  then the queue;
* `ledger_rows` / `ledger_xlsx`: the stock ledger of the whole company for
  the current month, on screen and to Excel.

## Measured timings

Reference run: `_run(n_moves=1000000, n_products=20000)` on a 4 vCPU / 15 GB
container, PostgreSQL 16.14 with `fsync=off`, Odoo 19.0 Community at commit
2cc17f73, demo database, three companies, AVCO products, moves spread over
two years (982,483 summary rows, 966,524 valuation rows after the rebuild).

| Step | Time |
| --- | --- |
| Generate 20,000 products (ORM) and 1,000,000 done moves + lines + quants (SQL) | 222 s |
| Full rebuild of both daily tables for the company (cron path, 40 products per chunk) | 275 s |
| Incremental recompute after one value write on one move of 50 products | 0.8 s |
| Stock ledger, whole company, current month: rows (20,007 products) | 3.2 s |
| Stock ledger, whole company, current month: Excel file | 6.0 s |
| Table sizes after the run (`pg_total_relation_size`) | 208 MB movement, 205 MB valuation |

The full rebuild is linear in the number of products and moves (a 20,000
move / 500 product run took 4.9 s). The incremental path is what the
five-minute cron runs in production: it only rebuilds the products that
changed, from the first day that changed.

## Indexes

The module adds, on its own tables only:

* `asr_stock_move_daily`: `(company_id, product_id, day)`,
  `(company_id, day, move_type)`, `(location_id, day)`;
* `asr_stock_value_daily`: unique `(company_id, product_id, day)`,
  `(company_id, day)`;
* `asr_stock_dirty`: unique `(company_id, product_id, day_from)`;
* `asr_product_class`: unique `(company_id, product_id)`.

No index is added on core tables. The summary builder scans `stock_move` by
`(product_id)` joined to the `VALUES` list of products to rebuild and filtered
on `date` (both indexed by core), and `stock_move_line` by `move_id` (indexed
by core). Any further index will only be proposed with an `EXPLAIN ANALYZE`.
