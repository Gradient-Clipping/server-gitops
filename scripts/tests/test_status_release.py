import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import unittest

spec = importlib.util.spec_from_file_location("release", Path(__file__).parents[1] / "publish-smart-shop-recovery.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class SourceValidationTests(unittest.TestCase):
    def check(self, name="backend/main.py", data=b"verified", expected_data=b"verified", revision="a"*40, link=False):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT, pax_headers={"comment": revision}) as archive:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            if link:
                member.type = tarfile.SYMTYPE
                member.linkname = "/etc/passwd"
            archive.addfile(member, io.BytesIO(data))
        buffer.seek(0)
        digest = hashlib.sha1(b"blob " + str(len(expected_data)).encode() + b"\0" + expected_data).hexdigest()
        with tarfile.open(fileobj=buffer, mode="r:") as archive:
            release.validate_archive(archive, "a"*40, {"backend/main.py": digest})

    def test_exact_source(self):
        self.check()

    def test_modified_source(self):
        with self.assertRaises(ValueError):
            self.check(data=b"modified")

    def test_wrong_revision(self):
        with self.assertRaises(ValueError):
            self.check(revision="b"*40)

    def test_traversal(self):
        with self.assertRaises(ValueError):
            self.check(name="../main.py")

    def test_symlink(self):
        with self.assertRaises(ValueError):
            self.check(link=True)

    def test_unchanged_runtime_can_be_reused(self):
        source = {path: "same" for path in release.RUNTIME_INPUTS}
        release.compatible_runtime(source, source)

    def test_changed_dependency_requires_full_build(self):
        source = {path: "same" for path in release.RUNTIME_INPUTS}
        baseline = {**source, "backend/requirements.txt": "old"}
        with self.assertRaises(ValueError):
            release.compatible_runtime(source, baseline)
