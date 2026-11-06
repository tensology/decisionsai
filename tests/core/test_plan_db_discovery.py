"""Local DB discovery (N5 phase 1): loopback gate, bare TCP probes against a throwaway listener, socket stat,
brew parsing. Never touches the real Postgres/MySQL ports."""
import os
import socket
import tempfile
import threading

import pytest

from distr.core.planning import db_discovery as dd


def fake_resolver(table):
    def resolve(host, *_args, **_kw):
        if host not in table:
            raise socket.gaierror("unknown")
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0)) for a in table[host]]
    return resolve


RESOLVE = fake_resolver({
    "127.0.0.1": ["127.0.0.1"], "127.0.0.5": ["127.0.0.5"], "::1": ["::1"], "localhost": ["127.0.0.1", "::1"],
    "::ffff:127.0.0.1": ["::ffff:127.0.0.1"], "10.0.0.5": ["10.0.0.5"], "db.example.com": ["93.184.216.34"],
    "rebind.test": ["127.0.0.1", "192.168.1.9"], "::ffff:10.0.0.1": ["::ffff:10.0.0.1"], "fe80::1": ["fe80::1"],
    "0.0.0.0": ["0.0.0.0"], "::": ["::"],
})


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.5", "::1", "[::1]", "localhost", "::ffff:127.0.0.1"])
def test_loopback_hosts_are_allowed(host):
    assert dd.assert_loopback_host(host, resolver=RESOLVE)


@pytest.mark.parametrize("host", ["", "  ", "0.0.0.0", "::", "10.0.0.5", "db.example.com", "rebind.test",
                                  "::ffff:10.0.0.1", "fe80::1", "unknown.invalid"])
def test_non_loopback_hosts_are_refused(host):
    with pytest.raises(dd.NonLoopbackHost):
        dd.assert_loopback_host(host, resolver=RESOLVE)


def test_real_resolver_localhost_is_loopback():
    assert all(dd._is_loopback(a) for a in dd.assert_loopback_host("localhost"))


@pytest.fixture
def listener():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    port = server.getsockname()[1]
    stop = threading.Event()

    def accept():
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
                conn.close()
            except OSError:
                pass
    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    yield port
    stop.set(); server.close(); thread.join(1)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_probe_reaches_throwaway_listener_and_closed_port(listener):
    assert dd.probe_port("127.0.0.1", listener) is True
    assert dd.probe_port("127.0.0.1", _free_port()) is False
    with pytest.raises(dd.NonLoopbackHost):
        dd.probe_port("0.0.0.0", listener)
    with pytest.raises(ValueError):
        dd.probe_port("127.0.0.1", 70000)


def test_socket_stat_on_temp_socket():
    directory = tempfile.mkdtemp(prefix="dbd")       # short path: AF_UNIX limit
    path = os.path.join(directory, "s.sock")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(path)
    try:
        assert dd.socket_present(path) is True
        regular = os.path.join(directory, "file")
        open(regular, "w").close()
        assert dd.socket_present(regular) is False
        assert dd.socket_present(os.path.join(directory, "missing")) is False
    finally:
        sock.close(); os.unlink(path)


BREW = """Name          Status  User File
mysql@8.4     started paul ~/Library/LaunchAgents/homebrew.mxcl.mysql@8.4.plist
postgresql@16 started paul ~/Library/LaunchAgents/homebrew.mxcl.postgresql@16.plist
redis         none
mariadb       stopped
"""


def test_brew_parsing_and_runner():
    rows = dd.parse_brew_services(BREW)
    assert rows == [{"name": "mysql@8.4", "engine": "mysql", "status": "started"},
                    {"name": "postgresql@16", "engine": "postgres", "status": "started"},
                    {"name": "mariadb", "engine": "mysql", "status": "stopped"}]

    class Result:
        returncode, stdout = 0, BREW
    calls = []
    def runner(args, **kw):
        calls.append((args, kw)); return Result()
    if dd.shutil.which("brew") or os.path.exists("/opt/homebrew/bin/brew"):
        assert dd.brew_services(runner=runner)[0]["name"] == "mysql@8.4"
        args, kw = calls[0]
        assert args[1:] == ["services", "list"] and kw["timeout"] <= 5 and "shell" not in kw


def test_discover_uses_only_given_ports_and_sockets(listener):
    seen = []
    def probe(host, port):
        seen.append((host, port)); return dd.probe_port(host, port)
    result = dd.discover(ports={"postgres": [listener]}, sockets={"mysql": ["/nonexistent/mysql.sock"]},
                         hosts=["127.0.0.1"], probe=probe, brew=lambda: [{"name": "x", "engine": "mysql", "status": "started"}])
    assert seen == [("127.0.0.1", listener)]
    assert result["candidates"] == [
        {"engine": "postgres", "host": "127.0.0.1", "port": listener, "socket": None, "reachable": True, "source": "tcp"},
        {"engine": "mysql", "host": "localhost", "port": None, "socket": "/nonexistent/mysql.sock", "reachable": False, "source": "socket"},
    ]
    assert "No login" in result["note"] and result["brew_services"][0]["engine"] == "mysql"


def test_module_never_imports_drivers_or_reads_credentials():
    source = open(dd.__file__).read()
    for word in ("psycopg", "pymysql", "pgpass", "my.cnf", "password", "'.env'", "\".env\""):
        assert word not in source.lower().replace("no credentials", "")
