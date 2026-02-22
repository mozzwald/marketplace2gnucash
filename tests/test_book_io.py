from __future__ import annotations

import gzip
import tempfile
from pathlib import Path
import unittest

from market2gnucash.core.book_io import load_book_info


_XML_MINIMAL = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<gnc-v2>
  <book>
    <account>
      <name>Root Account</name>
      <id>root-guid-1</id>
      <type>ROOT</type>
    </account>
    <account>
      <name>Assets</name>
      <id>asset-guid-1</id>
      <type>ASSET</type>
      <parent>root-guid-1</parent>
    </account>
  </book>
</gnc-v2>
"""


class BookIoTests(unittest.TestCase):
    def test_load_book_info_from_gzip_xml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "test.gnucash"
            with gzip.open(path, "wb") as handle:
                handle.write(_XML_MINIMAL.encode("utf-8"))

            info = load_book_info(path)

            self.assertEqual(info.book_id, "root-guid-1")
            self.assertEqual(info.path, str(path))
            account_names = {account.full_name for account in info.accounts}
            self.assertIn("Assets", account_names)


if __name__ == "__main__":
    unittest.main()
