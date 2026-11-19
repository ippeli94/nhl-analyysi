"""Static checks on the GitHub Actions workflows (read as text; no YAML parser)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
PIPELINE_CRONS = ["43 6 * * *", "17 15 * * *", "37 21 * * *", "17 12 * * 0,6"]
EXTRA_CRON = "17 12 * * 0,6"


def _text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _step_containing(text: str, marker: str) -> str:
    """Return the YAML step block (item starting with '      - ') that contains marker."""
    idx = text.index(marker)
    start = text.rfind("\n      - ", 0, idx)
    end = text.find("\n      - ", idx)
    return text[start : end if end != -1 else len(text)]


def test_pipeline_has_four_crons():
    text = _text("pipeline.yml")
    for cron in PIPELINE_CRONS:
        pattern = rf"cron:\s*[\"']{re.escape(cron)}[\"']"
        assert len(re.findall(pattern, text)) == 1, cron


def test_pipeline_has_concurrency_and_permissions():
    text = _text("pipeline.yml")
    concurrency = r"concurrency:\s*\n\s+group:\s*pipeline\s*\n\s+cancel-in-progress:\s*false"
    assert re.search(concurrency, text)
    for perm in ("contents: write", "pages: write", "id-token: write"):
        assert perm in text, perm
    assert "timeout-minutes: 20" in text
    assert "workflow_dispatch" in text


def test_pipeline_checks_out_the_triggering_ref():
    step = _step_containing(_text("pipeline.yml"), "actions/checkout")
    assert "ref: ${{ github.ref_name }}" in step


def test_pipeline_extra_run_only_on_weekend_cron():
    text = _text("pipeline.yml")
    run_step = _step_containing(text, "id: run")
    assert "--extra-if-early" in run_step
    assert f"github.event.schedule == '{EXTRA_CRON}'" in run_step


def test_deploy_is_guarded_by_ran_and_review_code():
    text = _text("pipeline.yml")
    deploy_step = _step_containing(text, "actions/upload-pages-artifact@v3")
    assert "steps.run.outputs.ran == 'true'" in deploy_step
    assert "steps.review.outputs.code != '2'" in deploy_step
    for marker in ("actions/configure-pages@v5", "actions/deploy-pages@v4"):
        step = _step_containing(text, marker)
        assert "steps.run.outputs.ran == 'true'" in step, marker
        assert "steps.review.outputs.code != '2'" in step, marker
    assert "environment:" in text and "github-pages" in text


def test_pipeline_deploy_retries():
    text = _text("pipeline.yml")
    guard = "steps.run.outputs.ran == 'true' && steps.review.outputs.code != '2'"
    deploy_step = _step_containing(text, "id: deploy\n")
    assert "continue-on-error: true" in deploy_step
    wait_step = _step_containing(text, "name: Wait before retry")
    assert "sleep 60" in wait_step
    assert guard in wait_step and "steps.deploy.outcome == 'failure'" in wait_step
    retry_step = _step_containing(text, "name: Retry deploy")
    assert "uses: actions/deploy-pages@v4" in retry_step
    assert "id: deploy_retry" in retry_step
    assert "steps.deploy.outcome == 'failure'" in retry_step
    assert guard in retry_step
    assert "continue-on-error" not in retry_step


def test_fail_step_runs_only_on_review_code_2():
    text = _text("pipeline.yml")
    fail_step = _step_containing(text, "exit 1")
    assert "if: steps.review.outputs.code == '2'" in fail_step


def test_workflow_inputs_are_not_interpolated_into_run():
    for name in ("backfill.yml", "train_xg.yml"):
        for line in _text(name).splitlines():
            if "${{ inputs." in line:
                assert re.fullmatch(r"\s+[A-Z_]+: \$\{\{ inputs\.\w+ \}\}", line), (name, line)


def test_train_defaults_are_two_latest_completed_seasons_and_current_test():
    text = _text("train_xg.yml")
    assert 'default: "20242025 20252026"' in text
    assert 'default: "20262027"' in text
    assert "timeout-minutes: 30" in text


def test_odds_secret_only_in_run_step():
    text = _text("pipeline.yml")
    assert text.count("secrets.") == 1
    step = _step_containing(text, "id: run")
    assert "secrets.ODDS_API_KEY" in step and "--odds" in step
    for cond in ("'17 15 * * *'", "'37 21 * * *'", "github.event_name == 'workflow_dispatch'"):
        assert step.count(cond) == 2, cond     # key and flag share the same condition
    assert "'43 6 * * *'" not in step


def test_other_workflows_have_no_secrets():    # ci, backfill, train_xg
    for name in ("ci.yml", "backfill.yml", "train_xg.yml"):
        assert "secrets." not in _text(name), name


def test_actions_are_pinned_to_major_versions():
    for name in ("ci.yml", "pipeline.yml", "backfill.yml", "train_xg.yml"):
        uses = re.findall(r"uses:\s*(\S+)", _text(name))
        assert uses, name
        for ref in uses:
            assert re.search(r"@v\d+$", ref), (name, ref)


def test_ci_runs_ruff_pytest_and_node_tests():
    text = _text("ci.yml")
    assert re.search(r"^on:", text, re.MULTILINE)
    assert "push:" in text and "pull_request:" in text
    assert "ruff check ." in text
    assert "python -m pytest" in text
    assert "node --test site/tests/format.test.js site/tests/bets.test.js" in text
    assert "node --check site/app.js" in text


def test_backfill_and_training_are_manual_and_commit_their_output():
    backfill = _text("backfill.yml")
    train = _text("train_xg.yml")
    assert "workflow_dispatch" in backfill and "workflow_dispatch" in train
    assert "timeout-minutes: 120" in backfill
    assert "pipeline.backfill" in backfill and "data/cache" in backfill
    assert "pipeline.xg train" in train and "model/xg_coefs.json" in train
    assert "schedule:" not in backfill and "schedule:" not in train
