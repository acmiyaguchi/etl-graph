import json
import logging
from pathlib import Path

import click

from .config import *
from .crawler import fetch_dataset_listing, fetch_table_listing, resolve_view_references
from .utils import ensure_folder, ndjson_load, print_json, qualify, run, run_query

ROOT = Path(__file__).parent.parent


@click.group()
def cli():
    pass


@cli.command()
@click.option(
    "--data-root", type=click.Path(file_okay=False), default=ROOT / "public" / "data"
)
def crawl(data_root):
    """Crawl bigquery projects."""
    run(f"gsutil ls gs://{BUCKET}")
    run(f"bq ls {PROJECT}:{DATASET}")

    data_root = ensure_folder(data_root)
    project = "moz-fx-data-shared-prod"
    dataset_listing = fetch_dataset_listing(project, data_root)
    tables_listing = fetch_table_listing(dataset_listing, data_root / project)
    # tables_listing = ndjson_load(data_root / project / "tables_listing.ndjson")

    views_listing = [row for row in tables_listing if row["table_type"] == "VIEW"]
    resolve_view_references(views_listing, data_root / project)


@cli.command()
@click.option(
    "--data-root", type=click.Path(file_okay=False), default=ROOT / "public" / "data"
)
def query_logs(data_root):
    """Create edgelist from jobs by project query logs."""
    # TODO: modify this so it's generic to a project instead of hardcoded.
    # Problem is that the worker running this command may not have permissions
    # to actually look at INFORMATION_SCHEMA.JOBS_BY_PROJECT
    sql = Path(__file__).parent / "resources" / "shared_prod_edgelist.sql"
    project = "moz-fx-data-shared-prod"
    run_query(
        sql.read_text(),
        dest_table="shared_prod_edgelist",
        output=ensure_folder(data_root) / project,
        project=project,
    )


def _get_name(obj):
    """Assumes structure in views_references.ndjson"""
    return qualify(obj["projectId"], obj["datasetId"], obj["tableId"])


def _deduplicate_edges(edges):
    """Given a list of flat dictionaries, return a deduplicated list."""
    edgeset = set([tuple(d.items()) for d in edges])
    return [dict(tup) for tup in edgeset]


@cli.command()
@click.option(
    "--data-root", type=click.Path(file_okay=False), default=ROOT / "public" / "data"
)
def index(data_root):
    """Combine all of the files together."""
    # currently, only combine view references and query_edgelist
    data_root = ensure_folder(data_root)
    edges = []

    for view_ref in data_root.glob("**/*views_references.ndjson"):
        rows = ndjson_load(view_ref)
        logging.info(
            f"merging {view_ref.relative_to(data_root)} with {len(rows)} views"
        )
        for row in rows:
            # TODO: this needs a schema
            destination = _get_name(row)
            for referenced in row["query"].get("referencedTables", []):
                edges.append(
                    dict(destination=destination, referenced=_get_name(referenced))
                )

    # TODO: hardcoded artifact tied to query_logs command
    for edgelist in data_root.glob("**/shared_prod_edgelist.ndjson"):
        rows = ndjson_load(edgelist)
        logging.info(
            f"merging {edgelist.relative_to(data_root)} with {len(rows)} query references"
        )
        for row in rows:
            edges.append(
                dict(
                    destination=row["destination_table"],
                    referenced=row["referenced_table"],
                )
            )

    edges = _deduplicate_edges(edges)

    # write the file to disk as both csv and json, csv target is gephi compatible
    with (data_root / "edges.json").open("w") as fp:
        json.dump(edges, fp, indent=2)
    logging.info("wrote edges.json")
    with (data_root / "edges.csv").open("w") as fp:
        fp.write("Source,Target\n")
        for edge in edges:
            fp.write(f"{edge['destination']},{edge['referenced']}\n")
    logging.info("wrote edges.csv")


logging.basicConfig(level=logging.DEBUG)
cli()
