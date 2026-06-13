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
def eval_score(run_path: str):
	from app.evals.runner import run_eval_set, load_dataset
	from app.evals.ragas_scorer import score
	from app.evals.report import print_report, save_scored

	results = load_dataset(run_path)
	run_tag = Path(run_path).stem.replace("run_", "")
	scored = score(results)
	print_report(results, scored)
	save_scored(results, scored, run_tag=run_tag)

@app.command("eval")
def eval():
	from app.evals.runner import run_eval_set
	from app.evals.ragas_scorer import score
	from app.evals.report import print_report, save_scored

	results, raw_path = run_eval_set()
	typer.echo(f"\nRaw results saved to {raw_path}")
	run_tag = raw_path.stem.replace("run_", "")
	scored = score(results)
	print_report(results, scored)
	save_scored(results, scored, run_tag=run_tag)
 
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
