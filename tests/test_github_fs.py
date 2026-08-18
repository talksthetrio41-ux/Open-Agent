from open_agent.github_fs import parse_github_block


def test_parse_write_block():
    text = "```github write src/app.py\nprint(1)\n```"
    action, path, body = parse_github_block(text)
    assert action == "write"
    assert path == "src/app.py"
    assert "print(1)" in body


def test_parse_commit_block():
    text = "```github commit add hello script\n```"
    action, path, body = parse_github_block(text)
    assert action == "commit"
    assert "add hello script" in path


def test_parse_ls_alias():
    text = "```github list lib/\n```"
    action, path, _ = parse_github_block(text)
    assert action == "ls"
    assert path == "lib/"
