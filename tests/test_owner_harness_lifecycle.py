"""Real harness control flow with a fake server, in a bounded isolated child."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r"""
import runpy, sys
from pathlib import Path
root = Path(sys.argv[1])
mode = sys.argv[2]
sys.argv = [str(root / "tests/owner_uat_mock_harness.py"), str(root)] + [str(root / "_tmp/harness-lifecycle-fixture")] * 7
loaded = runpy.run_path(str(root / "tests/owner_uat_mock_harness.py"), run_name="synthetic_harness")
namespace = loaded["main"].__globals__
namespace["configure_auth_env"] = lambda: None
class FakeServer:
    server_address = ("127.0.0.1", 1)
    def __init__(self, *args):
        pass
    def serve_forever(self):
        if mode == "raise":
            raise RuntimeError("SYNTHETIC_SERVING_FAILURE")
    def server_close(self):
        print("SYNTHETIC_SERVER_CLOSED", flush=True)
namespace["webapp"].ThreadingHTTPServer = FakeServer
result = namespace["main"]()
if result != 0:
    raise AssertionError("Unexpected normal harness result")
print("SYNTHETIC_NORMAL_RETURN", flush=True)
"""


class OwnerHarnessLifecycleTest(unittest.TestCase):
    def child(self, mode):
        env = {key:os.environ[key] for key in ("PATH","SystemRoot","COMSPEC","TEMP","TMP") if key in os.environ}
        env.update(APP_MODE="local", SQAG_DISABLE_DOTENV="1", PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        return subprocess.run([sys.executable,"-c",DRIVER,str(ROOT),mode],cwd=ROOT,env=env,
                              capture_output=True,text=True,encoding="utf-8",timeout=20)

    def test_normal_serving_return_closes_once_and_exits_zero(self):
        result = self.child("normal")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.count("SYNTHETIC_SERVER_CLOSED"),1)
        self.assertIn("SYNTHETIC_NORMAL_RETURN",result.stdout)
        self.assertNotIn("SYNTHETIC_SERVING_FAILURE",result.stderr)

    def test_serving_exception_closes_once_and_preserves_nonzero_exit(self):
        result = self.child("raise")
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(result.stdout.count("SYNTHETIC_SERVER_CLOSED"),1)
        self.assertIn("RuntimeError: SYNTHETIC_SERVING_FAILURE",result.stderr)
        self.assertNotIn("SYNTHETIC_NORMAL_RETURN",result.stdout)
