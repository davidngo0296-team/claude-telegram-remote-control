# tests/test_approve.py
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from approve import build_allow_rule, write_allow_rule


# ── build_allow_rule ────────────────────────────────────────────────────────

def test_bash_uses_first_word_with_wildcard():
    assert build_allow_rule("Bash", {"command": "git status"}) == "Bash(git *)"

def test_bash_multi_word_takes_only_first():
    assert build_allow_rule("Bash", {"command": "npm run build"}) == "Bash(npm *)"

def test_bash_single_word_command():
    assert build_allow_rule("Bash", {"command": "ls"}) == "Bash(ls *)"

def test_bash_empty_command_returns_bare_bash():
    assert build_allow_rule("Bash", {"command": ""}) == "Bash"

def test_bash_missing_command_key_returns_bare_bash():
    assert build_allow_rule("Bash", {}) == "Bash"

def test_edit_returns_edit():
    assert build_allow_rule("Edit", {"file_path": "foo.py"}) == "Edit"

def test_write_returns_edit():
    assert build_allow_rule("Write", {"file_path": "foo.py"}) == "Edit"

def test_notebook_edit_returns_edit():
    assert build_allow_rule("NotebookEdit", {"notebook_path": "nb.ipynb"}) == "Edit"

def test_read_returns_read():
    assert build_allow_rule("Read", {"file_path": "foo.py"}) == "Read"

def test_unknown_tool_returns_tool_name():
    assert build_allow_rule("WebFetch", {"url": "https://example.com"}) == "WebFetch"


# ── write_allow_rule ────────────────────────────────────────────────────────

def test_creates_settings_file_and_directory_if_missing(tmp_path):
    write_allow_rule(str(tmp_path), "Bash(git *)")
    settings_path = tmp_path / ".claude" / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert "Bash(git *)" in data["permissions"]["allow"]

def test_appends_to_existing_allow_list(tmp_path):
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    settings_path = settings_dir / "settings.json"
    settings_path.write_text(json.dumps({"permissions": {"allow": ["Read"]}}))
    write_allow_rule(str(tmp_path), "Bash(git *)")
    data = json.loads(settings_path.read_text())
    assert "Read" in data["permissions"]["allow"]
    assert "Bash(git *)" in data["permissions"]["allow"]

def test_does_not_duplicate_existing_rule(tmp_path):
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    settings_path = settings_dir / "settings.json"
    settings_path.write_text(json.dumps({"permissions": {"allow": ["Bash(git *)"]}}))
    write_allow_rule(str(tmp_path), "Bash(git *)")
    data = json.loads(settings_path.read_text())
    assert data["permissions"]["allow"].count("Bash(git *)") == 1

def test_preserves_existing_settings_keys(tmp_path):
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    settings_path = settings_dir / "settings.json"
    settings_path.write_text(json.dumps({"model": "claude-opus-4-6", "permissions": {"deny": ["Bash(rm *)"]}}))
    write_allow_rule(str(tmp_path), "Edit")
    data = json.loads(settings_path.read_text())
    assert data["model"] == "claude-opus-4-6"
    assert "Bash(rm *)" in data["permissions"]["deny"]
    assert "Edit" in data["permissions"]["allow"]

def test_falls_back_to_home_if_cwd_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    write_allow_rule("", "Edit")
    settings_path = tmp_path / ".claude" / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert "Edit" in data["permissions"]["allow"]

def test_falls_back_to_home_if_cwd_nonexistent(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    write_allow_rule("/nonexistent/path/xyz", "Read")
    settings_path = tmp_path / ".claude" / "settings.json"
    assert settings_path.exists()
