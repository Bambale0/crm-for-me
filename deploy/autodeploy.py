#!/usr/bin/env python3
"""Deploy the exact main commit approved by CI; runs locally under systemd."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def log(message: str) -> None:
    print(message, flush=True)


def ci_passed(runs: list[dict], sha: str) -> bool:
    # Only the latest trusted run for this exact main commit is authoritative.
    return (
        bool(runs)
        and runs[0].get("event") in ("push", "workflow_dispatch")
        and all(
            runs[0].get(key) == value
            for key, value in {
                "headSha": sha,
                "headBranch": "main",
                "status": "completed",
                "conclusion": "success",
            }.items()
        )
    )


def atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


class Deployer:
    def __init__(self) -> None:
        self.root = Path(os.environ.get("CRM_DEPLOY_DIR", "/opt/crm-for-me"))
        self.state_dir = Path(os.environ.get("CRM_DEPLOY_STATE", "/var/lib/crm-for-me-deploy"))
        self.repo = os.environ.get("CRM_DEPLOY_REPO", "Bambale0/crm-for-me")
        self.project = os.environ.get("CRM_DEPLOY_PROJECT", "crm-for-me")
        self.image = os.environ.get("CRM_DEPLOY_IMAGE", "crm-for-me-bot")
        self.db_user = os.environ.get("CRM_DEPLOY_DB_USER", "crm")
        self.db_name = os.environ.get("CRM_DEPLOY_DB_NAME", "crm")
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_file = self.state_dir / "state.json"

    def run(self, args: list[str], *, timeout: int = 120, output=None) -> str:
        result = subprocess.run(
            args,
            cwd=self.root,
            text=output is None,
            stdout=output or subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            details = (result.stdout or "") if output is None else ""
            errors = result.stderr
            if isinstance(errors, bytes):
                errors = errors.decode(errors="replace")
            # Details may contain sensitive application errors; keep them off the journal.
            (self.state_dir / "last-command-error.log").write_text(details + errors)
            raise RuntimeError(
                f"{args[0]} failed (exit {result.returncode}); see protected last-command-error.log"
            )
        return result.stdout.strip() if output is None else ""

    def git(self, *args: str) -> str:
        return self.run(["git", *args])

    def compose(self, *args: str, timeout: int = 120, output=None) -> str:
        return self.run(
            [
                "docker",
                "compose",
                "-p",
                self.project,
                "--project-directory",
                str(self.root),
                "--env-file",
                str(self.root / ".env"),
                "-f",
                str(self.root / "docker-compose.yml"),
                *args,
            ],
            timeout=timeout,
            output=output,
        )

    def clean_main(self, expected: str | None = None) -> str:
        if self.git("branch", "--show-current") != "main" or self.git("status", "--porcelain"):
            raise RuntimeError("Checkout must be clean and on main; local work was left untouched")
        head = self.git("rev-parse", "HEAD")
        if expected is not None and head != expected:
            raise RuntimeError("Checkout changed during deployment; local work was left untouched")
        return head

    def schema_version(self) -> str:
        return self.compose(
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            self.db_user,
            "-d",
            self.db_name,
            "-Atc",
            "SELECT version_num FROM alembic_version ORDER BY version_num",
        )

    def bot_info(self) -> dict:
        container_id = self.compose("ps", "-a", "-q", "bot")
        if not container_id:
            raise RuntimeError("Bot container not found")
        return json.loads(self.run(["docker", "inspect", container_id]))[0]

    def wait_ready(self, expected_image: str, timeout: int = 75) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            info = self.bot_info()
            if (
                info["Image"] == expected_image
                and info["State"]["Running"]
                and info["RestartCount"] == 0
            ):
                # Docker forwards application logs to either stdout or stderr.
                result = subprocess.run(
                    ["docker", "logs", info["Id"]],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=True,
                )
                logs = result.stdout + result.stderr
                if "Run polling for bot" in logs:
                    return
            time.sleep(2)
        raise RuntimeError("Bot did not start Telegram polling with the expected image")

    def backup(self, sha: str) -> Path:
        directory = self.root / "backups"
        directory.mkdir(exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = directory / f"autodeploy-{stamp}-{sha[:12]}.dump"
        with path.open("xb") as output:
            self.compose(
                "exec",
                "-T",
                "db",
                "pg_dump",
                "-U",
                self.db_user,
                "-d",
                self.db_name,
                "-Fc",
                output=output,
            )
        if not path.stat().st_size:
            raise RuntimeError("Empty database backup; deployment refused")
        return path

    def rollback(self, previous_sha: str, candidate: str, old_image: str, old_schema: str) -> bool:
        if self.schema_version() != old_schema:
            log(
                "Schema changed: automatic rollback is disabled to preserve new data. Manual recovery required."
            )
            return False
        head = self.clean_main()
        if head not in (previous_sha, candidate):
            raise RuntimeError("Unexpected checkout revision; automatic rollback refused")
        self.compose("stop", "bot")
        # Only undo our own fast-forward in a verified clean deployment checkout.
        if head == candidate and head != previous_sha:
            self.git("reset", "--hard", previous_sha)
        self.run(["docker", "image", "tag", old_image, f"{self.image}:latest"])
        self.compose("up", "-d", "--no-deps", "--no-build", "--force-recreate", "bot")
        self.wait_ready(old_image)
        log(f"Previous bot restored: {previous_sha}")
        return True

    def deploy(self, *, check: bool = False, retry: bool = False) -> None:
        previous_sha = self.clean_main()
        self.git("fetch", "origin", "main")
        candidate = self.git("rev-parse", "origin/main")
        state = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
        if state.get("deployed_sha") == candidate:
            log(f"Already deployed: {candidate}")
            return
        if state.get("failed_sha") == candidate and not retry:
            log(f"Failed revision skipped: {candidate}; use --retry after investigation")
            return
        self.git("merge-base", "--is-ancestor", previous_sha, candidate)
        runs = json.loads(
            self.run(
                [
                    "gh",
                    "run",
                    "list",
                    "--repo",
                    self.repo,
                    "--workflow",
                    "ci.yml",
                    "--branch",
                    "main",
                    "--commit",
                    candidate,
                    "--limit",
                    "1",
                    "--json",
                    "headSha,headBranch,event,status,conclusion,url",
                ]
            )
        )
        if not ci_passed(runs, candidate):
            log(f"Waiting for successful main CI: {candidate}")
            return
        if check:
            log(f"Ready to deploy: {candidate}; CI: {runs[0]['url']}")
            return
        old_image = self.bot_info()["Image"]
        old_schema = self.schema_version()
        stopped = False
        try:
            with tempfile.TemporaryDirectory(prefix="build-", dir=self.state_dir) as temporary:
                build = Path(temporary)
                archive = build / "source.tar"
                with archive.open("wb") as output:
                    self.run(["git", "archive", candidate], output=output)
                self.run(["tar", "-xf", str(archive), "-C", str(build)])
                archive.unlink()
                log(f"Building {candidate}")
                tag = f"{self.image}:{candidate}"
                self.run(
                    [
                        "docker",
                        "build",
                        "--label",
                        f"org.opencontainers.image.revision={candidate}",
                        "-t",
                        tag,
                        str(build),
                    ],
                    timeout=600,
                )
                image_id = self.run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"])
                self.run(
                    [
                        "docker",
                        "compose",
                        "-p",
                        self.project,
                        "--project-directory",
                        str(self.root),
                        "--env-file",
                        str(self.root / ".env"),
                        "-f",
                        str(build / "docker-compose.yml"),
                        "config",
                        "--quiet",
                    ]
                )
                self.clean_main(previous_sha)
                self.compose("stop", "bot")
                stopped = True
                backup = self.backup(candidate)
                log(f"Backup saved: {backup.name}")
                self.git("merge", "--ff-only", candidate)
                self.run(["docker", "image", "tag", old_image, f"{self.image}:rollback"])
                self.run(["docker", "image", "tag", tag, f"{self.image}:latest"])
                log("Applying migrations")
                migration_name = f"{self.project}-migration-{candidate[:12]}"
                try:
                    self.compose(
                        "run",
                        "--rm",
                        "--no-deps",
                        "--name",
                        migration_name,
                        "bot",
                        "alembic",
                        "upgrade",
                        "head",
                        timeout=180,
                    )
                except subprocess.TimeoutExpired:
                    # Stop the one-off process before deciding whether rollback is safe.
                    self.run(["docker", "rm", "-f", migration_name])
                    raise
                self.compose("up", "-d", "--no-deps", "--no-build", "--force-recreate", "bot")
                self.wait_ready(image_id)
            atomic_json(
                self.state_file,
                {
                    "deployed_sha": candidate,
                    "previous_sha": previous_sha,
                    "deployed_at": datetime.now(timezone.utc).isoformat(),
                    "ci_url": runs[0]["url"],
                    "backup": str(backup),
                    "image_id": image_id,
                },
            )
            log(f"Deployment successful: {candidate}")
        except Exception:
            state["failed_sha"] = candidate
            atomic_json(self.state_file, state)
            if stopped:
                try:
                    self.rollback(previous_sha, candidate, old_image, old_schema)
                except Exception as error:
                    log(f"Rollback needs manual recovery: {type(error).__name__}")
            raise


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="Check CI and revision without deploying"
    )
    parser.add_argument(
        "--retry", action="store_true", help="Retry a revision previously marked failed"
    )
    args = parser.parse_args()
    deployer = Deployer()
    with (deployer.state_dir / "deploy.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("Another deployment is running")
            return
        deployer.deploy(check=args.check, retry=args.retry)


if __name__ == "__main__":
    main()
