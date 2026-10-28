import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("build_browser", Path(__file__).with_name("build-browser.py"))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BuilderTests(unittest.TestCase):
    def test_wrong_source_version_rejected_before_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "chrome").mkdir()
            (root / "chrome/VERSION").write_text("MAJOR=155\nMINOR=0\nBUILD=8059\nPATCH=79\n")
            with self.assertRaisesRegex(ValueError, "155.0.8059.84"):
                builder.verify_version(root)

    def test_runtime_contains_components_and_resources_without_build_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output, stage = root / "output", root / "stage"
            output.mkdir()
            for name in ("chrome", "chrome_sandbox", "chrome_crashpad_handler", "libbase.so", "libffmpeg.so.1", "resources.pak", "icudtl.dat", "snapshot_blob.bin", "vk_swiftshader_icd.json", "ninja", "build.ninja", "args.gn", "v8_context_snapshot_generator"):
                (output / name).write_text(name)
            for directory in ("obj", "gen", "locales", "resources"):
                (output / directory).mkdir()
            for name in ("en-US.pak", "ru.pak", "de.pak"):
                (output / "locales" / name).write_text(name)
            (output / "resources/item").write_text("resource")
            (stage / "lib").mkdir(parents=True)
            (stage / "lib/libv4l2.so.0").write_text("previous library")
            builder.stage_runtime(output, stage)
            for name in ("chrome", "libbase.so", "libffmpeg.so.1", "resources.pak", "icudtl.dat", "snapshot_blob.bin", "vk_swiftshader_icd.json", "locales/ru.pak", "locales/en-US.pak", "resources/item", "lib/libv4l2.so.0"):
                self.assertTrue((stage / name).is_file(), name)
            for name in ("obj", "gen", "ninja", "build.ninja", "args.gn", "v8_context_snapshot_generator", "locales/de.pak"):
                self.assertFalse((stage / name).exists(), name)

    def test_rust_allocator_added_to_component_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, target = builder.RUST_ALLOCATOR_COMPONENTS[0]
            gn = root / path
            gn.parent.mkdir(parents=True)
            gn.write_text(target + '\n  configs += webrtc_configs\n}\n')
            runner = builder.Build.__new__(builder.Build)
            runner.source = root
            runner.link_rust_allocator()
            runner.link_rust_allocator()
            self.assertEqual(gn.read_text().count('"//build/rust/allocator"'), 1)

    def test_libm_linked_ahead_of_rust_std_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gn = root / builder.RUST_STD_GN
            gn.parent.mkdir(parents=True)
            gn.write_text("  } else {\n" + builder.RUST_STD_LDFLAGS + "\n      lib_dir = x\n    }\n")
            runner = builder.Build.__new__(builder.Build)
            runner.source = root
            runner.libm_before_rust_std()
            runner.libm_before_rust_std()
            self.assertEqual(gn.read_text().count('ldflags = [ "-lm" ]'), 1)
            self.assertNotIn("ldflags = []", gn.read_text())

    def test_rust_std_linked_after_shared_libraries_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gn = root / builder.RUST_STD_GN
            gn.parent.mkdir(parents=True)
            gn.write_text(builder.RUST_STD_LDFLAGS + "\n      foreach(lib, stdlib_files) {\n"
                          + builder.RUST_STD_FLAGS + "      }\n    }\n")
            runner = builder.Build.__new__(builder.Build)
            runner.source = root
            runner.libm_before_rust_std()
            runner.rust_std_after_shared_libraries()
            runner.rust_std_after_shared_libraries()
            text = gn.read_text()
            self.assertEqual(text.count(builder.RUST_STD_LIBS), 1)
            self.assertEqual(text.count("libs = []"), 1)
            self.assertNotIn("ldflags += [ this_file ]", text)

    def test_v4l2_depends_on_generated_stubs_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gn = root / builder.V4L2_GN
            gn.parent.mkdir(parents=True)
            gn.write_text('source_set("v4l2") {\n' + builder.V4L2_DEPS + '    "//base",\n  ]\n}\n')
            runner = builder.Build.__new__(builder.Build)
            runner.source = root
            runner.depend_on_v4l2_stubs()
            runner.depend_on_v4l2_stubs()
            self.assertEqual(gn.read_text().count('":libv4l2_stubs",'), 1)

    def test_compile_jobs_from_environment(self):
        with mock.patch.dict(builder.os.environ, {"CHROMIUM_JOBS": "3"}):
            self.assertEqual(builder.compile_jobs(), 3)
        with mock.patch.dict(builder.os.environ, {"CHROMIUM_JOBS": "0"}):
            with self.assertRaises(ValueError):
                builder.compile_jobs()

    def test_static_build_rules_and_component_only_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "debian").mkdir()
            rules = root / "debian/rules"
            rules.write_text("defines+=is_official_build=true\n")
            runner = builder.Build.__new__(builder.Build)
            runner.source = root
            with mock.patch.dict(builder.os.environ, {}, clear=True):
                runner.prepare_rules()
                steps = [name for name, _ in runner.steps()]
            self.assertIn("is_component_build=false use_mold=false", rules.read_text())
            self.assertNotIn("rust-allocator", steps)
            self.assertIn("v4l2-stubs", steps)
            with mock.patch.dict(builder.os.environ, {"CHROMIUM_COMPONENT_BUILD": "1"}):
                self.assertIn("rust-std-last", [name for name, _ in runner.steps()])

    def test_compile_pauses_at_deadline_and_records_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = builder.Build.__new__(builder.Build)
            runner.status = builder.Status(root / "build-status.json")
            calls = []
            def command(args, check=True, **_):
                calls.append(args)
                return mock.Mock(returncode=124)
            runner.command = command
            with mock.patch.dict(builder.os.environ, {"CHROMIUM_BUILD_DEADLINE": "1100"}), \
                    mock.patch.object(builder.time, "time", return_value=1000):
                with self.assertRaises(builder.BuildPaused):
                    runner.status.run("compile", runner.compile)
            self.assertEqual(calls[0][:4], ["timeout", "--signal=INT", "--kill-after=300", "100"])
            self.assertEqual(json.loads(runner.status.path.read_text())["state"], "paused")
            with mock.patch.dict(builder.os.environ, {"CHROMIUM_BUILD_DEADLINE": "900"}), \
                    mock.patch.object(builder.time, "time", return_value=1000):
                with self.assertRaises(builder.BuildPaused):
                    runner.compile()
            self.assertEqual(len(calls), 1)

    def test_failed_stage_has_atomic_machine_readable_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = builder.Status(root / "build-status.json")
            def fail():
                raise RuntimeError("compile failed")
            with self.assertRaisesRegex(RuntimeError, "compile failed"):
                status.run("compile", fail)
            value = json.loads(status.path.read_text())
            self.assertEqual(value["stage"], "compile")
            self.assertEqual(value["state"], "failed")
            self.assertEqual(value["error"], "compile failed")
            self.assertIn("updated_at", value)
            self.assertFalse(status.path.with_suffix(".tmp").exists())

    def test_missing_browser_does_not_publish_partial_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir()
            with self.assertRaises(FileNotFoundError):
                builder.stage_runtime(root / "output", root / "stage")
            self.assertFalse((root / "stage").exists())


if __name__ == "__main__":
    unittest.main()
