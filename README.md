# stardelt — the notebook library

The helper that sits between a notebook and the platform's warehouse:

```python
import stardelt

stardelt.sql("select count(*) from warehouse.covid.covid_summary")
stardelt.write_df(df, "bronze.raw.events")
stardelt.publish("my.ipynb")   # a versioned copy of this notebook, for scheduling
session = stardelt.spark()
stardelt.spark_tips()          # how to keep the shared driver healthy
```

The API reference is in [`stardelt/README.md`](stardelt/README.md) — it ships with the
package, so `help(stardelt.spark)` points at the same text.

## What it talks to

| | version | why it matters |
|---|---|---|
| Spark Connect **client** (`pyspark[connect]`) | same Spark line as the server: `4.1.x` | a newer client mis-parses the server's settings (`3221225472b`) and every `createDataFrame` fails with an opaque `int()` error |
| Trino (the `trino` client) | 0.340 | the `sql()` and `write_df()` paths |
| pandas | 2.2.x | the frames the notebook hands over |

`stardelt.spark()` refuses to work when the client and the server are on different Spark
lines, and says so in one sentence naming both versions.

## Install

The platform's notebook image installs a **pinned** release of this library; a plain
notebook can install the same pin:

```bash
pip install "stardelt @ git+https://github.com/stardelt/stardelt-python@v0.2.0"
```

## Container image

`ghcr.io/stardelt/stardelt-cli:dev` carries the `stardelt` command, and nothing else, for
jobs that run outside a notebook (the platform's table-maintenance CronJob):

```bash
docker run --rm ghcr.io/stardelt/stardelt-cli:dev maintenance run --help
```

## On a laptop

A local kernel (VS Code, Jupyter) reaches the warehouse through Nova's statement API with
the sign-in you store once — no workspace needed:

```bash
stardelt login --host https://<nova>
```

```python
import stardelt

stardelt.sql("SELECT * FROM warehouse.demo.trips LIMIT 10")   # up to 100 000 rows
```

`stardelt.spark()` and `stardelt.write_df()` need the platform: connect VS Code to your
workspace (Nova → Profile → VS Code).

## Releases

Semantic versions, tagged (`v0.2.0`). The package is not on PyPI: install a tag from Git, as above. `v0.2.0` is the first release that contains the `stardelt` command line (`stardelt login`, `stardelt bundle ...`). The platform pins a **release**, never a branch: a
moving tag is not an upgrade path. A release states the platform versions it was tested
against (the table above).

## Development

```bash
pip install -e ".[test]"
python -m pytest tests -q
```

The tests need no cluster: the Spark and Trino surfaces are faked.
