import typer
import json
from app.config import settings, SourceConfig
from app.db import init_db
from app.ingestion.sync import run_sync
from app.retriever.retrieve import retrieve
app = typer.Typer()

@app.command("healthcheck")
def healthcheck():
	typer.echo("OK")

@app.command("sync")
def sync():
	run_sync()

@app.command("eval")
def eval():
	typer.echo("Not yet implemented")
 
@app.command("retrieve")
def test_retrieve(query: str):
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
	typer.echo(json.dumps(settings.model_dump(), indent=2))

@app.command("init")
def init():
	init_db()
	typer.echo("Database initialized")

if __name__ == "__main__":
	app()
