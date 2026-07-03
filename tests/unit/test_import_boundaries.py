import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> set[str]:
	tree = ast.parse(path.read_text(), filename=str(path))
	found: set[str] = set()
	for node in ast.walk(tree):
		if isinstance(node, ast.Import):
			for alias in node.names:
				found.add(alias.name)
		elif isinstance(node, ast.ImportFrom) and node.module:
			found.add(node.module)
			for alias in node.names:
				found.add(f"{node.module}.{alias.name}")
	return found


def _matches(module: str, prefix: str) -> bool:
	return module == prefix or module.startswith(prefix + ".")


def _python_files(*parts: str) -> list[Path]:
	return [
		path
		for path in (ROOT / Path(*parts)).rglob("*.py")
		if "__pycache__" not in path.parts
	]


def test_ingestion_does_not_import_downstream_layers():
	for path in _python_files("app", "ingestion"):
		for module in _imports(path):
			assert not any(
				_matches(module, prefix)
				for prefix in ("app.indexing", "app.retriever", "app.evals", "app.api", "app.ui")
			), f"{path.relative_to(ROOT)} imports forbidden module {module}"


def test_indexing_does_not_import_adapters_or_ingestion_sync():
	for path in _python_files("app", "indexing"):
		for module in _imports(path):
			assert not any(
				_matches(module, prefix)
				for prefix in ("app.api", "app.ui", "app.ingestion.sync")
			), f"{path.relative_to(ROOT)} imports forbidden module {module}"


def test_api_and_ui_import_only_allowed_app_prefixes():
	allowed = (
		"app.api",
		"app.ui",
		"app.config",
		"app.sync_service",
		"app.retriever.answer_service",
		"app.retriever.retrieve",
		"app.runtime.health",
		"app.db",
		"app.conversation.session",
	)
	for folder in ("api", "ui"):
		for path in _python_files("app", folder):
			for module in _imports(path):
				if not _matches(module, "app"):
					continue
				assert any(_matches(module, prefix) for prefix in allowed), (
					f"{path.relative_to(ROOT)} imports forbidden module {module}"
				)
