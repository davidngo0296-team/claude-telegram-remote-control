# tests/test_run_claude.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from run_claude import _format_input_snippet, _truncate_result, _render_activity


# --- _format_input_snippet ---

def test_bash_extracts_command():
    assert _format_input_snippet("Bash", {"command": "ls -la"}) == "ls -la"

def test_read_extracts_file_path():
    assert _format_input_snippet("Read", {"file_path": "src/main.py"}) == "src/main.py"

def test_edit_extracts_file_path():
    assert _format_input_snippet("Edit", {"file_path": "config.json"}) == "config.json"

def test_write_extracts_file_path():
    assert _format_input_snippet("Write", {"file_path": "output.txt"}) == "output.txt"

def test_unknown_tool_serializes_json():
    result = _format_input_snippet("Glob", {"pattern": "**/*.py"})
    assert "**/*.py" in result

def test_long_input_truncated_to_60():
    long_cmd = "x" * 80
    result = _format_input_snippet("Bash", {"command": long_cmd})
    assert len(result) <= 60
    assert result.endswith("...")


# --- _truncate_result ---

def test_short_result_unchanged():
    assert _truncate_result("line1\nline2") == "line1\nline2"

def test_long_result_truncated_to_5_lines():
    text = "\n".join(f"line{i}" for i in range(10))
    result = _truncate_result(text)
    lines = result.splitlines()
    assert lines[-1] == "…"
    assert len(lines) == 6  # 5 content + ellipsis

def test_list_content_joined():
    content = [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]
    result = _truncate_result(content)
    assert "hello" in result
    assert "world" in result

def test_empty_result_returns_empty():
    assert _truncate_result("") == ""
    assert _truncate_result(None) == ""


# --- _render_activity ---

def test_render_shows_working_header():
    result = _render_activity([])
    assert "Working" in result

def test_render_running_tool_shows_hourglass():
    calls = [{"name": "Bash", "snippet": "ls", "result_lines": "", "done": False}]
    result = _render_activity(calls)
    assert "⏳" in result
    assert "ls" in result

def test_render_done_tool_shows_checkmark():
    calls = [{"name": "Bash", "snippet": "ls", "result_lines": "file.py", "done": True}]
    result = _render_activity(calls)
    assert "✓" in result

def test_render_running_tool_shows_name_and_snippet():
    calls = [{"name": "Read", "snippet": "main.py", "result_lines": "", "done": False}]
    result = _render_activity(calls)
    assert "Read" in result
    assert "main.py" in result

def test_render_done_tool_no_result_shows_plain_checkmark():
    calls = [{"name": "Bash", "snippet": "ls", "result_lines": "", "done": True}]
    result = _render_activity(calls)
    assert "✓" in result
    assert "```" not in result
