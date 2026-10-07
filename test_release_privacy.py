"""Ensure privacy checks find leaks inside compressed data, not just filenames."""
import io
import marshal
from pathlib import Path
import struct
import types
import unittest
import zipfile
import zlib

from tools.audit_release_privacy import Auditor
from tools.inno7_payloads import compressed_block, inspect


class ReleasePrivacyTests(unittest.TestCase):
    def auditor(self):
        return Auditor({"workspace": r"Q:\PrivateBuild\ExampleProject", "username": "ExampleBuilder"})

    def test_utf16_and_escaped_paths_are_found_without_exposing_the_value(self):
        for text, encoding in [(r"Q:\PrivateBuild\ExampleProject\file", "utf-16le"),
                               (r"q:/privatebuild/exampleproject/file", "utf-8"),
                               (r"Q:\\PrivateBuild\\ExampleProject\\file", "utf-8")]:
            audit = self.auditor()
            audit.blob(text.encode(encoding), "fixture")
            self.assertTrue(audit.findings)
            self.assertNotIn("PrivateBuild", str(audit.findings))

    def test_username_word_boundary_preserves_distinct_public_handle(self):
        audit = self.auditor()
        audit.blob(b"https://example.org/ExampleBuilderXC", "public")
        self.assertFalse(audit.findings)
        audit.blob(b"C:\\Users\\ExampleBuilder\\settings.json", "private")
        self.assertTrue(audit.findings)

    def test_public_url_exception_does_not_hide_the_same_name_elsewhere(self):
        url = "https://github.com/ExampleBuilder/project"
        audit = Auditor({"hostname": "ExampleBuilder"}, [url])
        audit.blob((url + "/issues").encode(), "public")
        self.assertFalse(audit.findings)
        audit.blob((url + " machine=ExampleBuilder").encode(), "private")
        self.assertTrue(audit.findings)

    def test_nested_pyc_code_filename_in_zip_is_scanned(self):
        payload = compile("def nested():\n return 1\n", "Q:/PrivateBuild/ExampleProject/module.py", "exec")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("module.pyc", b"\0" * 16 + marshal.dumps(payload))
        audit = self.auditor()
        audit.blob(buffer.getvalue(), "fixture.zip")
        self.assertEqual(audit.counts["python_code_objects"], 2)
        self.assertTrue(any(item["issue"] == "absolute Python code filename" for item in audit.findings))

    def test_png_metadata_is_rejected_even_without_a_known_identifier(self):
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        image = b"\x89PNG\r\n\x1a\n" + chunk(b"tEXt", b"Author\0Private Author") + chunk(b"IEND", b"")
        audit = self.auditor()
        audit.blob(image, "fixture.png")
        self.assertTrue(audit.findings)

    def test_inno_checksum_failure_cannot_report_success(self):
        body = b"text"
        header = struct.pack("<QB", len(body) + 4, 0)
        block = struct.pack("<I", zlib.crc32(header)) + header + struct.pack("<I", zlib.crc32(body)) + body
        self.assertEqual(compressed_block(block, 0)[0], body)
        with self.assertRaisesRegex(ValueError, "CRC mismatch"):
            compressed_block(block[:-1] + b"X", 0)
        with self.assertRaises(ValueError):
            inspect(b"MZunsupported")


if __name__ == "__main__":
    unittest.main()
