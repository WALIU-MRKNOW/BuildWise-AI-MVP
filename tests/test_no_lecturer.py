import pathlib
import unittest


class RemovedModeTests(unittest.TestCase):
    def test_source_and_docs_have_no_removed_mode(self) -> None:
        root = pathlib.Path(__file__).parents[1]
        scanned = list((root / "bot").rglob("*.py"))
        scanned += [root / "README.md", root / ".env.example"]
        forbidden = "lect" + "urer"
        for path in scanned:
            self.assertNotIn(forbidden, path.read_text(encoding="utf-8").lower(), str(path))


if __name__ == "__main__":
    unittest.main()
