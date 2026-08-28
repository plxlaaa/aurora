"""skills 文件管理与 zip 导入单元测试。"""
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.skills import SkillsManager  # noqa: E402


def make_zip(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


MANIFEST = "name: demo_skill\ndescription: 演示技能\nentry: run.py\n"


class SkillTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mgr = SkillsManager(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_skill(self, name="demo_skill", manifest=MANIFEST, files=None):
        root = Path(self.tmp.name) / name
        root.mkdir(parents=True, exist_ok=True)
        (root / "manifest.yaml").write_text(manifest, encoding="utf-8")
        for rel, content in (files or {}).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        self.mgr.reload()
        return root


class TestZipImport(SkillTestBase):
    def test_import_root_manifest(self):
        data = make_zip(
            {
                "manifest.yaml": MANIFEST,
                "run.py": "print(1)",
                "SKILL.md": "# 用法",
            }
        )
        result = self.mgr.import_zip(data)
        self.assertEqual(result["name"], "demo_skill")
        self.assertEqual(result["files"], 3)
        self.assertIn("demo_skill", {s["name"] for s in self.mgr.list()})

    def test_import_prefixed_manifest(self):
        data = make_zip(
            {
                "demo_skill/manifest.yaml": MANIFEST,
                "demo_skill/run.py": "print(1)",
                "demo_skill/sub/helper.py": "x = 1",
            }
        )
        result = self.mgr.import_zip(data)
        self.assertEqual(result["files"], 3)
        files = self.mgr.list_files("demo_skill")
        paths = {f["path"] for f in files}
        self.assertIn("sub/helper.py", paths)

    def test_import_missing_manifest_rejected(self):
        data = make_zip({"run.py": "print(1)"})
        with self.assertRaises(ValueError):
            self.mgr.import_zip(data)

    def test_import_bad_name_rejected(self):
        data = make_zip({"manifest.yaml": "description: 无名\n"})
        with self.assertRaises(ValueError):
            self.mgr.import_zip(data)

    def test_import_slip_rejected_rolls_back(self):
        self.write_skill(files={"SKILL.md": "旧内容"})
        data = make_zip(
            {
                "manifest.yaml": MANIFEST,
                "../escape.py": "evil",
            }
        )
        with self.assertRaises(ValueError):
            self.mgr.import_zip(data)
        # 原内容保留（回滚）
        self.assertIn("demo_skill", {s["name"] for s in self.mgr.list()})
        self.assertEqual(self.mgr.read_file("demo_skill", "SKILL.md"), "旧内容")

    def test_import_overwrites_with_reload(self):
        self.write_skill(files={"SKILL.md": "v1"})
        data = make_zip({"manifest.yaml": MANIFEST, "SKILL.md": "v2", "run.py": "print(1)"})
        self.mgr.import_zip(data)
        self.assertEqual(self.mgr.read_file("demo_skill", "SKILL.md"), "v2")


class TestFileManagement(SkillTestBase):
    def test_list_read_write(self):
        self.write_skill(files={"SKILL.md": "# doc", "sub/a.py": "print('a')"})
        files = self.mgr.list_files("demo_skill")
        paths = {f["path"] for f in files}
        self.assertEqual(paths, {"manifest.yaml", "SKILL.md", "sub/a.py"})

        content = self.mgr.read_file("demo_skill", "sub/a.py")
        self.assertEqual(content, "print('a')")

        self.mgr.write_file("demo_skill", "sub/b.md", "新增文档")
        self.assertEqual(self.mgr.read_file("demo_skill", "sub/b.md"), "新增文档")

    def test_path_escape_blocked(self):
        self.write_skill()
        with self.assertRaises(ValueError):
            self.mgr.read_file("demo_skill", "../other/secret.txt")
        with self.assertRaises(ValueError):
            self.mgr.write_file("demo_skill", "../../evil.py", "x=1")

    def test_binary_read_blocked(self):
        self.write_skill(files={"data.bin": "whatever"})
        with self.assertRaises(ValueError):
            self.mgr.read_file("demo_skill", "data.bin")

    def test_delete(self):
        self.write_skill()
        self.assertTrue(self.mgr.delete("demo_skill"))
        self.assertNotIn("demo_skill", {s["name"] for s in self.mgr.list()})
        self.assertFalse(self.mgr.delete("demo_skill"))


class TestReadSkill(SkillTestBase):
    def test_read_skill_full(self):
        self.write_skill(
            manifest=MANIFEST + "parameters:\n- name: city\n  type: string\n  required: true\n",
            files={"SKILL.md": "# 使用说明\n先选城市。"},
        )
        info = self.mgr.read_skill("demo_skill")
        self.assertEqual(info["name"], "demo_skill")
        self.assertEqual(info["description"], "演示技能")
        self.assertIn("使用说明", info["doc"])
        self.assertEqual(info["parameters"][0]["name"], "city")

    def test_read_skill_missing(self):
        self.write_skill()
        info = self.mgr.read_skill("no_such")
        self.assertIn("error", info)
        self.assertIn("demo_skill", info["error"])


if __name__ == "__main__":
    unittest.main()
