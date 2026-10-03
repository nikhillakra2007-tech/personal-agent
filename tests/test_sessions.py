"""V2-06: opt-in persistent browser session profiles.

Playwright's persistent context already keeps cookies/storage
opaquely inside its profile directory — V2-06 adds only explicit
identity: --session-profile NAME resolves under one Lakra-
controlled sessions root (validated names, never paths), while the
default behavior stays exactly V1 (ephemeral profile directory,
unchanged). Queue items carry the identity in-leg (no schema
migration); the daemon rotates sessions per item and never attaches
a profile silently.

State round-trips run over a test-local 127.0.0.1 HTTP server (no
real credentials anywhere): a setter page writes one cookie + one
localStorage entry, a reader page renders them into DOM text, and
the observe road verifies — across fresh OS processes sharing one
named profile.
"""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lakra.control.profiles import (  # noqa: E402
    ProfileRefused,
    clear_profile,
    resolve_profile,
    validate_profile_name,
)

FIXTURES = Path(__file__).parent / "fixtures"
COURSES_URL = (FIXTURES / "courses.html").as_uri()
FORM_URL = COURSES_URL
FORM_SLOTS = {"name": {"text": "Ada"}}

SETTER_BODY = (
    "<html><body>setter ready"
    "<script>"
    # max-age: session (memory-only) cookies never reach disk by
    # browser design, so the round-trip uses a persistent cookie.
    'document.cookie = "lakra_state=alpha-7; path=/; max-age=3600";'
    'localStorage.setItem("lakra_note", "beta-9");'
    "</script></body></html>")
READER_BODY = (
    "<html><body>reader ready"
    '<div id="state">empty</div>'
    "<script>"
    'document.getElementById("state").textContent ='
    ' document.cookie + "|" + localStorage.getItem("lakra_note");'
    "</script></body></html>")
STATE_EXPECT = "lakra_state=alpha-7|beta-9"


class _StateHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/setter.html":
            body = SETTER_BODY.encode()
        elif self.path == "/reader.html":
            body = READER_BODY.encode()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def state_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StateHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=30)


def _domain(base):
    return base.split("://", 1)[1].split("/", 1)[0]


# -- names ---------------------------------------------------------------------

def test_validate_names():
    assert validate_profile_name("alpha") == "alpha"
    assert validate_profile_name("Alpha") == "alpha"  # one identity
    assert validate_profile_name("a1-_x") == "a1-_x"
    assert validate_profile_name("x" * 64) == "x" * 64
    for bad in ("", ".", "..", "../x", "a/b", "a\\b", "/abs", "C:\\w",
                "a b", "a*b", "a?", ".hidden", "-lead", "_lead",
                "x" * 65, "caf\u00e9", "a:b"):
        with pytest.raises(ProfileRefused):
            validate_profile_name(bad)
    for bad in (None, 123, ["a"], b"a"):
        with pytest.raises(ProfileRefused):
            validate_profile_name(bad)


def test_resolve_deterministic_and_confined(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    first = resolve_profile(root, "Alpha")
    assert first == resolve_profile(root, "alpha")
    assert first.parent == root
    assert first != resolve_profile(root, "beta")
    assert not first.exists()  # resolution creates nothing
    for bad in ("../evil", "/abs", "a/b"):
        with pytest.raises(ProfileRefused):
            resolve_profile(root, bad)


def test_resolve_symlink_escape_where_supported(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()
    link = root / "sneaky"
    try:
        link.symlink_to(tmp_path / "elsewhere",
                        target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need privilege on this host")
    with pytest.raises(ProfileRefused):
        resolve_profile(root, "sneaky")


def test_clear_profile(tmp_path):
    root = tmp_path / "sessions"
    target = resolve_profile(root, "gone")
    target.mkdir(parents=True)
    (target / "junk.txt").write_text("x", encoding="utf-8")
    assert clear_profile(root, "gone") == target
    assert not target.exists()
    with pytest.raises(ProfileRefused):
        clear_profile(root, "gone")  # unknown refuses, never silent
    with pytest.raises(ProfileRefused):
        clear_profile(root, "../evil")


def test_item_session_profile_identity():
    sys.path.insert(0, str(ROOT / "scripts"))
    import lakra_do
    legs = [{"road": "observe", "url": "file:///a",
             "expect_text": "A", "session_profile": "Alpha"},
            {"road": "observe", "url": "file:///b",
             "expect_text": "B", "session_profile": "alpha"}]
    assert lakra_do.item_session_profile(legs) == "alpha"
    assert lakra_do.item_session_profile(
        [{"road": "observe", "url": "file:///a",
          "expect_text": "A"}]) is None
    assert lakra_do.item_session_profile([]) is None
    with pytest.raises(ProfileRefused):
        lakra_do.item_session_profile([
            {"road": "observe", "url": "file:///a", "expect_text": "A",
             "session_profile": "one"},
            {"road": "observe", "url": "file:///b", "expect_text": "B",
             "session_profile": "two"}])
    with pytest.raises(ProfileRefused):
        lakra_do.item_session_profile([
            {"road": "observe", "url": "file:///a", "expect_text": "A",
             "session_profile": "../evil"}])
    with pytest.raises(ProfileRefused):
        lakra_do.item_session_profile([
            {"road": "observe", "url": "file:///a", "expect_text": "A",
             "session_profile": 7}])


def test_queue_legs_carry_profile_without_migration():
    # Extra leg keys validate (chain contract) and route untouched
    # (dispatcher sub-filters by road keys): identity rides along
    # with no schema change.
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control.chain import validate_chain
    legs = validate_chain({"legs": [
        {"road": "observe", "url": "file:///a", "expect_text": "A",
         "session_profile": "alpha"},
        {"road": "observe", "url": "file:///b",
         "expect_text": "B"}]})
    assert legs[0]["session_profile"] == "alpha"
    assert "session_profile" not in legs[1]


def test_queue_round_trip_preserves_profile(tmp_path):
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control import work_queue as wq
    from lakra.control.store import Database
    db = Database(tmp_path / "q.db")
    try:
        item = wq.enqueue(db, legs=[
            {"road": "observe", "url": "file:///a", "expect_text": "A",
             "session_profile": "alpha"},
            {"road": "observe", "url": "file:///b",
             "expect_text": "B", "session_profile": "alpha"}])
        back = wq.get(db, item["work_id"])
        assert back["legs_json"] == item["legs_json"]
        mat = wq.materialize(back)
        assert mat["legs"][0]["session_profile"] == "alpha"
        sys.path.insert(0, str(ROOT / "scripts"))
        import lakra_do
        assert lakra_do.item_session_profile(
            mat["legs"]) == "alpha"
        # Schema v6 unchanged: no profile column was needed.
        cols = [r[1] for r in db.execute(
            "PRAGMA table_info(work_items)").fetchall()]
        assert "session_profile" not in cols
        assert db.execute(
            "SELECT value FROM meta WHERE key='v'").fetchone()[0] \
            == "6"
    finally:
        db.close()


# -- CLI plumbing (no browser) -----------------------------------------------------

def test_cli_flag_matrix():
    sys.path.insert(0, str(ROOT / "scripts"))
    import lakra_do
    assert lakra_do.parse_args(
        ["--road", "observe", "--url", "file:///x", "--text", "t",
         "--expect", "e", "--session-profile", "alpha",
         "--sessions-dir", str(ROOT)])["session_profile"] == "alpha"
    name, target, reused = lakra_do.select_session(
        {"session_profile": "Alpha", "profile_dir": None}, "/tmp/p")
    assert (name, reused) == ("alpha", False)
    assert target == Path(ROOT / "var" / "sessions" / "alpha")
    name, target, reused = lakra_do.select_session(
        {"session_profile": None, "profile_dir": None}, "/tmp/p")
    assert (name, target, reused) == (None, Path("/tmp/p"), False)
    with pytest.raises(lakra_do.UsageError):
        lakra_do.select_session(
            {"session_profile": "a", "profile_dir": "/tmp/x"},
            "/tmp/p")
    with pytest.raises(lakra_do.UsageError):
        lakra_do.select_session(
            {"session_profile": "../evil", "profile_dir": None},
            "/tmp/p")


# -- live CLI --------------------------------------------------------------------------

def _env(tmp_path, db="cli.db"):
    env = dict(os.environ)
    env["LAKRA_DB"] = str(tmp_path / db)
    return env


def _base(tmp_path):
    # No --profile-dir here: --profile-dir and --session-profile are
    # mutually exclusive by design, so ephemeral call sites add their
    # own --profile-dir explicitly below.
    return [sys.executable, str(ROOT / "scripts" / "lakra_do.py"),
            "--audit", str(tmp_path / "audit-do.jsonl"),
            "--shots-dir", str(tmp_path / "shots"),
            "--sessions-dir", str(tmp_path / "sessions"),
            "--ram-floor-mb", "0", "--cpu-ceiling", "100"]


def _prof(tmp_path, name):
    return ["--profile-dir", str(tmp_path / name)]


def _session_lines(stdout):
    out = {}
    for line in stdout.splitlines():
        if line.startswith("session profile: "):
            out["profile"] = line.split(": ", 1)[1]
        if line.startswith("session reused: "):
            out["reused"] = line.split(": ", 1)[1]
    return out


def test_cli_ephemeral_by_default(tmp_path, state_server):
    env = _env(tmp_path)
    url = f"{state_server}/setter.html"
    proc = subprocess.run(
        _base(tmp_path) + _prof(tmp_path, "eph")
        + ["--road", "observe", "--url", url,
           "--text", "setter", "--expect",
           "setter ready", "--allow-domain",
           _domain(state_server), "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "session profile:" not in proc.stdout  # V1 output exact
    # The named-profile root is untouched by default runs.
    assert not (tmp_path / "sessions").exists()


def test_cli_profile_create_and_reuse(tmp_path, state_server):
    env = _env(tmp_path)
    url = f"{state_server}/setter.html"
    dom = _domain(state_server)
    first = subprocess.run(
        _base(tmp_path) + ["--road", "observe", "--url", url,
                                 "--text", "setter", "--expect",
                                 "setter ready", "--allow-domain", dom,
                                 "--session-profile", "Alpha", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert first.returncode == 0, first.stdout + first.stderr
    assert _session_lines(first.stdout) == {"profile": "alpha",
                                            "reused": "no"}
    assert (tmp_path / "sessions" / "alpha").is_dir()
    second = subprocess.run(
        _base(tmp_path) + ["--road", "observe", "--url", url,
                                 "--text", "setter", "--expect",
                                 "setter ready", "--allow-domain", dom,
                                 "--session-profile", "alpha", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert second.returncode == 0, second.stdout + second.stderr
    assert _session_lines(second.stdout) == {"profile": "alpha",
                                             "reused": "yes"}


def test_cli_state_survives_fresh_processes(tmp_path, state_server):
    env = _env(tmp_path)
    dom = _domain(state_server)
    prime = subprocess.run(
        _base(tmp_path) + ["--road", "observe", "--url",
                                 f"{state_server}/setter.html",
                                 "--text", "setter", "--expect",
                                 "setter ready", "--allow-domain", dom,
                                 "--session-profile", "stateful",
                                 "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert prime.returncode == 0, prime.stdout + prime.stderr
    reader = subprocess.run(
        _base(tmp_path) + ["--road", "observe", "--url",
                                 f"{state_server}/reader.html",
                                 "--text", "reader", "--expect",
                                 STATE_EXPECT, "--allow-domain", dom,
                                 "--session-profile", "stateful",
                                 "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert reader.returncode == 0, reader.stdout + reader.stderr
    assert "status: DONE" in reader.stdout
    assert _session_lines(reader.stdout)["reused"] == "yes"


def test_cli_ephemeral_does_not_reuse(tmp_path, state_server):
    env = _env(tmp_path)
    dom = _domain(state_server)
    prime = subprocess.run(
        _base(tmp_path) + ["--road", "observe", "--url",
                                 f"{state_server}/setter.html",
                                 "--text", "setter", "--expect",
                                 "setter ready", "--allow-domain", dom,
                                 "--session-profile", "lonely", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert prime.returncode == 0, prime.stdout + prime.stderr
    reader = subprocess.run(
        _base(tmp_path) + _prof(tmp_path, "e2")
        + ["--road", "observe", "--url",
           f"{state_server}/reader.html",
           "--text", "reader", "--expect",
           STATE_EXPECT, "--allow-domain", dom,
           "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert reader.returncode == 1, reader.stdout + reader.stderr
    assert "status: STOPPED" in reader.stdout
    assert "session profile:" not in reader.stdout


def test_cli_profiles_isolated(tmp_path, state_server):
    env = _env(tmp_path)
    dom = _domain(state_server)
    prime = subprocess.run(
        _base(tmp_path) + ["--road", "observe", "--url",
                                 f"{state_server}/setter.html",
                                 "--text", "setter", "--expect",
                                 "setter ready", "--allow-domain", dom,
                                 "--session-profile", "profile-a",
                                 "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert prime.returncode == 0, prime.stdout + prime.stderr
    other = subprocess.run(
        _base(tmp_path) + ["--road", "observe", "--url",
                                 f"{state_server}/reader.html",
                                 "--text", "reader", "--expect",
                                 STATE_EXPECT, "--allow-domain", dom,
                                 "--session-profile", "profile-b",
                                 "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert other.returncode == 1, other.stdout + other.stderr
    assert "status: STOPPED" in other.stdout
    assert (tmp_path / "sessions" / "profile-a").is_dir()
    assert (tmp_path / "sessions" / "profile-b").is_dir()


def test_cli_invalid_profile_refused(tmp_path):
    env = _env(tmp_path)
    for name in ("../evil", "/abs", "a/b", "", "x" * 65):
        proc = subprocess.run(
            _base(tmp_path) + ["--road", "observe", "--url",
                                      COURSES_URL, "--text", "t",
                                      "--expect", "e",
                                      "--session-profile", name,
                                      "--yes"],
            capture_output=True, text=True, cwd=str(ROOT), env=env,
            timeout=120)
        assert proc.returncode == 1, (name, proc.stdout + proc.stderr)
        assert "usage error" in proc.stdout, (name, proc.stdout)
    both = subprocess.run(
        _base(tmp_path) + ["--road", "observe", "--url",
                                   COURSES_URL, "--text", "t",
                                   "--expect", "e", "--session-profile",
                                   "alpha", "--profile-dir",
                                   str(tmp_path / "custom")],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=120)
    assert both.returncode == 1 and "usage error" in both.stdout


def test_cli_clear_session(tmp_path):
    env = _env(tmp_path)
    target = tmp_path / "sessions" / "doomed"
    target.mkdir(parents=True)
    (target / "junk.txt").write_text("x", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "lakra_do.py"),
         "--clear-session-profile", "doomed",
         "--sessions-dir", str(tmp_path / "sessions")],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "cleared session profile: doomed" in proc.stdout
    assert not target.exists()
    missing = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "lakra_do.py"),
         "--clear-session-profile", "doomed",
         "--sessions-dir", str(tmp_path / "sessions")],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=120)
    assert missing.returncode == 1
    evil = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "lakra_do.py"),
         "--clear-session-profile", "../evil",
         "--sessions-dir", str(tmp_path / "sessions")],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=120)
    assert evil.returncode == 1 and "refused" in evil.stdout
    mixed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "lakra_do.py"),
         "--clear-session-profile", "doomed", "--road", "observe",
         "--url", COURSES_URL, "--sessions-dir",
         str(tmp_path / "sessions")],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=120)
    assert mixed.returncode == 1 and "usage error" in mixed.stdout


def test_cli_password_safety_with_profile(tmp_path):
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path) + ["--road", "observe", "--url",
                                 COURSES_URL, "--text", "courses",
                                 "--expect", "Thermodynamics",
                                 "--session-profile", "pwcheck", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "s3cr3t-nope" not in proc.stdout  # never extracted/printed
    assert _session_lines(proc.stdout)["profile"] == "pwcheck"


def test_cli_no_secret_logging_and_no_token_leak(tmp_path,
                                                 state_server):
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control.store import Database as D
    env = _env(tmp_path)
    dom = _domain(state_server)
    proc = subprocess.run(
        _base(tmp_path) + ["--road", "observe", "--url",
                                  f"{state_server}/setter.html",
                                  "--text", "setter", "--expect",
                                  "setter ready", "--allow-domain", dom,
                                  "--session-profile", "leakcheck",
                                  "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "session profile: leakcheck" in proc.stdout
    assert "alpha-7" not in proc.stdout  # state values never printed
    assert "beta-9" not in proc.stdout
    profdir = tmp_path / "sessions" / "leakcheck"
    assert profdir.is_dir()
    d = D(tmp_path / "cli.db")
    try:
        secrets = [r[0] for r in d.execute(
            "SELECT task_id FROM tasks").fetchall()]
        secrets += [r[0] for r in d.execute(
            "SELECT approval_id FROM approvals").fetchall()]
        try:
            secrets += [r[0] for r in d.execute(
                "SELECT token_id FROM tokens").fetchall()]
        except Exception:
            pass
    finally:
        d.close()
    blob = b""
    for p in profdir.rglob("*"):
        if p.is_file():
            try:
                blob += p.read_bytes()
            except OSError:
                pass
    text = blob.decode("utf-8", errors="ignore")
    assert "s3cr3t-nope" not in text
    for secret in secrets:
        assert secret not in text  # ids are Lakra-side, never stored
        assert secret not in proc.stdout


def _wait_park(audit_path, kind="browser.submit", timeout_s=150):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            lines = Path(audit_path).read_text(
                encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in reversed(lines):
            if '"ACTION_REQUIRES_APPROVAL"' in line \
                    and f'"{kind}"' in line:
                return True
        time.sleep(2)
    return False


def test_cli_concurrent_profile_second_refused(tmp_path):
    env = _env(tmp_path)
    holder = subprocess.Popen(
        _base(tmp_path) + ["--road", "form", "--url", FORM_URL,
                                   "--slots-json",
                                   json.dumps(FORM_SLOTS), "--submit",
                                   "#m-submit", "--session-profile",
                                   "shared", "--poll", "120"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        cwd=str(ROOT), env=env)
    try:
        assert _wait_park(tmp_path / "audit-do.jsonl"), \
            "holder never parked at the L3 gate"
        rival = subprocess.run(
            _base(tmp_path) + ["--road", "observe", "--url",
                                        COURSES_URL, "--text", "t",
                                        "--expect", "Thermodynamics",
                                        "--session-profile", "shared",
                                        "--yes"],
            capture_output=True, text=True, cwd=str(ROOT), env=env,
            timeout=180)
        assert rival.returncode == 1, rival.stdout + rival.stderr
        assert "cannot launch browser" in rival.stdout, rival.stdout
        # Unrelated runs are unaffected while the profile is held.
        free = subprocess.run(
            _base(tmp_path) + _prof(tmp_path, "free")
            + ["--road", "observe", "--url",
               COURSES_URL, "--text", "t",
               "--expect", "Thermodynamics",
               "--yes"],
            capture_output=True, text=True, cwd=str(ROOT), env=env,
            timeout=300)
        assert free.returncode == 0, free.stdout + free.stderr
    finally:
        if holder.poll() is None:
            holder.terminate()
            try:
                holder.wait(timeout=60)
            except Exception:
                holder.kill()


def test_cli_crash_restart_keeps_profile(tmp_path, state_server):
    env = _env(tmp_path)
    dom = _domain(state_server)
    prime = subprocess.run(
        _base(tmp_path) + ["--road", "observe", "--url",
                                 f"{state_server}/setter.html",
                                 "--text", "setter", "--expect",
                                 "setter ready", "--allow-domain", dom,
                                 "--session-profile", "crashy", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert prime.returncode == 0, prime.stdout + prime.stderr
    doomed = subprocess.Popen(
        _base(tmp_path) + ["--road", "form", "--url", FORM_URL,
                                   "--slots-json",
                                   json.dumps(FORM_SLOTS), "--submit",
                                   "#m-submit",
                                   "--session-profile", "crashy",
                                   "--poll", "120"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        cwd=str(ROOT), env=env)
    try:
        assert _wait_park(tmp_path / "audit-do.jsonl"), \
            "doomed run never parked"
        doomed.terminate()  # death while holding the profile
        doomed.wait(timeout=60)
    finally:
        if doomed.poll() is None:
            doomed.kill()
    assert (tmp_path / "sessions" / "crashy").is_dir()  # not corrupt
    retry = subprocess.run(
        _base(tmp_path) + ["--road", "observe", "--url",
                                 f"{state_server}/reader.html",
                                 "--text", "reader", "--expect",
                                 STATE_EXPECT, "--allow-domain", dom,
                                 "--session-profile", "crashy", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert _session_lines(retry.stdout) == {"profile": "crashy",
                                            "reused": "yes"}


def test_cli_chain_uses_named_profile(tmp_path, state_server):
    env = _env(tmp_path)
    dom = _domain(state_server)
    prime = subprocess.run(
        _base(tmp_path) + ["--road", "observe", "--url",
                                  f"{state_server}/setter.html",
                                  "--text", "setter", "--expect",
                                  "setter ready", "--allow-domain", dom,
                                  "--session-profile", "chained",
                                  "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert prime.returncode == 0, prime.stdout + prime.stderr
    chain = {"legs": [
        {"road": "observe",
         "url": f"{state_server}/reader.html",
         "expect_text": STATE_EXPECT},
        {"road": "observe", "url": f"{state_server}/setter.html",
         "expect_text": "setter ready"}]}
    chain_file = tmp_path / "session-chain.json"
    chain_file.write_text(json.dumps(chain), encoding="utf-8")
    proc = subprocess.run(
        _base(tmp_path) + ["--chain-file", str(chain_file),
                                  "--allow-domain", dom,
                                  "--session-profile", "chained",
                                  "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=400)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    assert _session_lines(proc.stdout) == {"profile": "chained",
                                           "reused": "yes"}


def test_cli_daemon_reuses_named_profile(tmp_path, state_server):
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control import work_queue as wq
    from lakra.control.store import Database as D
    env = _env(tmp_path)
    dom = _domain(state_server)
    prime = subprocess.run(
        _base(tmp_path) + ["--road", "observe", "--url",
                                  f"{state_server}/setter.html",
                                  "--text", "setter", "--expect",
                                  "setter ready", "--allow-domain", dom,
                                  "--session-profile", "queued", "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert prime.returncode == 0, prime.stdout + prime.stderr
    legs = [{"road": "observe",
             "url": f"{state_server}/reader.html",
             "expect_text": STATE_EXPECT,
             "session_profile": "queued"}]
    # A chain needs 2+ legs; pair the profiled read with a plain one.
    legs.append({"road": "observe",
                 "url": f"{state_server}/setter.html",
                 "expect_text": "setter ready",
                 "session_profile": "queued"})
    d = D(tmp_path / "cli.db")
    try:
        wid = wq.enqueue(d, legs=legs)["work_id"]
    finally:
        d.close()
    proc = subprocess.run(
        _base(tmp_path) + _prof(tmp_path, "dq1")
        + ["--daemon", "--once", "--yes",
           "--allow-domain", dom],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=400)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "processed=1 settled=1" in proc.stdout
    d = D(tmp_path / "cli.db")
    try:
        item = wq.get(d, wid)
        assert item["state"] == "COMPLETED" and item["task_id"]
        # The queue item's own task COMPLETED (the DB also holds the
        # earlier priming run's task: same DB, unrelated run).
        from lakra.control import task_store
        assert task_store.load_task(
            d, item["task_id"]).status.value == "COMPLETED"
    finally:
        d.close()


def test_cli_daemon_plain_item_stays_ephemeral(tmp_path, state_server):
    sys.path.insert(0, str(ROOT / "src"))
    from lakra.control import work_queue as wq
    from lakra.control.store import Database as D
    env = _env(tmp_path)
    dom = _domain(state_server)
    prime = subprocess.run(
        _base(tmp_path) + ["--road", "observe", "--url",
                                  f"{state_server}/setter.html",
                                  "--text", "setter", "--expect",
                                  "setter ready", "--allow-domain", dom,
                                  "--session-profile", "untouched",
                                  "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert prime.returncode == 0, prime.stdout + prime.stderr
    before = sorted(str(p.relative_to(tmp_path / "sessions"))
                    for p in (tmp_path / "sessions").rglob("*")
                    if p.is_file())
    # Reader WITHOUT a profile: ephemeral state cannot see it, so the
    # chain leg fails and the item must not settle COMPLETED. The
    # daemon never attaches "untouched" silently.
    legs = [{"road": "observe",
             "url": f"{state_server}/reader.html",
             "expect_text": STATE_EXPECT},
            {"road": "observe",
             "url": f"{state_server}/setter.html",
             "expect_text": "setter ready"}]
    d = D(tmp_path / "cli.db")
    try:
        wid = wq.enqueue(d, legs=legs)["work_id"]
    finally:
        d.close()
    proc = subprocess.run(
        _base(tmp_path) + _prof(tmp_path, "nq1")
        + ["--daemon", "--once", "--yes",
           "--allow-domain", dom],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=400)
    assert "processed=1" in proc.stdout, proc.stdout + proc.stderr
    d = D(tmp_path / "cli.db")
    try:
        assert wq.get(d, wid)["state"] != "COMPLETED"
    finally:
        d.close()
    after = sorted(str(p.relative_to(tmp_path / "sessions"))
                   for p in (tmp_path / "sessions").rglob("*")
                   if p.is_file())
    assert after == before  # the named profile was never touched


def test_cli_transfer_with_profile(tmp_path):
    sys.path.insert(0, str(ROOT / "src"))
    (tmp_path / "transfers").mkdir(exist_ok=True)
    env = _env(tmp_path)
    proc = subprocess.run(
        _base(tmp_path) + ["--road", "upload", "--url",
                                 str((FIXTURES / "transfer-upload.html"
                                      ).as_uri()),
                                 "--upload", "Expense receipt", "--src",
                                 "cargo.txt", "--expect",
                                 "received cargo.txt",
                                 "--transfers-dir",
                                 str(tmp_path / "transfers"),
                                 "--session-profile", "shipper",
                                 "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    # cargo.txt was never staged: road-level refusal before any gate.
    # Session selection still reports (metadata only, no contents).
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert _session_lines(proc.stdout) == {"profile": "shipper",
                                           "reused": "no"}
    (tmp_path / "transfers" / "cargo.txt").write_bytes(b"manifest")
    proc = subprocess.run(
        _base(tmp_path) + ["--road", "upload", "--url",
                                  str((FIXTURES / "transfer-upload.html"
                                       ).as_uri()),
                                  "--upload", "Expense receipt",
                                  "--src", "cargo.txt", "--expect",
                                  "received cargo.txt (8 bytes)",
                                  "--transfers-dir",
                                  str(tmp_path / "transfers"),
                                  "--session-profile", "shipper",
                                  "--yes"],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
        timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "status: DONE" in proc.stdout
    assert _session_lines(proc.stdout) == {"profile": "shipper",
                                           "reused": "yes"}
