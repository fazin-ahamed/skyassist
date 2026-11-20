import json
import os
import socket
import subprocess
import sys
from contextlib import closing, contextmanager
from time import monotonic, sleep

import httpx

from skyassist.db import connect, init_db
from test_assist import seed, trace
from test_service import create_journey, request_body


def wait_until(check, timeout=8):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        result = check()
        if result:
            return result
        sleep(0.025)
    raise AssertionError("Service did not reach the expected state")


@contextmanager
def uvicorn(env, port, log):
    with log.open("a+") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "skyassist.service:app_from_env", "--factory",
             "--host", "127.0.0.1", "--port", str(port)],
            env=env, stdout=output, stderr=output,
        )
        try:
            yield process
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_second_process_approval_and_dispatch(tmp_path):
    db = tmp_path / "subprocess.sqlite3"
    init_db(db)
    with closing(connect(db)) as conn:
        seed(conn)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    log = tmp_path / "uvicorn.log"
    env = os.environ | {"SKYASSIST_MODE": "demo", "SKYASSIST_DB_PATH": str(db),
                        "SKYASSIST_SERVICE_URL": url, "SKYASSIST_COORDINATOR_TOKEN": "test-coordinator",
                        "SKYASSIST_INTEGRATION_TOKEN": "test-integration"}

    def run_staff(command, case_ref, event_id):
        result = subprocess.run(
            [sys.executable, "-m", "skyassist.staff_client", command,
             "--case", case_ref, "--event-id", event_id],
            env=env, capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        return json.loads(result.stdout)

    with httpx.Client(base_url=url, timeout=1, trust_env=False) as passenger:
        def ready(process):
            assert process.poll() is None, log.read_text()
            try:
                return passenger.get("/openapi.json").is_success
            except httpx.TransportError:
                return False

        def notification_sent():
            with closing(connect(db)) as conn:
                return conn.execute("SELECT 1 FROM outbox WHERE effect='coordinator_notification' "
                                    "AND status='done'").fetchone()

        with uvicorn(env, port, log) as process:
            wait_until(lambda: ready(process))
            journey, headers = create_journey(passenger)
            journey_url = f"/journeys/{journey}"
            created = passenger.post(f"{journey_url}/assistance", headers=headers, json=request_body(journey))
            assert created.status_code == 200, created.text
            case = created.json()
            assert case["state"] == "pending_approval"
            wait_until(notification_sent)

        with uvicorn(env, port, log) as process:
            wait_until(lambda: ready(process))

            def state():
                return passenger.get(journey_url, headers=headers).json()["view"]["assistance"]["state"]

            assert state() == "pending_approval"
            approved = run_staff("approve", case["case_ref"], "second-process-approval")
            assert approved["state"] == "approved" and approved["outcome"] == "applied"
            assert state() == "approved"

            def handoff_sent():
                with closing(connect(db)) as conn:
                    row = conn.execute("SELECT status FROM outbox WHERE effect='mobility_dispatch'").fetchone()
                    return row and row[0] == "sent"

            wait_until(handoff_sent)
            assert state() == "approved"
            assert run_staff("dispatch", case["case_ref"], "second-process-dispatch")["state"] == "dispatched"
            assert state() == "dispatched"
            replay = run_staff("approve", case["case_ref"], "second-process-approval")
            assert replay["outcome"] == "replayed" and replay["state"] == "approved"
            assert state() == "dispatched"

    with closing(connect(db)) as conn:
        history = trace(conn)
        assert history == [
            ("assistance_requested", "pending_approval"), ("coordinator_notification", "available"),
            ("decision_approve", "applied"), ("dispatch_claimed", "in_flight"),
            ("mobility_dispatch", "accepted"), ("dispatch_outcome", "accepted"),
            ("dispatch_dispatched", "applied"),
        ]
        assert conn.execute("SELECT count(*) FROM staff_decisions").fetchone()[0] == 1
        assert conn.execute("SELECT attempts FROM outbox WHERE effect='mobility_dispatch'").fetchone()[0] == 1
