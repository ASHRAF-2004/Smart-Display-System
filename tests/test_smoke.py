"""Small repository smoke checks. Browser and Google flows need separate testing."""
import ast
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class RepositorySmoke(unittest.TestCase):
    def test_python_modules_parse(self):
        for path in ("app.py", "security.py", "scripts/first_run.py"):
            ast.parse((ROOT / path).read_text(encoding="utf-8"))

    def test_neutral_page(self):
        page = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("YOUR LOGO", page)
        self.assertNotIn("your-uni.com", page.lower())

    def test_no_default_staff_accounts(self):
        import sys
        sys.path.insert(0, str(ROOT))
        import security
        self.assertEqual(security.SEEDED_EDITORS, {})

    def test_first_run_config_validation(self):
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        from first_run import valid_origin
        self.assertTrue(valid_origin("https://screen.example.com"))
        self.assertFalse(valid_origin("https://screen.example.com/path"))
        self.assertFalse(valid_origin("http://screen.example.com"))
        self.assertTrue(valid_origin("http://127.0.0.1:8787"))


if __name__ == "__main__":
    unittest.main()
