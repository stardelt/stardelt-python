# stardelt notebook helper

The small package the notebook image ships: `stardelt.sql` (query as the
signed-in hub user), `stardelt.write_df` (governed DataFrame writes),
`stardelt.spark` (Spark Connect session), `stardelt.connection` (a named
connection to an outside system: your own or your group's; values are read from the secret store when you ask,
never kept or printed).

Pure Python, MIT. This repository is the library's home: the platform's
notebook image installs a **pinned release** of it, and both this repository's
CI (`tests/`) and the platform's `notebook-helper` job run the tests at that
pinned ref. The full contract is the docstrings in `__init__.py` — this file
exists so the build backend has a readme.

## The Nova API client: `stardelt.api`

```python
import stardelt.api

nova = stardelt.api.Client()      # STARDELT_NOVA_URL and the notebook's own token
nova.me()                         # the signed-in person, a dict
nova.query("select 1 as x").to_pandas()
nova.statement("select * from warehouse.demo.trips").to_pandas()   # long queries: polled, up to 100000 rows
```

`Client(base_url=None, token=None)` sends the token as a bearer token (a string, or a function that returns
one). An error answer from Nova becomes a `StardeltApiError` with Nova's one sentence and the HTTP status; a
token never appears in a message or a `repr`. `query()` returns at most the rows Nova's cap allows; check
`result.truncated`.

Every public operation of the contract is also a method, grouped by resource: `nova.ops.<resource>.<verb>(...)`.
The names are the `operationId`s split at the first underscore, the keyword arguments are the path and query
parameters of the operation (`body=` is the JSON body), and `dir(nova.ops)` and `dir(nova.ops.pipelines)` list
what exists, so tab completion works in a notebook:

```python
nova.ops.pipelines.list()
nova.ops.pipelines.get(namespace="etl", name="nyc_taxi")
nova.ops.pipelines.run_get(namespace="etl", name="nyc_taxi", run_id="r1")
nova.ops.query.log_list(scope="mine", state="FAILED", limit=20)
nova.ops.table.history(ns="demo", tbl="trips")
nova.ops.health()                  # a resource with a single operation is called directly
```

A wrong argument name is a `TypeError` that lists the accepted ones, before any request is sent.

## Notebook tasks: `python -m stardelt.notebook_run`

The pod a notebook task runs holds no storage credential: the DAG signs two URLs and the wrapper
downloads the pinned notebook from `NOTEBOOK_GET_URL`, runs papermill with `run_date`, `run_id`
and `NOTEBOOK_PARAMS` as raw `-r` parameters, uploads the executed copy to `NOTEBOOK_PUT_URL`
(after a failure too), and on SIGTERM cancels the statements it recorded in `STARDELT_QUERY_IDS`
before exiting 143 (papermill's own code otherwise, 2 for a contract error). Stages and byte counts
are logged — never a URL, a token, a parameter value or notebook text.

## Reading public S3 with Spark

A `stardelt.spark()` session reads a **public** S3 bucket anonymously, with either
URI form Spark understands:

```python
spark = stardelt.spark()
spark.read.csv("s3a://noaa-ghcn-pds/csv.gz/by_year/1763.csv.gz").count()
spark.read.parquet("s3://ookla-open-data/parquet/performance/type=fixed/year=2023/quarter=1/").limit(5).show()
```

Only public buckets answer: S3A runs with `AnonymousAWSCredentialsProvider`, so a
private bucket fails with an access error, and the platform's own storage
(`s3a://lakehouse/`) is not reachable this way — read it through Trino or an Iceberg
table, where the catalog's grants apply. The jars and the switch live in the Spark
image and `manifests/spark.yaml`; the platform README ("Reading public S3 with
Spark") has the details.

## Python on executors

UDFs run. `udf`, `pandas_udf`, `mapInPandas`, `applyInPandas`, `foreach` and
`foreachPartition` execute the Python you write on the Spark workers:

```python
from pyspark.sql import functions as F

spark = stardelt.spark()
spark.range(5).select(F.udf(lambda x: x + 1, "long")("id").alias("v")).show()
```

The worker interpreter is CPython **3.11** — the same minor as this notebook — with
exactly `pandas==2.2.3`, `pyarrow==18.1.0` and `numpy==2.0.2`, this image's versions.
Nothing else is installed there: a library you `import` inside a UDF, or install in
the notebook with `!pip install`, is **not** available on the executors. Do that work
in the notebook process instead (or ask for the library to be added to the Spark
image, `images/spark/Dockerfile`).

Python code crosses the wire as a pickle and PySpark compares the *minor* version, so
the two images must stay on one Python minor: if they drift apart, every one of those
calls fails with `[PYTHON_VERSION_MISMATCH] Python in worker has different version ...
than that in driver ...` while DataFrame and SQL keep working. Keep both on the same
Python minor: Spark Connect UDFs fail otherwise (platform#369).

## Spark Connect: what is different

The notebook talks to Spark Connect, not to a driver you can reach into: there is no
`sparkContext` and no RDD API, because both live inside the JVM on the other side of the
connection. Coming from classic Spark, the first three calls of a rewrite tend to be the
ones in the left column. `stardelt.spark()` installs a hint on `df.rdd` that says this in
the error itself; the table is the rest of it.

| RDD API (classic Spark) | On Spark Connect |
|---|---|
| `df.rdd`, `df.rdd.getNumPartitions()` | `df.select(spark_partition_id()).distinct().count()` — the number of partitions (`2` for `spark.range(10)`, `4` after `repartition(4)`); `df.rdd` raises `[NOT_IMPLEMENTED]` and names this |
| `rdd.map(...)` | `df.select(...)` / `df.withColumn(...)` for column work, or `df.mapInPandas(...)` when the work is per-partition Python |
| `rdd.foreach(...)` | `df.foreach(...)` (runs your Python on a worker — see below) — or write the result to a table and read it |
| `sparkContext.parallelize(...)` | `spark.createDataFrame(...)` for a local list, `spark.range(...)` for numbers |
| `sparkContext.broadcast(x)` | join the small DataFrame: `F.broadcast(small_df)` — there is no broadcast handle to pass around |
| accumulators | one `groupBy(...).agg(...)` and read the row: an aggregate is the state |
| `sparkContext.setLogLevel(...)` | not available — the log level belongs to the shared driver, not to your session |

Extra libraries are not available on executors either: the workers run exactly the pandas,
pyarrow and numpy versions of this image, so an `import` of anything you installed in the
notebook fails inside a UDF (see "Python on executors" above).

## Keeping the shared driver healthy

Every session `stardelt.spark()` opens runs against the same driver — 1 GiB heap in a
3 GiB pod since platform#372, `spark.driver.maxResultSize` 512 MiB, and no Spark UI.
Patterns that are fine on a laptop fill that driver for everyone, so:

| Instead of | Do | Why |
|---|---|---|
| `df.toPandas()` / `df.collect()` on a big table | `df.limit(10_000).toPandas()`, aggregate first, or write the result with `df.writeTo("<catalog>.<schema>.<table>").createOrReplace()` and read it with SQL | the whole result lands in the 1 GiB driver heap; above 512 MiB the call fails |
| `spark.createDataFrame(big_pandas_or_list)` | write the data to a table once (`writeTo`), then `spark.table(...)` | local data is shipped inside every task of every job |
| a Python loop that runs one job per item | one query with a join or `groupBy`, or `df.repartition(n)` plus `mapInPandas` | every job adds a broadcast and a plan to the driver |
| `df.join(small_df, ...)` repeated in a loop | `from pyspark.sql.functions import broadcast`; `df.join(broadcast(small_df), ...)` once, outside the loop | one broadcast instead of one per iteration |
| `df.cache()` never released | `df.unpersist()` when done; cache only what several actions reuse | cached blocks occupy executor memory (1 GiB each) and are not freed by themselves |
| `pd.read_csv(chunksize=250_000)` then `spark.createDataFrame(chunk)` + `saveAsTable(append)` per chunk | chunks of at most 20_000 rows (about 1 MiB per task), write finished chunk numbers to a small table so a restart can resume, and see platform#378 for the planned loader | measured 2026-10-21: 250_000 rows = tasks of 11 MiB, 305 warnings in 3 h, driver OOMKilled |
| `df.count()` repeated to "check" | run it once, keep the number | each action is a job |

Executors: at most 2 x 1 core x 1 GiB today; sizes are designed in platform#375. If a call
fails with `maxResultSize`, write the result to a table instead of raising the limit.

`stardelt.spark_tips()` prints the seven rows into a notebook, so they are one cell away.

## SQL in notebooks (JupySQL)

`pip install "stardelt[jupyter]"`, then `%load_ext stardelt.sqlmagic` (the platform's notebook
image does this when the kernel starts). It registers the default JupySQL connection
`warehouse`, as the signed-in person:

```sql
%sql select 1
```

```sql
%%sql
select * from warehouse.demo.trips limit 10
```

`%sql --connections` lists the connection; `%sqlcmd tables`, `%sqlcmd columns -t <table>` and
`%sqlplot` are JupySQL's own. Notebook variables bind with `:name`.

The connection stays open for the life of the kernel and sends the current token with every
request, so a long session keeps working while the token is renewed. Loading it runs one
`SELECT version()` (JupySQL asks the engine once; the query log shows it as a notebook
statement). JupySQL's usage statistics are switched off. Outside the platform there is no
Trino to reach: loading warns, it does not fail.

## Publishing a notebook

`stardelt.publish("my.ipynb")` sends the notebook to Nova, signed with the same
token as `sql()`; Nova strips the cell outputs and stores one immutable version
(a schedule pins it). The group defaults to your only group from the token;
when you are in several, pass `group=`. The call prints and returns
`name`, `group`, `version`, `sha256` and the parsed `parameters`.

## Catalog browser

`stardelt.browse()` opens a side panel (or inline fallback) to browse catalogs, schemas, and tables with columns and preview.

## Command line

`pip install stardelt` also installs a `stardelt` command, a thin client for shells, CI and
agents over Nova's public API. It needs a Nova URL and a sign-in:

```bash
export STARDELT_NOVA_URL=https://nova.example.com
stardelt login                                 # once, see below
stardelt whoami
stardelt sql "select 1 as x" --format json     # table (default), json or csv
stardelt sql "select * from warehouse.demo.trips" --max-rows 20
stardelt sql "select * from warehouse.demo.trips" --long      # as a statement: up to 100000 rows, Ctrl-C cancels it
stardelt sql -f report.sql --format csv                       # the statement from a file; -f - reads standard input
stardelt run etl nyc_taxi --wait                                  # start a pipeline run and wait; exit 0 only when it succeeded
```

`stardelt login` signs you in with a code you approve in a browser (the device flow). It asks Nova
(`GET /api/auth/config`, no sign-in needed) which identity provider to use and which public client to sign in with;
`--issuer` (or `STARDELT_ISSUER`) names the provider yourself and then Nova is not asked, `--client-id` (or
`STARDELT_CLIENT_ID`) names another client, and without any of them the client is `stardelt-dev`. It stores the tokens in
`~/.config/stardelt/credentials.json` (mode 0600; `$XDG_CONFIG_HOME` is honoured), renews the access token
when it has less than a minute left, and remembers `--host`/`STARDELT_NOVA_URL` given at login. `STARDELT_TOKEN`
still wins when set (CI). `stardelt logout` deletes the file.

Results go to stdout, messages to stderr. Exit codes: `0` ok, `1` error, `2` wrong usage,
`3` authentication needed. A token is never printed.

Every `public` operation of Nova's contract is reachable by its `operationId`, in the command
line and in Python (the list is `stardelt/operations.json`, refreshed from the contract by
`python scripts/sync_contract.py`):

```bash
stardelt api --list                                   # operationId, method, path
stardelt api pipelines_get -p namespace=etl -p name=nyc_taxi
stardelt api query_log_list -q scope=mine -q state=FAILED -q limit=20 --format table
stardelt api saved_queries_create --body '{"title": "t", "sql": "select 1"}'
```

```python
nova = stardelt.api.Client()
nova.call("table_history", path={"ns": "demo", "tbl": "trips"})
nova.call("pipelines_run_trigger", path={"namespace": "etl", "name": "nyc_taxi"})
```

### Resource commands

The same operations have short names: the first word of an `operationId` is the resource, the rest
is the verb (joined with dashes), and the values of the path follow in the order of the path.
`stardelt --help` lists them; `stardelt pipelines` lists the verbs and the values each one takes.
The options are those of `stardelt api`.

```bash
stardelt pipelines list --format table
stardelt pipelines get etl nyc_taxi                    # = api pipelines_get -p namespace=etl -p name=nyc_taxi
stardelt table history demo trips --format table
```

### Apply a pipeline from a file

`stardelt apply -f pipeline.json` creates the pipeline, or updates it when one of that name exists, so
the same file can run on every deploy. The file is one JSON or YAML object shaped like the body of
`pipelines_create`: `name`, `ownerGroup`, `sql` (or `notebook`), and optionally `schedule`, `params` and
`options`. `-f -` reads stdin. `--dry-run` says what would happen and writes nothing. An update replaces
what the file says (`params` and `options` left out go back to their defaults); the group of an existing
pipeline cannot change. The answer on stdout is `{"action": "created" | "updated" | "would-create" |
"would-update", "name": ...}`.

```bash
cat > pipeline.json <<'JSON'
{"name": "daily", "ownerGroup": "etl", "sql": "select 1", "schedule": {"cron": "0 3 * * *"}}
JSON
stardelt apply -f pipeline.json --dry-run
stardelt apply -f pipeline.json
```

The same pipeline as YAML (`pipeline.yaml`; a value that YAML reads as a date or a number must be quoted if it is
meant as text):

```yaml
name: daily
ownerGroup: etl
sql: select 1
schedule:
  cron: "0 3 * * *"
```

### An MCP server for agents

`stardelt mcp serve` is a local MCP server (stdio) over the same contract: each tool is one public
operation, named by its `operationId`. Only read operations are offered by default; `--allow
query_run` adds one more (SQL, or any write you name) on purpose. Two more tools are guarded SQL, not
operations: `sql_read` runs ONE reading statement (`SELECT`, `WITH`, `VALUES`, `SHOW`, `DESCRIBE`, `TABLE`,
`EXPLAIN`; a write, a second statement or `EXPLAIN ANALYZE` is refused before anything is sent) and returns at
most `maxRows` rows (default 200, up to 1000) with a hint when more exist; a statement that takes longer than
60 seconds is cancelled in the engine. `sql_explain` shows the plan without running it. Both go through the
statement API, so they need a Nova that has it, and `--no-sql` leaves them out. The statement check is only a
first filter: the engine's own permissions for the signed-in person stay the control. It runs as the person whose token
is in `STARDELT_TOKEN`, and what it returns is data, never instructions. `stardelt mcp config` prints
the line that registers it in Claude Code and the `.mcp.json` snippet (the token stays a placeholder):

```bash
claude mcp add --transport stdio --env STARDELT_NOVA_URL=https://nova.example.com \
  --env 'STARDELT_TOKEN=<your bearer token>' stardelt -- stardelt mcp serve
```
