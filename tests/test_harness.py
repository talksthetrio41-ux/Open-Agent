import os
from pathlib import Path

from open_agent.harness import AgentHarness
from open_agent.prompts import COMPACT_PROMPT, SYSTEM_PROMPT


def test_extract_standard_bash():
    h = AgentHarness(sandbox_dir="/tmp/oa-test-sandbox")
    text = "Thought: list files\n```bash\nls -la\n```\n"
    assert h.extract_bash_command(text) == "ls -la"


def test_extract_strips_line_numbers_and_nbsp():
    h = AgentHarness(sandbox_dir="/tmp/oa-test-sandbox")
    text = "bash\n1\n2\nls\xa0-la\n"
    cmd = h.extract_bash_command(text)
    assert cmd is not None
    assert "ls -la" in cmd
    assert "1" not in cmd.split()


def test_execute_command_in_sandbox(tmp_path):
    h = AgentHarness(sandbox_dir=str(tmp_path))
    result = h.execute_command("echo hello-open-agent && pwd")
    assert result.ok
    assert "hello-open-agent" in result.output
    assert str(tmp_path) in result.output


def test_github_write_and_read(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("GITHUB_REPO", "")
    h = AgentHarness(sandbox_dir=str(tmp_path))
    h.fs.workspace = tmp_path
    text = "```github write notes/hello.txt\nhello from test\n```"
    result = h.run_tool(text)
    assert result is not None
    assert result.kind == "github"
    assert result.ok
    assert (tmp_path / "notes" / "hello.txt").read_text(encoding="utf-8").startswith("hello from test")
    read = h.run_tool("```github read notes/hello.txt\n```")
    assert "hello from test" in read.output


def test_github_ls_and_delete(tmp_path):
    h = AgentHarness(sandbox_dir=str(tmp_path))
    h.fs.workspace = tmp_path
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    listed = h.run_tool("```github ls\n```")
    assert "a.txt" in listed.output
    deleted = h.run_tool("```github delete a.txt\n```")
    assert deleted.ok
    assert not (tmp_path / "a.txt").exists()


def test_prefers_single_tool_and_done_flag():
    h = AgentHarness(sandbox_dir="/tmp/oa-test-sandbox")
    assert h.is_done("all good\n<DONE>\n")
    assert h.is_compact_ready("summary\n<COMPACT_READY>")
    assert "one tool" in SYSTEM_PROMPT.lower() or "ONE tool" in SYSTEM_PROMPT
    assert "<COMPACT_READY>" in COMPACT_PROMPT


def test_path_escape_rejected(tmp_path):
    h = AgentHarness(sandbox_dir=str(tmp_path))
    h.fs.workspace = tmp_path
    result = h.run_tool("```github read ../secret\n```")
    assert result is not None
    assert not result.ok
