"""Bundles (stardelt.bundle) and ``stardelt bundle validate``: offline, no network, no sign-in."""

import io
import os

from stardelt import bundle, cli

MANIFEST = """
bundle: {name: sales-etl, ownerGroup: sales}
variables:
  catalog: {default: sales_dev}
  unused: {default: x}
include: [resources/*.yaml]
files: [sql/*.sql, notebooks/*.ipynb]
requires: {connections: [crm-db]}
targets:
  dev:  {mode: development, variables: {catalog: sales_dev}}
  prod: {mode: production, variables: {catalog: sales}, runAs: pipeline-sales}
"""
LOAD = "name: load\nownerGroup: sales\nsqlFile: sql/load.sql\nschedule: {cron: '0 3 * * *'}\n"
NIGHTLY = "name: nightly\nsql: select * from ${var.catalog}.demo.trips\n"


def make(tmp_path, manifest=MANIFEST, resources=None, files=None):
    (tmp_path / "bundle.yaml").write_text(manifest)
    (tmp_path / "resources").mkdir(exist_ok=True)
    for name, text in (resources if resources is not None else {"load.yaml": LOAD, "nightly.yaml": NIGHTLY}).items():
        (tmp_path / "resources" / name).write_text(text)
    for name, text in (files if files is not None else {"sql/load.sql": "select 1", "notebooks/explore.ipynb": "{}"}).items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def said(problems):
    return " | ".join(f"{where}: {message}" for where, message in problems)


def test_a_good_bundle_resolves_the_pipelines_of_every_target(tmp_path):
    problems, warnings, resolved = bundle.validate(make(tmp_path))
    assert problems == []
    assert resolved == {"dev": ("development", ["load", "nightly"]), "prod": ("production", ["load", "nightly"])}
    assert warnings == ["variable 'unused' is declared but no pipeline file uses it"]


def test_the_command_prints_one_line_per_target_without_a_host_or_a_sign_in(tmp_path):
    make(tmp_path)
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["bundle", "validate", str(tmp_path), "-t", "prod"], environ={}, stdout=out, stderr=err)
    assert (code, out.getvalue()) == (0, "prod (production): load, nightly\n")
    assert "warning" in err.getvalue()


def test_the_command_exits_2_with_one_sentence_per_problem(tmp_path):
    make(tmp_path, manifest=MANIFEST.replace("sales-etl", "X"))
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["bundle", "validate", str(tmp_path)], environ={}, stdout=out, stderr=err)
    assert (code, out.getvalue()) == (2, "")
    assert "stardelt: bundle.name: must be 3 to 40 characters" in err.getvalue()


def test_unknown_keys_and_a_missing_manifest_are_problems(tmp_path):
    assert "no bundle.yaml here" in said(bundle.validate(tmp_path)[0])
    make(tmp_path, manifest=MANIFEST + "colour: red\n")
    assert "unknown key 'colour'" in said(bundle.validate(tmp_path)[0])


def test_a_variable_must_be_declared_and_have_a_value_in_every_target(tmp_path):
    manifest = MANIFEST.replace("  catalog: {default: sales_dev}\n", "  catalog: {}\n").replace("dev:  {mode: development, variables: {catalog: sales_dev}}", "dev:  {mode: development}")
    problems = said(bundle.validate(make(tmp_path, manifest=manifest))[0])
    assert "nightly.yaml (dev): ${var.catalog} has no value" in problems
    manifest = MANIFEST.replace("variables: {catalog: sales}, runAs", "variables: {catalog: sales, other: 1}, runAs")
    assert "variable 'other' is not declared" in said(bundle.validate(make(tmp_path, manifest=manifest))[0])


def test_a_production_target_needs_a_run_as_principal_of_the_owner_group(tmp_path):
    for run_as in ("", ", runAs: pipeline-other", ", runAs: someone"):
        manifest = MANIFEST.replace(", runAs: pipeline-sales", run_as)
        assert "a production target needs runAs: pipeline-sales" in said(bundle.validate(make(tmp_path, manifest=manifest))[0]), run_as
    manifest = MANIFEST.replace("runAs: pipeline-sales", "runAs: pipeline-sales-prod")
    assert bundle.validate(make(tmp_path, manifest=manifest))[0] == []


def test_every_pipeline_file_must_be_a_pipeline_with_one_source_inside_the_bundle(tmp_path):
    resources = {"a.yaml": "sql: select 1\n", "b.yaml": "name: b\nsql: select 1\nsqlFile: sql/load.sql\n", "c.yaml": "name: c\nsqlFile: sql/missing.sql\n",
                 "d.yaml": "name: d\nnotebook: {file: notebooks/none.ipynb}\n", "e.yaml": "name: e\nownerGroup: other\nsql: select 1\n"}
    problems = said(bundle.validate(make(tmp_path, resources=resources), "dev")[0])
    assert "a.yaml (dev): the file must be one JSON or YAML object with a \"name\"" in problems
    assert "b.yaml (dev): give exactly one of sql, sqlFile or notebook" in problems
    assert "c.yaml (dev): sqlFile 'sql/missing.sql' is not among the bundle's files" in problems
    assert "d.yaml (dev): notebook file 'notebooks/none.ipynb' is not among the bundle's files" in problems
    assert "e.yaml (dev): ownerGroup 'other' is not the bundle's group 'sales'" in problems


def test_two_files_with_one_pipeline_name_and_a_pattern_that_matches_nothing_are_problems(tmp_path):
    problems = said(bundle.validate(make(tmp_path, resources={"a.yaml": NIGHTLY, "b.yaml": NIGHTLY}), "dev")[0])
    assert "two pipeline files define 'nightly'" in problems
    problems = said(bundle.validate(make(tmp_path, manifest=MANIFEST.replace("notebooks/*.ipynb", "notebooks/*.nope")))[0])
    assert "notebooks/*.nope: matches no file" in problems


def test_paths_must_stay_inside_the_bundle_and_links_and_big_files_are_refused(tmp_path):
    problems = said(bundle.validate(make(tmp_path, manifest=MANIFEST.replace("sql/*.sql", "../*.sql")))[0])
    assert "a pattern must stay inside the bundle" in problems
    make(tmp_path)
    outside = tmp_path.parent / "outside.sql"
    outside.write_text("select 2")
    os.symlink(outside, tmp_path / "sql" / "link.sql")
    assert "sql/link.sql: a symbolic link is not allowed" in said(bundle.validate(tmp_path)[0])
    os.unlink(tmp_path / "sql" / "link.sql")
    (tmp_path / "sql" / "big.sql").write_bytes(b"x" * (bundle.MAX_BYTES + 1))
    assert "sql/big.sql: larger than 5 MiB" in said(bundle.validate(tmp_path)[0])


def test_a_target_filter_and_an_unknown_target(tmp_path):
    make(tmp_path)
    problems, _, resolved = bundle.validate(tmp_path, "dev")
    assert problems == [] and list(resolved) == ["dev"]
    assert "no target named 'nope' (targets: dev, prod)" in said(bundle.validate(tmp_path, "nope")[0])


def test_init_writes_a_bundle_that_validates(tmp_path):
    written = bundle.init(tmp_path / "shop", "shop-etl", "shop")
    assert written == ["bundle.yaml", "resources/daily.yaml", "sql/daily.sql"]
    problems, warnings, resolved = bundle.validate(tmp_path / "shop")
    assert (problems, warnings) == ([], [])
    assert resolved == {"dev": ("development", ["daily"]), "prod": ("production", ["daily"])}
    assert "runAs: pipeline-shop" in (tmp_path / "shop" / "bundle.yaml").read_text()


def test_init_refuses_a_bad_name_a_bad_group_and_an_existing_bundle_and_writes_nothing(tmp_path):
    for name, group, said in (("X", "shop", "the name must be"), ("shop-etl", "", "the group must be")):
        try:
            bundle.init(tmp_path, name, group)
        except ValueError as err:
            assert said in str(err)
        else:
            raise AssertionError("expected a ValueError")
    assert list(tmp_path.iterdir()) == []
    bundle.init(tmp_path, "shop-etl", "shop")
    try:
        bundle.init(tmp_path, "shop-etl", "shop")
    except ValueError as err:
        assert "already exists" in str(err)
    else:
        raise AssertionError("expected a ValueError")


def test_the_init_command_says_what_it_wrote_and_exits_2_on_a_mistake(tmp_path):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["bundle", "init", str(tmp_path / "b"), "--name", "shop-etl", "--group", "shop"], environ={}, stdout=out, stderr=err)
    assert (code, out.getvalue()) == (0, "") and "wrote bundle.yaml, resources/daily.yaml, sql/daily.sql" in err.getvalue()
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["bundle", "init", str(tmp_path / "b"), "--name", "shop-etl", "--group", "shop"], environ={}, stdout=out, stderr=err)
    assert code == 2 and "already exists" in err.getvalue()
