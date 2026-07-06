import typer
import json
from pathlib import Path
from app.config import settings
from app.db import init_db
from app.observability.logger import configure_logging

configure_logging()

app = typer.Typer()

@app.command("healthcheck")
def healthcheck():
	from app.runtime.health import ping_url, qdrant_ok

	qdrant_healthy = qdrant_ok()
	uses_ollama = settings.embedding_backend == "ollama" or not settings.llm_model.startswith("claude")
	ollama_ok = ping_url(f"{settings.ollama_base_url}/api/version") if uses_ollama else None
	healthy = qdrant_healthy and (ollama_ok is not False)
	typer.echo(json.dumps({
		"status": "ok" if healthy else "degraded",
		"qdrant": qdrant_healthy,
		"ollama": ollama_ok,
	}, indent=2))

@app.command("sync")
def sync():
	from app.sync_service import run_sync

	result = run_sync()
	typer.echo(f"\nSync complete: {result}")

@app.command("eval-score")
def eval_score(
	run_path: str,
	use_cache: bool = typer.Option(True, "--cache/--no-cache", help="reuse cached RAGAS row scores"),
):
	from app.evals import artifacts
	from app.evals.runner import load_dataset
	from app.evals.ragas_scorer import score
	from app.evals.report import print_report, save_scored

	results = load_dataset(run_path)
	run_tag = artifacts.tag_from_run_path(run_path)
	scored = score(results, use_cache=use_cache)
	print_report(results, scored)
	save_scored(results, scored, run_tag=run_tag)

@app.command("eval")
def eval(
	use_cache: bool = typer.Option(True, "--cache/--no-cache", help="reuse cached RAGAS row scores"),
	do_score: bool = typer.Option(True, "--score/--no-score", help="run the RAGAS judge after generation (--no-score = generation only; review answers, then judge via eval-score)"),
):
	from app.evals.runner import run_eval_set
	from app.evals.report import print_report, save_scored

	results, raw_path, run_tag = run_eval_set()
	typer.echo(f"\nRaw results saved to {raw_path}")
	if not do_score:
		typer.echo(f"Skipped judging (--no-score). Score later with: raglab eval-score {raw_path}")
		return
	from app.evals.ragas_scorer import score

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

logs_app = typer.Typer(help="Manage local log and trace files")

@logs_app.command("prune")
def logs_prune(days: int = typer.Option(30, "--days", min=0, help="keep traces from the last N days")):
	from app.observability.trace import prune_traces

	typer.echo(json.dumps(prune_traces(days), indent=2))

app.add_typer(logs_app, name="logs")
 
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
def ask(query: str, session: str = typer.Option(None, "--session")):
	from app.retriever.answer_service import answer
	from app.conversation.session import session_exists, create_session

	session_id = session
	if session_id and not session_exists(session_id):
		create_session(session_id=session_id)  # create with the given ID
	result = answer(query, session_id=session_id, trace_label="cli")
	typer.echo(result["answer"])
	if result["sources"]:
		typer.echo("\nSources:")
		for s in result["sources"]:
			typer.echo(f"[{s['ref']}] {s['title']} - {s['url']}")

@app.command("timeline")
def timeline(
	fragment: str = typer.Argument(None, help="provision_id or source_id fragment to search"),
	summary: bool = typer.Option(False, "--summary", help="print corpus timeline totals"),
):
	from app.db import get_connection
	from app.indexing.amendment_timeline import build_timelines

	if not summary and not fragment:
		raise typer.BadParameter("provide <fragment> or --summary")

	with get_connection() as conn:
		result = build_timelines(conn)

	if summary:
		total_keys = len(result.timelines)
		multi_entry = sum(1 for t in result.timelines.values() if len(t.entries) >= 2)
		chains = sum(1 for t in result.timelines.values() if len(t.entries) >= 3)
		partial_entries = sum(
			1
			for t in result.timelines.values()
			for entry in t.entries
			if entry.provision_partial
		)
		typer.echo(json.dumps({
			"total_keys": total_keys,
			"keys_with_2_or_more_entries": multi_entry,
			"chains_3_or_more_entries": chains,
			"partial_flagged_entries": partial_entries,
			"ambiguous_insertions": len(result.ambiguous_insertions),
			"same_date_conflicts": len(result.same_date_conflicts),
			"missing_dates": len(result.missing_dates),
		}, indent=2))
		return

	needle = fragment.lower()
	for key in sorted(result.timelines):
		timeline = result.timelines[key]
		source_ids = {entry.source_id for entry in timeline.entries}
		if needle not in key.lower() and not any(needle in s.lower() for s in source_ids):
			continue
		for entry in timeline.entries:
			ratio = "" if entry.length_ratio is None else f"{entry.length_ratio:.3f}"
			labels = ", ".join(entry.unit_labels)
			typer.echo(
				f"{timeline.key}\t{entry.approval_date}\t{entry.source_id}\t"
				f"insertion={entry.is_insertion}\tpartial={entry.provision_partial}\t"
				f"ratio={ratio}\tlabels({len(entry.unit_labels)})={labels}"
			)

@app.command("consolidate-report")
def consolidate_report():
	from app.db import get_connection
	from app.indexing.consolidation import build_splice_plan, plan_report

	with get_connection() as conn:
		report = plan_report(build_splice_plan(conn))
	typer.echo(json.dumps(report, indent=2))

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
		settings.log_dir,
	]:
		Path(path).mkdir(parents=True, exist_ok=True)
	init_db()
	typer.echo("Data directories created and database initialized")

if __name__ == "__main__":
	app()
