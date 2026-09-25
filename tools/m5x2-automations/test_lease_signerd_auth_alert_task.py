"""The 2FA-reminder task the daemon spawns must carry the name the user
actually works from (renamed 2026-09-25 from the long "Lease auto-signer
needs 2FA re-login — ... --login) [10]" string): the way it gets done is a
Screen Share into Ix, so the task says that, and it keeps its [10] value so
/did credits it."""
import ast
from pathlib import Path

SRC = (Path(__file__).resolve().parent / "lease_signerd.py").read_text()


def _const(name: str) -> str:
    tree = ast.parse(SRC)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found")


def test_auth_alert_task_name():
    assert _const("AUTH_ALERT_TASK") == "do 2fa with Ix to refresh appfolio cookie [10]"


def test_create_task_uses_the_constant():
    # The literal must not creep back into the create_task call.
    assert "_todoist.create_task(\n            AUTH_ALERT_TASK," in SRC
    assert "Lease auto-signer needs 2FA re-login" not in SRC.split("def maybe_send_auth_alert")[1].split("def _clear_auth_alert")[0]
