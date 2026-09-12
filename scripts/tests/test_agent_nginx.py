import contextlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap_agent as host


class AgentNginxTests(unittest.TestCase):
    def test_nginx_mode_never_reads_credentials_installs_runtime_or_provisions_data(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(host, "STATE", Path(temporary)))
            stack.enter_context(patch.object(host.os, "geteuid", return_value=0, create=True))
            stack.enter_context(patch.object(host.os, "uname", return_value=SimpleNamespace(machine="x86_64"), create=True))
            stack.enter_context(patch.object(sys, "argv", ["bootstrap", "--nginx-only"]))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            for method in ("read_env", "install_runtime", "provision", "copy_managed"):
                stack.enter_context(patch.object(host, method, side_effect=AssertionError("Unexpected broader bootstrap action")))
            configure = stack.enter_context(patch.object(host, "configure_nginx"))
            host.main()
            configure.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
