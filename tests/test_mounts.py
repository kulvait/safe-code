import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = ("safe-opencode", "safe-claudecode")


class MountTests(unittest.TestCase):
    def setUp(self):
        # Host-path links cannot mount below reserved sandbox paths such as /tmp.
        self.temp = tempfile.TemporaryDirectory(prefix="safe-code-test-", dir=Path.home())
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.source = self.base / "external data"
        self.other = self.base / "other data"
        self.bin = self.base / "bin"
        for directory in (self.project, self.source, self.other, self.bin):
            directory.mkdir()
        self.capture = self.base / "bwrap-args"
        for name, body in (
            ("opencode", "exit 0\n"),
            ("claude", "exit 0\n"),
            ("bwrap", 'printf "%s\\0" "$@" > "$CAPTURE"\n'),
        ):
            executable = self.bin / name
            executable.write_text("#!/bin/bash\n" + body)
            executable.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "CAPTURE": str(self.capture),
            "SAFE_OPENCODE_CONFIG": str(self.base / "opencode-config"),
            "SAFE_OPENCODE_DATA": str(self.base / "opencode-data"),
            "SAFE_OPENCODE_BWRAP": str(self.bin / "bwrap"),
            "SAFE_CLAUDECODE_CONFIG": str(self.base / "claude-config"),
            "SAFE_CLAUDECODE_BIN": str(self.bin / "claude"),
            "SAFE_CLAUDECODE_BWRAP": str(self.bin / "bwrap"),
        }

    def run_launcher(self, launcher, *options, succeeds=True):
        self.capture.unlink(missing_ok=True)
        result = subprocess.run(
            [str(ROOT / launcher), "--dry-run", *map(str, options), str(self.project)],
            cwd=self.base,
            env=self.env,
            capture_output=True,
            text=True,
        )
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stderr)
            return self.capture.read_bytes().decode().rstrip("\0").split("\0")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Error:", result.stderr)
        self.assertFalse(self.capture.exists())
        return result

    def assert_bind(self, args, mode, source, target):
        bindings = list(zip(args, args[1:], args[2:]))
        self.assertIn((mode, str(source), str(target)), bindings)

    def test_claude_disables_agent_view_inside_sandbox(self):
        args = self.run_launcher("safe-claudecode")
        self.assertIn(
            ("--setenv", "CLAUDE_CODE_DISABLE_AGENT_VIEW", "1"),
            list(zip(args, args[1:], args[2:])),
        )

    def test_links_persist_at_real_source_path_and_can_be_reused(self):
        alias = self.base / "source alias"
        alias.symlink_to(self.source, target_is_directory=True)
        for launcher in LAUNCHERS:
            for mode, bind in (("ro", "--ro-bind"), ("rw", "--bind")):
                with self.subTest(launcher=launcher, mode=mode):
                    target = self.project / f"{launcher}-{mode}"
                    for _ in range(2):
                        args = self.run_launcher(launcher, "--rw", f"--link-{mode}", alias, target.name)
                        self.assertEqual(target.readlink(), self.source)
                        self.assert_bind(args, bind, self.source, self.source)
                        self.assert_bind(args, "--bind", self.project, "/workspace")

    def test_existing_equivalent_relative_symlink_is_preserved(self):
        target = self.project / "data"
        relative = Path("..") / self.source.name
        target.symlink_to(relative)
        for launcher in LAUNCHERS:
            self.run_launcher(launcher, "--rw", "--link-ro", self.source, "data")
            self.assertEqual(target.readlink(), relative)

    def test_conflicting_targets_are_not_replaced_or_partially_created(self):
        for kind in ("file", "directory", "wrong-link", "dangling-link"):
            target = self.project / kind
            if kind == "file":
                target.write_text("unchanged")
            elif kind == "directory":
                target.mkdir()
            else:
                target.symlink_to(self.other if kind == "wrong-link" else self.base / "missing")
            for launcher in LAUNCHERS:
                with self.subTest(launcher=launcher, kind=kind):
                    result = self.run_launcher(
                        launcher, "--rw", "--link-ro", self.source, "not-created",
                        "--link-rw", self.source, kind, succeeds=False,
                    )
                    self.assertIn("not a symlink", result.stderr)
                    self.assertFalse((self.project / "not-created").is_symlink())
            if kind == "file":
                self.assertEqual(target.read_text(), "unchanged")
            elif kind == "directory":
                self.assertTrue(target.is_dir())
            else:
                self.assertEqual(target.readlink(), self.other if kind == "wrong-link" else self.base / "missing")

    def test_multiple_links_share_one_mount(self):
        for launcher in LAUNCHERS:
            args = self.run_launcher(
                launcher, "--rw", "--link-ro", self.source, "first",
                "--link-ro", self.source, "second",
            )
            self.assertEqual(list(zip(args, args[1:], args[2:])).count(
                ("--ro-bind", str(self.source), str(self.source))), 1)

    def test_project_relative_mounts_leave_host_directories_unchanged(self):
        destination = self.project / "nested" / "data"
        destination.mkdir(parents=True)
        for launcher in LAUNCHERS:
            for mode, bind in (("ro", "--ro-bind"), ("rw", "--bind")):
                with self.subTest(launcher=launcher, mode=mode):
                    args = self.run_launcher(
                        launcher, "--rw", f"--mount-{mode}", f"{self.source}=./nested/data"
                    )
                    self.assert_bind(args, bind, self.source, "/workspace/nested/data")
                    self.assert_bind(args, "--bind", self.project, "/workspace")
                    self.assertFalse(destination.is_symlink())
                    self.assertEqual(list(destination.iterdir()), [])

    def test_workspace_root_mounts_are_rejected(self):
        for launcher in LAUNCHERS:
            for mode in ("ro", "rw"):
                for target in (".", "./", "/workspace", "/workspace/child/.."):
                    with self.subTest(launcher=launcher, mode=mode, target=target):
                        self.run_launcher(
                            launcher, "--rw", f"--mount-{mode}",
                            f"{self.source}={target}", succeeds=False,
                        )

    def test_nonempty_workspace_mountpoints_are_rejected(self):
        for name in ("visible", ".hidden", "subdirectory", "dangling"):
            destination = self.project / name
            destination.mkdir()
            entry = destination / name
            if name == "subdirectory":
                entry.mkdir()
            elif name == "dangling":
                entry.symlink_to(self.base / "missing")
            else:
                entry.write_text("unchanged")
            for launcher in LAUNCHERS:
                for mode in ("ro", "rw"):
                    for target in (f"./{name}", f"/workspace/{name}"):
                        with self.subTest(launcher=launcher, mode=mode, target=target):
                            result = self.run_launcher(
                                launcher, "--rw", f"--mount-{mode}",
                                f"{self.source}={target}", succeeds=False,
                            )
                            self.assertIn("must be empty", result.stderr)
                            self.assertEqual(list(destination.iterdir()), [entry])

    def test_invalid_project_mount_destinations(self):
        (self.project / "alias").symlink_to(self.other, target_is_directory=True)
        (self.project / "internal-alias").symlink_to(".", target_is_directory=True)
        (self.project / "dangling").symlink_to(self.project / "missing")
        (self.project / "file").write_text("unchanged")
        for launcher in LAUNCHERS:
            for target in (
                "missing/child", "../escape", ".", "alias", "internal-alias/alias",
                "internal-alias/new", "dangling", "dangling/child", "file", "file/child",
            ):
                with self.subTest(launcher=launcher, target=target):
                    self.run_launcher(launcher, "--rw", "--mount-ro", f"{self.source}={target}", succeeds=False)
        self.assertFalse((self.project / "missing").exists())

    def test_missing_final_mountpoint_is_accepted_when_parent_exists(self):
        (self.project / "existing").mkdir()
        for launcher in LAUNCHERS:
            for mode, bind in (("ro", "--ro-bind"), ("rw", "--bind")):
                for target in ("./new", "./existing/new", "/workspace/new"):
                    with self.subTest(launcher=launcher, mode=mode, target=target):
                        args = self.run_launcher(
                            launcher, "--rw", f"--mount-{mode}", f"{self.source}={target}"
                        )
                        sandbox_target = (
                            "/workspace/existing/new" if target == "./existing/new"
                            else "/workspace/new"
                        )
                        self.assert_bind(args, bind, self.source, sandbox_target)
                        self.assertIn(("--dir", sandbox_target), list(zip(args, args[1:])))
                        # The fake Bubblewrap records arguments but creates nothing.
                        self.assertFalse((self.project / "new").exists())
                        self.assertFalse((self.project / "existing" / "new").exists())

    def test_link_targets_cannot_escape_project(self):
        (self.project / "outside").symlink_to(self.other, target_is_directory=True)
        for launcher in LAUNCHERS:
            for target in ("../escape", str(self.base / "absolute"), "outside/data", "."):
                with self.subTest(launcher=launcher, target=target):
                    self.run_launcher(launcher, "--rw", "--link-ro", self.source, target, succeeds=False)
        self.assertFalse((self.other / "data").exists())

    def test_reserved_and_conflicting_mounts_fail_before_link_creation(self):
        for launcher in LAUNCHERS:
            for options in (
                ("--link-ro", "/usr", "not-created"),
                ("--link-ro", self.source, "not-created", "--link-rw", self.source, "also-not-created"),
                ("--link-ro", self.source, "not-created", "--link-ro", self.other, "not-created"),
                ("--link-ro", self.source, "not-created", "--mount-ro", f"{self.other}={self.base}"),
            ):
                with self.subTest(launcher=launcher, options=options):
                    self.run_launcher(launcher, "--rw", *options, succeeds=False)
                    self.assertFalse((self.project / "not-created").is_symlink())

    def test_read_only_project_rejects_links_even_when_correct_link_exists(self):
        existing = self.project / "existing"
        existing.symlink_to(self.source)
        for launcher in LAUNCHERS:
            for mode in ("ro", "rw"):
                for target in ("new", "existing"):
                    with self.subTest(launcher=launcher, mode=mode, target=target):
                        result = self.run_launcher(
                            launcher, f"--link-{mode}", self.source, target, succeeds=False,
                        )
                        self.assertIn("require --rw", result.stderr)
                        self.assertFalse((self.project / "new").is_symlink())
                        self.assertEqual(existing.readlink(), self.source)
        self.assertFalse((self.base / "opencode-config").exists())
        self.assertFalse((self.base / "claude-config").exists())

    def test_read_only_project_rejects_relative_and_absolute_workspace_mounts(self):
        (self.project / "data").mkdir()
        for launcher in LAUNCHERS:
            for mode in ("ro", "rw"):
                for target in ("./data", "data", "/workspace/data", "/workspace/nested/../data", "./missing"):
                    with self.subTest(launcher=launcher, mode=mode, target=target):
                        result = self.run_launcher(
                            launcher, f"--mount-{mode}", f"{self.source}={target}", succeeds=False,
                        )
                        self.assertIn("require --rw", result.stderr)
        self.assertEqual(list((self.project / "data").iterdir()), [])
        self.assertFalse((self.base / "opencode-config").exists())
        self.assertFalse((self.base / "claude-config").exists())

    def test_existing_numbered_and_absolute_mount_syntax(self):
        for launcher in LAUNCHERS:
            args = self.run_launcher(
                launcher, "--mount-ro", self.source, "--mount-rw", self.other,
                "--mount-ro", f"{self.other}=/reference",
            )
            self.assert_bind(args, "--ro-bind", self.source, "/mnt/host-ro/1")
            self.assert_bind(args, "--bind", self.other, "/mnt/host-rw/1")
            self.assert_bind(args, "--ro-bind", self.other, "/reference")
            self.assert_bind(args, "--ro-bind", self.project, "/workspace")

    def test_missing_option_arguments(self):
        for launcher in LAUNCHERS:
            for option in ("--link-ro", "--link-rw"):
                with self.subTest(launcher=launcher, option=option):
                    result = subprocess.run(
                        [str(ROOT / launcher), option, str(self.source)],
                        env=self.env, capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("requires HOST_PATH and TARGET", result.stderr)

    @unittest.skipUnless(os.environ.get("SAFE_CODE_TEST_BWRAP"), "set SAFE_CODE_TEST_BWRAP for real sandbox checks")
    def test_real_sandbox_links_and_project_mount_permissions(self):
        for name in ("reference", "results"):
            (self.project / name).mkdir()
        (self.source / "content").write_text("reference")
        env = {
            **self.env,
            "SAFE_OPENCODE_BWRAP": os.environ.get("SAFE_CODE_TEST_BWRAP", ""),
            "SAFE_CLAUDECODE_BWRAP": os.environ.get("SAFE_CODE_TEST_BWRAP", ""),
        }
        commands = """
set -e
test "$(cat /workspace/reference/content)" = reference
test "$(cat /workspace/reference-link/content)" = reference
printf project > /workspace/project-write
if touch /workspace/reference/forbidden 2>/dev/null; then exit 32; fi
if touch /workspace/reference-link/forbidden 2>/dev/null; then exit 33; fi
printf mounted > /workspace/results/output
printf linked > /workspace/results-link/link-output
exit 0
"""
        for launcher in LAUNCHERS:
            with self.subTest(launcher=launcher):
                result = subprocess.run(
                    [
                        str(ROOT / launcher), "--dry-run", "--rw",
                        "--mount-ro", f"{self.source}=./reference",
                        "--mount-rw", f"{self.other}=./results",
                        "--link-ro", str(self.source), "reference-link",
                        "--link-rw", str(self.other), "results-link",
                        str(self.project),
                    ],
                    cwd=self.base, env=env, input=commands,
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((self.project / "project-write").read_text(), "project")
                self.assertEqual((self.other / "output").read_text(), "mounted")
                self.assertEqual((self.other / "link-output").read_text(), "linked")
                self.assertEqual((self.project / "reference-link").readlink(), self.source)
                self.assertEqual((self.project / "results-link").readlink(), self.other)
                self.assertEqual(list((self.project / "results").iterdir()), [])
                self.assertFalse((self.project / "results" / "output").exists())
                self.assertFalse((self.source / "forbidden").exists())

    @unittest.skipUnless(os.environ.get("SAFE_CODE_TEST_BWRAP"), "set SAFE_CODE_TEST_BWRAP for real sandbox checks")
    def test_real_sandbox_creates_only_missing_final_mountpoint(self):
        (self.project / "existing").mkdir()
        (self.source / "content").write_text("reference")
        for launcher in LAUNCHERS:
            for mode in ("ro", "rw"):
                for parent in ("", "existing/"):
                    with self.subTest(launcher=launcher, mode=mode, parent=parent):
                        relative = f"{parent}{launcher}-{mode}"
                        target = self.project / relative
                        commands = f"""
set -e
test "$(cat /workspace/{relative}/content)" = reference
"""
                        if mode == "ro":
                            commands += f"if touch /workspace/{relative}/forbidden 2>/dev/null; then exit 31; fi\n"
                        else:
                            commands += f"printf mounted > /workspace/{relative}/output\n"
                        commands += "exit 0\n"
                        env = {
                            **self.env,
                            "SAFE_OPENCODE_BWRAP": os.environ["SAFE_CODE_TEST_BWRAP"],
                            "SAFE_CLAUDECODE_BWRAP": os.environ["SAFE_CODE_TEST_BWRAP"],
                        }
                        result = subprocess.run(
                            [
                                str(ROOT / launcher), "--dry-run", "--rw",
                                f"--mount-{mode}", f"{self.source}=./{relative}",
                                str(self.project),
                            ],
                            cwd=self.base, env=env, input=commands,
                            capture_output=True, text=True,
                        )
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertTrue(target.is_dir())
                        self.assertEqual(list(target.iterdir()), [])
                        self.assertFalse((self.source / "forbidden").exists())
                        if mode == "rw":
                            self.assertEqual((self.source / "output").read_text(), "mounted")


if __name__ == "__main__":
    unittest.main()

