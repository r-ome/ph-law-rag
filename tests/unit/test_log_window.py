import json

from app import log_reader


def _write_lines(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_window_filters_by_timestamp_and_skips_malformed(tmp_path, monkeypatch):
    monkeypatch.setattr(log_reader.settings, "log_dir", str(tmp_path))
    lines = [
        json.dumps({"timestamp": "2026-07-11T00:00:00Z", "level": "info", "event": "before", "logger": "x"}),
        json.dumps({"timestamp": "2026-07-11T02:30:00Z", "level": "info", "event": "inside", "logger": "x"}),
        json.dumps({"timestamp": "2026-07-11T05:00:00Z", "level": "info", "event": "after", "logger": "x"}),
        "{not json",
    ]
    _write_lines(tmp_path / "app.log", lines)

    entries, truncated = log_reader.read_logs_window(
        since="2026-07-11T10:00:00+08:00", until="2026-07-11T11:00:00+08:00",
    )

    assert truncated is False
    assert [e["event"] for e in entries] == ["inside"]


def test_window_truncates_at_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(log_reader.settings, "log_dir", str(tmp_path))
    lines = [
        json.dumps({"timestamp": "2026-07-11T02:00:00Z", "level": "info", "event": "e1", "logger": "x"}),
        json.dumps({"timestamp": "2026-07-11T02:10:00Z", "level": "info", "event": "e2", "logger": "x"}),
    ]
    _write_lines(tmp_path / "app.log", lines)

    entries, truncated = log_reader.read_logs_window(
        since="2026-07-11T00:00:00Z", until="2026-07-11T23:59:59Z", limit=1,
    )

    assert len(entries) == 1
    assert truncated is True
