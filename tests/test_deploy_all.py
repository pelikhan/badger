import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("deploy_collection", ROOT / "tools/deploy_all.py")
deployment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deployment)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / "collection"
        self.apps = self.repo / "badge/apps"
        self.apps.mkdir(parents=True)
        self.mount = self.root / "BADGER"
        self.destination = self.mount / "apps"
        self.destination.mkdir(parents=True)
        self.deployer = SimpleNamespace(
            find_apps_dir=Mock(return_value=self.destination),
            deploy=Mock(),
        )
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def app(self, name):
        app = self.apps / name
        app.mkdir()
        (app / "__init__.py").write_text(
            f'import os\nos.chdir("/system/apps/{name}")\n'
            "def update():\n    pass\nrun(update)\n"
        )
        (app / "README.md").write_text("# Test app\n\nThis app tests badge deployment.\n")
        return app

    def run_deploy(self, **kwargs):
        deployment.deploy_all(self.repo, self.deployer, self.mount, **kwargs)

    def test_preview_discovers_sorted_apps_and_never_writes(self):
        second = self.app("zebra")
        first = self.app("alpha")
        (self.apps / "__pycache__").mkdir()
        (self.apps / ".hidden").mkdir()
        (self.apps / "README.md").write_text("Not an app.")
        self.run_deploy(replace=True)
        self.assertEqual(
            self.deployer.deploy.call_args_list,
            [
                ((first, self.mount, False, True, self.repo),),
                ((second, self.mount, False, True, self.repo),),
            ],
        )
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_all_preflights_precede_all_writes(self):
        first = self.app("alpha")
        second = self.app("zebra")
        self.run_deploy(write=True, replace=True)
        self.assertEqual(
            [call.args for call in self.deployer.deploy.call_args_list],
            [
                (first, self.mount, False, True, self.repo),
                (second, self.mount, False, True, self.repo),
                (first, self.mount, True, True, self.repo),
                (second, self.mount, True, True, self.repo),
            ],
        )

    def test_later_preflight_failure_prevents_all_writes(self):
        self.app("alpha")
        self.app("zebra")
        for error in (RuntimeError("validation failed"), FileExistsError("already installed")):
            with self.subTest(error=error):
                self.deployer.deploy.reset_mock()
                self.deployer.deploy.side_effect = [None, error]
                with self.assertRaises(type(error)):
                    self.run_deploy(write=True)
                self.assertTrue(all(not call.args[2] for call in self.deployer.deploy.call_args_list))

    def test_write_failure_stops_and_reports_partial_progress(self):
        self.app("alpha")
        self.app("middle")
        self.app("zebra")
        self.deployer.deploy.side_effect = [None, None, None, None, OSError("disk full")]
        with self.assertRaisesRegex(RuntimeError, "middle; earlier apps may already be updated"):
            self.run_deploy(write=True)
        self.assertEqual(self.deployer.deploy.call_count, 5)

    def test_empty_collection_and_missing_entrypoint_fail(self):
        with self.assertRaisesRegex(ValueError, "No apps"):
            self.run_deploy()
        (self.apps / "broken").mkdir()
        with self.assertRaisesRegex(ValueError, "Missing app entry"):
            self.run_deploy()
        self.deployer.deploy.assert_not_called()

    def test_reserved_names_and_icons_fail_before_deployment(self):
        for name in ("menu", "startup", "Invalid"):
            with self.subTest(name=name):
                app = self.app(name)
                with self.assertRaisesRegex(ValueError, "unsafe or reserved"):
                    self.run_deploy(write=True)
                (app / "__init__.py").unlink()
                (app / "README.md").unlink()
                app.rmdir()
        app = self.app("alpha")
        (app / "ICON.PNG").write_bytes(b"not deployed")
        with self.assertRaisesRegex(ValueError, "forbidden icon"):
            self.run_deploy(write=True)
        self.deployer.deploy.assert_not_called()

    def test_source_and_destination_symlinks_are_rejected(self):
        app = self.app("alpha")
        link = app / "linked.py"
        link.symlink_to(app / "__init__.py")
        with self.assertRaisesRegex(ValueError, "symlink in app"):
            self.run_deploy()
        link.unlink()
        link = self.destination / "alpha"
        link.symlink_to(app, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "destination"):
            self.run_deploy()
        link.unlink()
        self.destination.rmdir()
        self.destination.symlink_to(self.apps, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink on badge"):
            self.run_deploy()
        self.deployer.deploy.assert_not_called()

    def test_bootloader_and_stale_staging_are_rejected(self):
        self.app("alpha")
        marker = self.mount / "INFO_UF2.TXT"
        marker.write_text("UF2 Bootloader")
        with self.assertRaisesRegex(ValueError, "bootloader"):
            self.run_deploy()
        marker.unlink()
        staging = self.mount / ".badge-app-staging/alpha"
        staging.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "Previous deployment staging"):
            self.run_deploy()
        self.assertTrue(staging.is_dir())
        self.deployer.deploy.assert_not_called()

    def test_skill_discovery_and_explicit_missing_skill(self):
        scripts = self.repo / ".github/skills/hyb26-skill/scripts"
        scripts.mkdir(parents=True)
        for name in ("deploy_app.py", "validate_app.py", "skill_paths.py"):
            (scripts / name).write_text("")
        self.assertEqual(deployment.find_skill_scripts(None, self.repo), scripts)
        self.assertEqual(deployment.find_skill_scripts(scripts, self.repo), scripts)
        with self.assertRaisesRegex(FileNotFoundError, "Install hyb26-skill"):
            deployment.find_skill_scripts(self.root / "missing", self.repo)

    def test_cli_failure_has_nonzero_status_and_error(self):
        with patch.object(deployment, "find_skill_scripts", side_effect=FileNotFoundError("missing")):
            with contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(deployment.main([]), 1)
        self.assertIn("error: missing", errors.getvalue())

    def test_upstream_copy_backup_exclusions_and_nested_layout(self):
        try:
            scripts = deployment.find_skill_scripts(None, ROOT)
        except FileNotFoundError:
            self.skipTest("Install hyb26-skill to run the upstream integration check")
        deployer = deployment.load_deployer(scripts)
        app = self.app("alpha")
        for name in (".private", "cache.pyc"):
            (app / name).write_text("excluded")
        assets = app / "assets"
        assets.mkdir()
        (assets / "data.txt").write_text("app-owned data")
        self.destination.rmdir()
        self.destination = self.mount / "system/apps"
        self.destination.mkdir(parents=True)
        target = self.destination / "alpha"
        target.mkdir()
        (target / "old.txt").write_text("old version")
        unrelated = self.destination / "other_app"
        unrelated.mkdir()
        (unrelated / "keep.txt").write_text("untouched")
        (app / "secrets.py").write_text("# No credentials.")
        with self.assertRaisesRegex(RuntimeError, "secrets.py"):
            deployment.deploy_all(self.repo, deployer, self.mount, write=True, replace=True)
        self.assertFalse((self.mount / ".badge-app-backups").exists())
        (app / "secrets.py").unlink()
        with self.assertRaises(FileExistsError):
            deployment.deploy_all(self.repo, deployer, self.mount, write=True)
        deployment.deploy_all(self.repo, deployer, self.mount, replace=True)
        self.assertEqual([path.name for path in target.iterdir()], ["old.txt"])
        self.assertFalse((self.mount / ".badge-app-backups").exists())
        deployment.deploy_all(self.repo, deployer, self.mount, write=True, replace=True)
        self.assertEqual(
            sorted(path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()),
            ["README.md", "__init__.py", "assets/data.txt"],
        )
        backups = list((self.mount / ".badge-app-backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "old.txt").read_text(), "old version")
        self.assertEqual((unrelated / "keep.txt").read_text(), "untouched")
        self.assertFalse((self.mount / ".badge-app-staging").exists())

    def test_collection_preview_with_upstream_tools(self):
        try:
            scripts = deployment.find_skill_scripts(None, ROOT)
        except FileNotFoundError:
            self.skipTest("Install hyb26-skill to run the upstream integration check")
        deployer = deployment.load_deployer(scripts)
        deployment.deploy_all(ROOT, deployer, self.mount)
        for app in deployment.discover_apps(ROOT):
            self.assertIn(f"Would deploy {app.name}", self.output.getvalue())
        self.assertEqual(list(self.mount.iterdir()), [self.destination])


if __name__ == "__main__":
    unittest.main()
