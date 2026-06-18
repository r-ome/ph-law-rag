import typer
import json
from pathlib import Path
from app.config import settings
from app.db import init_db
app = typer.Typer()

@app.command("healthcheck")
def healthcheck():
	from app.api.health_query import ping_url

	qdrant_ok = ping_url(f"{settings.qdrant_url}/collections")
	ollama_ok = ping_url(f"{settings.ollama_base_url}/api/version")
	typer.echo(json.dumps({
		"status": "ok" if qdrant_ok and ollama_ok else "degraded",
		"qdrant": qdrant_ok,
		"ollama": ollama_ok,
	}, indent=2))

@app.command("sync")
def sync():
	from app.ingestion.sync import run_sync

	result = run_sync()
	typer.echo(f"\nSync complete: {result}")

@app.command("eval-score")
def eval_score(
	run_path: str,
	use_cache: bool = typer.Option(True, "--cache/--no-cache", help="reuse cached RAGAS row scores"),
):
	from app.evals.runner import run_eval_set, load_dataset
	from app.evals.ragas_scorer import score
	from app.evals.report import print_report, save_scored

	results = load_dataset(run_path)
	run_tag = Path(run_path).stem.replace("run_", "")
	scored = score(results, use_cache=use_cache)
	print_report(results, scored)
	save_scored(results, scored, run_tag=run_tag)

@app.command("eval")
def eval(
	use_cache: bool = typer.Option(True, "--cache/--no-cache", help="reuse cached RAGAS row scores"),
):
	from app.evals.runner import run_eval_set
	from app.evals.ragas_scorer import score
	from app.evals.report import print_report, save_scored

	results, raw_path = run_eval_set()
	typer.echo(f"\nRaw results saved to {raw_path}")
	run_tag = raw_path.stem.replace("run_", "")
	scored = score(results, use_cache=use_cache)
	print_report(results, scored)
	save_scored(results, scored, run_tag=run_tag)

eval_cache_app = typer.Typer(help="Manage cached RAGAS row scores")

@eval_cache_app.command("seed")
def eval_cache_seed(run_tag: str):
	from app.evals.ragas_cache import seed_from_artifacts

	typer.echo(json.dumps(seed_from_artifacts(run_tag), indent=2))

@eval_cache_app.command("stats")
def eval_cache_stats():
	from app.evals.ragas_cache import stats

	typer.echo(json.dumps(stats(), indent=2))

@eval_cache_app.command("clear")
def eval_cache_clear():
	from app.evals.ragas_cache import clear

	deleted = clear()
	typer.echo(f"deleted {deleted} cached RAGAS score rows")

app.add_typer(eval_cache_app, name="eval-cache")
 
@app.command("reindex")
def reindex(doc_id: str = typer.Option(None, help="reindex only this doc_id/source_id")):
	from app.indexing.reindex import reindex as run_reindex

	run_reindex(doc_id)

@app.command("eval-diff")
def eval_diff(
	experiment: str,
	baseline: str = typer.Option(None, help="baseline run tag for delta columns"),
	out: str = typer.Option(None, help="markdown output path"),
):
	from app.evals.diff_report import build_diff_report

	path = build_diff_report(experiment, baseline, out)
	typer.echo(f"Diff report written to {path}")

@app.command("retrieve")
def test_retrieve(query: str):
	from app.retriever.retrieve import retrieve

	typer.echo(retrieve(query))

@app.command("ask")
def ask(query: str):
	from app.retriever.answer_service import answer
	result = answer(query)
	typer.echo(result["answer"])
	if result["sources"]:
		typer.echo("\nSources:")
		for s in result["sources"]:
			typer.echo(f"[{s['ref']}] {s['title']} - {s['url']}")

@app.command("show-config")
def show_config():
	typer.echo(json.dumps(settings.model_dump(mode="json"), indent=2))

@app.command("init")
def init():
	for path in [
		settings.raw_data_dir,
		settings.normalized_data_dir,
		"data/qdrant",
		settings.bm25_path,
		Path(settings.db_path).parent,
		settings.eval_results_dir,
	]:
		Path(path).mkdir(parents=True, exist_ok=True)
	init_db()
	typer.echo("Data directories created and database initialized")

if __name__ == "__main__":
	app()
