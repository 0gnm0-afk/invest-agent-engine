import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from invest_agent.cli import main
from test_risk_contract import adoption, position, request


class RiskCliTests(unittest.TestCase):
    def test_policy_position_adoption_status_and_llm_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "request.json"
            def call(command):
                path.write_text(json.dumps(command), encoding="utf-8")
                with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                    code = main(["plan", "policy-event", "--instance", str(root), "--input", str(path)])
                return code, out.getvalue(), err.getvalue()
            code, _, err = call({"operation": "register_position", "actor": "user", "position": position(), "request": request("p")})
            self.assertEqual(code, 0, err)
            code, _, err = call({"operation": "register_position", "actor": "user", "position": position(), "request": request("p")})
            self.assertEqual(code, 0, err)
            code, _, err = call({"operation": "adopt_protection", "actor": "user", "position_id": "p", "request": adoption()})
            self.assertEqual(code, 0, err)
            code, _, _ = call({"operation": "adopt_protection", "actor": "llm", "position_id": "p", "request": adoption("bad", "91")})
            self.assertEqual(code, 1)
            with redirect_stdout(io.StringIO()) as out:
                code = main(["plan", "policy-status", "--instance", str(root)])
            result = json.loads(out.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["positions"][0]["current_protection_price"], "90")
            self.assertEqual(len(result["audit"]), 2)


if __name__ == "__main__":
    unittest.main()
