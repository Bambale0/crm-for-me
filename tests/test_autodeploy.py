"""Exercise deployment decisions without touching Docker or the working checkout."""

import json

import pytest

from deploy.autodeploy import Deployer, ci_passed

OLD = "a" * 40
NEW = "b" * 40
GOOD = {
    "headSha": NEW,
    "headBranch": "main",
    "event": "push",
    "status": "completed",
    "conclusion": "success",
    "url": "https://example.test/ci",
}


@pytest.mark.parametrize(
    "change",
    [
        {"headSha": OLD},
        {"headBranch": "feature"},
        {"event": "pull_request"},
        {"event": "pull_request_target"},
        {"event": "repository_dispatch"},
        {"status": "in_progress"},
        {"conclusion": "failure"},
        {"conclusion": "cancelled"},
    ],
)
def test_only_exact_successful_main_run_is_accepted(change):
    assert not ci_passed([GOOD | change], NEW)


class SimulatedDeployer(Deployer):
    def __init__(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CRM_DEPLOY_DIR", str(tmp_path))
        monkeypatch.setenv("CRM_DEPLOY_STATE", str(tmp_path / "state"))
        super().__init__()
        self.head = OLD
        self.schema = "0005"
        self.calls = []
        self.runs = [GOOD]
        self.dirty = False
        self.fail_migration = False
        self.change_schema = False
        self.fail_start = False

    def git(self, *args):
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return " M app.py" if self.dirty else ""
        if args == ("rev-parse", "HEAD"):
            return self.head
        if args == ("rev-parse", "origin/main"):
            return NEW
        if args[:2] == ("merge", "--ff-only"):
            self.head = args[2]
        if args[:2] == ("reset", "--hard"):
            self.head = args[2]
        return ""

    def run(self, args, **kwargs):
        self.calls.append(tuple(args))
        if args[:3] == ["gh", "run", "list"]:
            return json.dumps(self.runs)
        if args[:3] == ["docker", "image", "inspect"]:
            return "new-image"
        return ""

    def compose(self, *args, **kwargs):
        self.calls.append(args)
        if "alembic" in args:
            if self.fail_migration:
                raise RuntimeError("migration failed")
            if self.change_schema:
                self.schema = "0006"
        return ""

    def schema_version(self):
        return self.schema

    def bot_info(self):
        return {"Image": "old-image"}

    def wait_ready(self, expected_image, timeout=75):
        self.calls.append(("ready", expected_image))
        if expected_image == "new-image" and self.fail_start:
            raise RuntimeError("startup failed")

    def backup(self, sha):
        self.calls.append(("backup", sha))
        path = self.state_dir / "backup.dump"
        path.write_bytes(b"backup")
        return path


def test_pending_ci_does_not_build_or_stop_bot(tmp_path, monkeypatch):
    deployer = SimulatedDeployer(tmp_path, monkeypatch)
    deployer.runs = [GOOD | {"status": "in_progress"}]
    deployer.deploy()
    assert len(deployer.calls) == 1 and deployer.calls[0][0] == "gh"


def test_dirty_checkout_is_never_overwritten(tmp_path, monkeypatch):
    deployer = SimulatedDeployer(tmp_path, monkeypatch)
    deployer.dirty = True
    with pytest.raises(RuntimeError, match="left untouched"):
        deployer.deploy()
    assert not deployer.calls


def test_success_records_revision_only_after_backup_migration_and_readiness(tmp_path, monkeypatch):
    deployer = SimulatedDeployer(tmp_path, monkeypatch)
    deployer.deploy()
    state = json.loads(deployer.state_file.read_text())
    assert state["deployed_sha"] == deployer.head == NEW
    stop = deployer.calls.index(("stop", "bot"))
    backup = deployer.calls.index(("backup", NEW))
    migrate = next(n for n, call in enumerate(deployer.calls) if "alembic" in call)
    ready = deployer.calls.index(("ready", "new-image"))
    assert stop < backup < migrate < ready


def test_failed_migration_restores_previous_image_and_does_not_loop(tmp_path, monkeypatch):
    deployer = SimulatedDeployer(tmp_path, monkeypatch)
    deployer.fail_migration = True
    with pytest.raises(RuntimeError, match="migration failed"):
        deployer.deploy()
    assert deployer.head == OLD
    assert ("ready", "old-image") in deployer.calls
    assert json.loads(deployer.state_file.read_text())["failed_sha"] == NEW
    deployer.calls.clear()
    deployer.deploy()
    assert not deployer.calls


def test_failed_start_rolls_back_only_when_schema_is_unchanged(tmp_path, monkeypatch):
    deployer = SimulatedDeployer(tmp_path, monkeypatch)
    deployer.fail_start = True
    with pytest.raises(RuntimeError, match="startup failed"):
        deployer.deploy()
    assert deployer.head == OLD and ("ready", "old-image") in deployer.calls


def test_schema_change_prevents_unsafe_automatic_rollback(tmp_path, monkeypatch):
    deployer = SimulatedDeployer(tmp_path, monkeypatch)
    deployer.fail_start = deployer.change_schema = True
    with pytest.raises(RuntimeError, match="startup failed"):
        deployer.deploy()
    assert deployer.head == NEW
    assert ("ready", "old-image") not in deployer.calls
    assert not any("pg_restore" in call for call in deployer.calls)


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_successful_push_and_manual_run_are_accepted(event):
    assert ci_passed([GOOD | {"event": event}], NEW)
    assert not ci_passed([GOOD | {"event": event, "headSha": OLD}], NEW)
    assert not ci_passed([GOOD | {"event": event, "headBranch": "feature"}], NEW)
    assert not ci_passed([GOOD | {"event": event, "conclusion": "failure"}], NEW)
    assert not ci_passed([GOOD | {"event": event, "status": "in_progress"}], NEW)


def test_manual_ci_is_included_in_deployer_lookup(tmp_path, monkeypatch):
    deployer = SimulatedDeployer(tmp_path, monkeypatch)
    deployer.runs = [GOOD | {"event": "workflow_dispatch"}]
    deployer.deploy(check=True)
    args = deployer.calls[0]
    assert "--event" not in args
    assert args[args.index("--commit") + 1] == NEW
    assert args[args.index("--branch") + 1] == "main"
    assert args[args.index("--workflow") + 1] == "ci.yml"
