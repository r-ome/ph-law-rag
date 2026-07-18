import typer
import json
from pathlib import Path
from app.config import settings
from app.db import init_db
from app.observability.logger import configure_logging

app = typer.Typer()

@app.callback()
def _main() -> None:
	# Configure at invocation, not import: importing this module must stay
	# side-effect free (a global structlog reconfigure breaks capsys-based tests
	# and any embedder of the CLI module).
	configure_logging()

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
	paths = artifacts.paths_for_tag(run_tag)
	if not paths.retrieval_summary.exists():
		from app.evals.retrieval_metrics import rebuild_for_tag

		if rebuild_for_tag(run_tag) is None:
			typer.echo("Retrieval metrics unavailable: this historical run has no candidate trace.")
	scored = score(results, use_cache=use_cache)
	print_report(results, scored)
	save_scored(results, scored, run_tag=run_tag)
	if any(row.get("split") == "holdout" for row in results):
		from app.evals.holdout_ledger import log_holdout_aggregate_read

		log_holdout_aggregate_read(
			access_type="score_run",
			tags=[run_tag],
			purpose=settings.eval_run_label or None,
			source="cli.eval-score",
		)

@app.command("eval")
def eval(
	splits: list[str] = typer.Option(["regression", "dev"], "--split", help="dataset split to run; repeatable"),
	holdout_release_run: bool = typer.Option(False, "--holdout-release-run", help="required acknowledgement before running holdout"),
	use_cache: bool = typer.Option(True, "--cache/--no-cache", help="reuse cached RAGAS row scores"),
	do_score: bool = typer.Option(True, "--score/--no-score", help="run the RAGAS judge after generation (--no-score = generation only; review answers, then judge via eval-score)"),
):
	from app.evals.runner import run_eval_set
	from app.evals.report import print_report, save_scored

	if "holdout" in splits and not holdout_release_run:
		raise typer.BadParameter(
			"holdout is release-only; add --holdout-release-run to acknowledge aggregate-only reporting",
			param_hint="--holdout-release-run",
		)
	results, raw_path, run_tag = run_eval_set(tuple(splits))
	typer.echo(f"\nRaw results saved to {raw_path}")
	if "holdout" in splits:
		from app.evals.holdout_ledger import log_holdout_aggregate_read

		log_holdout_aggregate_read(
			access_type="release_run",
			tags=[run_tag],
			purpose=settings.eval_run_label or None,
			source="cli.eval",
		)
	if not do_score:
		typer.echo(f"Skipped judging (--no-score). Score later with: raglab eval-score {raw_path}")
		return
	from app.evals.ragas_scorer import score

	scored = score(results, use_cache=use_cache)
	print_report(results, scored)
	save_scored(results, scored, run_tag=run_tag)

@app.command("eval-phase4-paired")
def eval_phase4_paired(
	baseline_tag: str,
	candidate_tag: str,
	tag: str = typer.Option(None, "--tag"),
	use_cache: bool = typer.Option(True, "--cache/--no-cache", help="reuse cached RAGAS row scores"),
):
	"""Aggregate-only paired Phase 4 comparison for dev or holdout live runs."""
	from app.evals.paired_aggregate import build_paired_aggregate, printable_summary

	try:
		artifact = build_paired_aggregate(
			baseline_tag,
			candidate_tag,
			tag=tag,
			use_cache=use_cache,
		)
	except (ValueError, FileExistsError, AssertionError) as exc:
		raise typer.BadParameter(str(exc)) from exc
	typer.echo(json.dumps(printable_summary(artifact), indent=2, ensure_ascii=False))

@app.command("eval-phase4-cp-a0")
def eval_phase4_cp_a0(
	frozen_tag: str = typer.Option("phase3-sibling-aware-minilm", "--frozen-tag"),
	record: bool = typer.Option(False, "--record/--no-record", help="append the aggregate probe result to docs/retrieval_strategy_review.md"),
):
	"""Run the locked non-holdout Phase 4 live-retrieval reproducibility probe."""
	from app.evals.phase4_validation import append_cp_a0_result_to_review, run_cp_a0_probe

	result = run_cp_a0_probe(frozen_tag=frozen_tag)
	if record:
		append_cp_a0_result_to_review(
			result,
			path="docs/retrieval_strategy_review.md",
		)
	typer.echo(json.dumps(result, indent=2, ensure_ascii=False))

@app.command("eval-phase4-cp-a2c")
def eval_phase4_cp_a2c(
	frozen_tag: str = typer.Option("phase4-adaptive-context-v2-minilm", "--frozen-tag"),
):
	"""Run non-holdout live-on CP-A2.c at selector-semantic granularity."""
	from app.evals.phase4_validation import run_cp_a2c_semantic_probe

	result = run_cp_a2c_semantic_probe(frozen_tag=frozen_tag)
	typer.echo(json.dumps(result, indent=2, ensure_ascii=False))

@app.command("eval-phase4-holdout")
def eval_phase4_holdout(
	tag: str = typer.Option(..., "--tag", help="label prefix for the paired holdout run"),
	holdout_release_run: bool = typer.Option(False, "--holdout-release-run", help="required acknowledgement before running holdout"),
	use_cache: bool = typer.Option(True, "--cache/--no-cache", help="reuse cached RAGAS row scores"),
):
	"""Run the single-retrieval-pass Phase 4 holdout A/B and emit one aggregate verdict."""
	if not holdout_release_run:
		raise typer.BadParameter(
			"holdout is release-only; add --holdout-release-run to acknowledge aggregate-only reporting",
			param_hint="--holdout-release-run",
		)
	from app.evals.paired_aggregate import printable_summary
	from app.evals.phase4_single_pass import run_phase4_single_pass

	try:
		artifact = run_phase4_single_pass(
			tag=tag,
			splits=("holdout",),
			use_cache=use_cache,
		)
	except (ValueError, FileExistsError, AssertionError) as exc:
		raise typer.BadParameter(str(exc)) from exc
	typer.echo(json.dumps(printable_summary(artifact), indent=2, ensure_ascii=False))

@app.command("eval-phase4-single-pass")
def eval_phase4_single_pass(
	tag: str = typer.Option(..., "--tag"),
	splits: list[str] = typer.Option(["regression", "dev"], "--split", help="dataset split; repeatable"),
	holdout_release_run: bool = typer.Option(False, "--holdout-release-run", help="required acknowledgement before running holdout"),
	use_cache: bool = typer.Option(True, "--cache/--no-cache", help="reuse cached RAGAS row scores"),
):
	"""Run Phase 4 paired A/B from one retrieval pass per row."""
	if "holdout" in splits and not holdout_release_run:
		raise typer.BadParameter(
			"holdout is release-only; add --holdout-release-run to acknowledge aggregate-only reporting",
			param_hint="--holdout-release-run",
		)
	from app.evals.paired_aggregate import printable_summary
	from app.evals.phase4_single_pass import run_phase4_single_pass

	try:
		artifact = run_phase4_single_pass(
			tag=tag,
			splits=tuple(splits),
			use_cache=use_cache,
		)
	except (ValueError, FileExistsError, AssertionError) as exc:
		raise typer.BadParameter(str(exc)) from exc
	typer.echo(json.dumps(printable_summary(artifact), indent=2, ensure_ascii=False))

@app.command("eval-retrieve")
def eval_retrieve(
	splits: list[str] = typer.Option(["regression", "dev"], "--split", help="dataset split; repeatable"),
	row_ids: list[str] = typer.Option([], "--row-id", help="eval row ID; repeatable"),
	tag: str = typer.Option(..., "--tag"),
	resume: bool = typer.Option(False, "--resume"),
	keep_retrieval_models: bool = typer.Option(False, "--keep-retrieval-models", help="diagnostics only"),
	legal_query_separation: bool = typer.Option(
		False,
		"--legal-query-separation/--original-only",
	),
	strategy: str = typer.Option(None, "--strategy", help="explicit retrieval strategy override"),
):
	if "holdout" in splits:
		raise typer.BadParameter("holdout is sealed and unavailable to eval-retrieve")
	from app.evals.dataset import load_eval_dataset
	from app.evals.retrieval_runner import retrieve_rows
	rows = load_eval_dataset(settings.eval_dataset_path, splits=tuple(splits), row_ids=row_ids or None)
	query_separation_arm = (
		"original_plus_rewrite" if legal_query_separation else "original_only"
	)
	typer.echo(
		"Retrieval bundle written to "
		f"{retrieve_rows(rows, tag=tag, resume=resume, keep_retrieval_models=keep_retrieval_models, query_separation_arm=query_separation_arm, strategy_override=strategy)}"
	)

@app.command("eval-generate")
def eval_generate(
	retrieval_tag: str,
	tag: str = typer.Option(..., "--tag"),
	generator_model: str = typer.Option(None, "--generator-model"),
	resume: bool = typer.Option(False, "--resume"),
):
	from app.evals.generation_replay import generate_bundle
	typer.echo(f"Generation bundle written to {generate_bundle(retrieval_tag, tag=tag, model_override=generator_model, resume=resume)}")

@app.command("eval-retrieval-compare")
def eval_retrieval_compare(
	baseline_tag: str,
	candidate_tag: str,
	tag: str = typer.Option(..., "--tag"),
	expected_baseline_arm: str = typer.Option(
		"original_only", "--expected-baseline-arm"
	),
	expected_candidate_arm: str = typer.Option(
		"original_plus_rewrite", "--expected-candidate-arm"
	),
	expected_knob_diff: list[str] = typer.Option(
		[],
		"--expected-knob-diff",
		help="repeatable declaration: name=[baseline,candidate]",
	),
):
	from app.evals.retrieval_comparison import compare_retrieval_bundles

	parsed_knob_diff = {}
	for declaration in expected_knob_diff:
		name, separator, payload = declaration.partition("=")
		if not separator or not name:
			raise typer.BadParameter(
				"expected knob diff must use name=[baseline,candidate]",
				param_hint="--expected-knob-diff",
			)
		if name in parsed_knob_diff:
			raise typer.BadParameter(
				f"duplicate expected knob diff for {name!r}",
				param_hint="--expected-knob-diff",
			)
		try:
			endpoints = json.loads(payload)
		except json.JSONDecodeError as exc:
			raise typer.BadParameter(
				f"invalid JSON endpoints for {name!r}: {exc.msg}",
				param_hint="--expected-knob-diff",
			) from exc
		if not isinstance(endpoints, list) or len(endpoints) != 2:
			raise typer.BadParameter(
				f"expected knob diff for {name!r} must be a two-item JSON list",
				param_hint="--expected-knob-diff",
			)
		parsed_knob_diff[name] = (endpoints[0], endpoints[1])

	try:
		path = compare_retrieval_bundles(
			baseline_tag,
			candidate_tag,
			tag=tag,
			expected_arm_pair=(expected_baseline_arm, expected_candidate_arm),
			expected_knob_diff=parsed_knob_diff or None,
		)
	except ValueError as exc:
		raise typer.BadParameter(str(exc)) from exc
	typer.echo(f"Retrieval comparison written to {path}")

@app.command("eval-context-replay")
def eval_context_replay(
	source_tag: str,
	tag: str = typer.Option(..., "--tag"),
	selector: str = typer.Option("adaptive", "--selector", help="fixed or adaptive"),
):
	"""Replay final-context packaging from a sealed non-holdout bundle."""
	if selector not in {"fixed", "adaptive"}:
		raise typer.BadParameter(
			"selector must be fixed or adaptive", param_hint="--selector"
		)
	from app.evals.context_selection_replay import replay_context_selection

	try:
		path = replay_context_selection(source_tag, tag=tag, selector=selector)
	except (ValueError, FileExistsError) as exc:
		raise typer.BadParameter(str(exc)) from exc
	typer.echo(f"Context-selection bundle written to {path}")

@app.command("eval-facet-audit")
def eval_facet_audit(
	bundle_tag: str = typer.Argument(
		"phase4-adaptive-context-v2-minilm",
		help="sealed non-holdout retrieval bundle tag (Phase 5 CP1 input)",
	),
	tag: str = typer.Option(..., "--tag", help="output artifact tag"),
	authorize_paid_calls: bool = typer.Option(
		False,
		"--authorize-paid-calls",
		help="allow real Haiku calls for cache misses; default mode is zero-network",
	),
):
	"""Phase 5 CP1: offline CRAG facet-checker audit over a sealed bundle."""
	from app.evals.facet_audit import run_facet_audit

	try:
		result = run_facet_audit(
			bundle_tag, output_tag=tag, authorize_paid_calls=authorize_paid_calls
		)
	except (ValueError, FileExistsError, PermissionError, FileNotFoundError) as exc:
		raise typer.BadParameter(str(exc)) from exc
	typer.echo(json.dumps(result, indent=2, ensure_ascii=False))

@app.command("eval-sibling-census")
def eval_sibling_census(
	trace_path: str,
	radius: int = typer.Option(1, "--radius", min=1),
	db_path: str = typer.Option(None, "--db-path"),
):
	"""Inspect radius-eligible exact-leaf misses without running retrieval."""
	from app.evals.sibling_census import build_sibling_eligibility_census

	payload = build_sibling_eligibility_census(
		trace_path,
		db_path=db_path or settings.db_path,
		radius=radius,
	)
	typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))

@app.command("eval-repeatability")
def eval_repeatability(
	run_path: str,
	repeats: int = typer.Option(5, "--repeats", min=2, help="number of uncached judge passes"),
	row_ids: list[str] = typer.Option([], "--row-id", help="eval row ID to include; repeatable"),
	sample_size: int = typer.Option(10, "--sample-size", min=1, help="deterministic panel size when --row-id is omitted"),
	out: str = typer.Option(None, help="JSON artifact output path"),
):
	from app.evals.repeatability import CAVEAT, run_repeatability_panel

	payload, out_path = run_repeatability_panel(
		run_path,
		repeats=repeats,
		row_ids=row_ids,
		sample_size=sample_size,
		out=out,
	)
	typer.echo(
		f"Judge repeatability panel - rows: {payload['row_count']}, "
		f"repeats: {payload['repeats']}"
	)
	for metric, stats in payload["metrics"].items():
		typer.echo(f"\n{metric}")
		typer.echo(f" median within-row range: {stats['median_within_row_range']}")
		typer.echo(f" 90th-percentile range:  {stats['p90_within_row_range']}")
		typer.echo(f" maximum observed range: {stats['max_within_row_range']}")
		if stats["nan_count"]:
			typer.echo(f" NaN scores ignored:      {stats['nan_count']}")
	typer.echo(f"\nCaveat: {CAVEAT}")
	typer.echo(f"Repeatability artifact written to {out_path}")

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
	from app.pipeline.policy import resolve_policy

	resolution = resolve_policy()
	typer.echo(json.dumps({
		**settings.model_dump(mode="json"),
		"profile": resolution.policy.name,
		"policy_overrides": resolution.policy_overrides,
		"env_ignored": resolution.env_ignored,
	}, indent=2))

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
