import typer
import json
from app.config import settings, SourceConfig
from app.db import init_db
from app.ingestion.sync import run_sync

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

@app.command("ask")
def ask():
	typer.echo("Not yet implemented")

@app.command("show-config")
def show_config():
	typer.echo(json.dumps(settings.model_dump(), indent=2))

@app.command("init")
def init():
	init_db()
	typer.echo("Database initialized")

if __name__ == "__main__":
	app()
