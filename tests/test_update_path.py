"""Regression tests for the stale-code bug.

A phone kept crashing with `ModuleNotFoundError: No module named 'playwright'`
long after the lazy-import fix landed on main, because install.sh's update
path swallowed git failures (`|| true` + `--ff-only` on a shallow clone) and
silently ran the old code. The installer and launcher must hard-reset to the
remote branch and self-heal instead.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = (ROOT / "install.sh").read_text(encoding="utf-8")
LAUNCHER = (ROOT / "oa").read_text(encoding="utf-8")


def test_install_update_hard_resets_to_remote():
    assert 'reset --hard "origin/$BRANCH"' in INSTALL


def test_install_update_never_silently_keeps_stale_code():
    # The old block fetched/checked-out/pulled with `|| true` on every line.
    start = INSTALL.find('if [ -d "$INSTALL_DIR/.git" ]')
    end = INSTALL.find('cd "$INSTALL_DIR"', start)
    block = INSTALL[start:end]
    assert "pull --ff-only" not in block
    # No line in the update block may swallow git failures.
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("git "):
            assert not stripped.endswith("|| true"), stripped


def test_install_reclones_when_update_fails():
    assert "fresh_clone" in INSTALL
    assert "git update failed" in INSTALL


def test_install_self_heals_toplevel_playwright_import():
    # The installer must detect the stale-code signature and force a re-clone.
    assert 'grep -qE "^(from|import)[[:space:]]+playwright" open_agent/qwen_browser.py' in INSTALL
    assert "Stale code detected" in INSTALL


def test_launcher_self_updates_with_opt_out():
    assert "OA_NO_UPDATE" in LAUNCHER
    assert 'reset --hard "origin/$BRANCH"' in LAUNCHER


def test_main_prints_running_commit():
    src = (ROOT / "open_agent" / "__main__.py").read_text(encoding="utf-8")
    assert "Open Agent commit:" in src
