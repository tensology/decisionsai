from copy import deepcopy
from datetime import datetime
import socket
import struct

import pytest

from distr.core.monk_mode import (
    HOSTS_BLOCK_BEGIN,
    HOSTS_BLOCK_END,
    HostsFileController,
    MonkModeService,
    build_hosts_content,
    blocked_domains,
    expanded_hostnames,
    normalize_hostname,
    schedule_is_active,
)
from distr.core.monk_dns import DnsPacketError, MonkDnsServer, build_nxdomain_response
from distr.core.monk_privileged_helper import (
    RESOLVER_MARKER,
    main as privileged_helper_main,
    sync_resolvers,
)


def test_normalize_hostname_accepts_urls_and_idn() -> None:
    assert normalize_hostname("https://WWW.Example.com/watch?v=1") == "www.example.com"
    assert normalize_hostname("example.com:8443/path") == "example.com"
    assert normalize_hostname("https://münich.example") == "xn--mnich-kva.example"


@pytest.mark.parametrize(
    "value",
    ["", "localhost", "file:///etc/passwd", "*.example.com", "bad host.example", "example"],
)
def test_normalize_hostname_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_hostname(value)


def test_hosts_block_is_reversible_and_preserves_unrelated_content() -> None:
    original = "127.0.0.1 localhost\n10.0.0.2 internal.test\n"
    sites = [{"url": "https://example.com", "hostname": "example.com"}]

    enabled = build_hosts_content(original, enabled=True, sites=sites)
    assert HOSTS_BLOCK_BEGIN in enabled
    assert HOSTS_BLOCK_END in enabled
    assert "127.0.0.1 example.com" in enabled
    assert "::1 www.example.com" in enabled
    assert "10.0.0.2 internal.test" in enabled

    disabled = build_hosts_content(enabled, enabled=False, sites=sites)
    assert disabled == original


def test_common_redirect_hostnames_are_blocked_with_the_saved_site() -> None:
    facebook_hosts = expanded_hostnames([{"hostname": "www.facebook.com"}])
    assert {
        "facebook.com",
        "m.facebook.com",
        "mbasic.facebook.com",
        "static.xx.fbcdn.net",
        "www.facebook.com",
    } - set(facebook_hosts) == {"static.xx.fbcdn.net"}
    assert {"facebook.net", "fb.com", "fbcdn.net", "fbsbx.com", "messenger.com"} <= set(facebook_hosts)

    content = build_hosts_content("127.0.0.1 localhost\n", enabled=True, sites=[{"hostname": "facebook.com"}])
    assert "127.0.0.1 m.facebook.com" in content
    assert "::1 mbasic.facebook.com" in content


def test_saved_www_address_becomes_a_whole_domain_suffix() -> None:
    assert blocked_domains([{"hostname": "www.facebook.com"}]) == [
        "facebook.com",
        "facebook.net",
        "fb.com",
        "fbcdn.net",
        "fbsbx.com",
        "messenger.com",
    ]
    assert blocked_domains([{"hostname": "news.example.com"}]) == ["news.example.com"]


def test_hosts_controller_writes_and_removes_managed_block(tmp_path) -> None:
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    controller = HostsFileController(hosts)
    sites = [{"url": "social.example", "hostname": "social.example"}]

    assert controller.apply(True, sites) is True
    assert controller.is_applied(sites) is True
    assert controller.apply(True, sites) is False
    assert controller.apply(False, sites) is True
    assert hosts.read_text(encoding="utf-8") == "127.0.0.1 localhost\n"


def test_privileged_helper_only_updates_managed_hosts_block(tmp_path) -> None:
    hosts = tmp_path / "hosts"
    state = tmp_path / "state.json"
    resolvers = tmp_path / "resolver"
    hosts.write_text("127.0.0.1 localhost\n10.0.0.4 preserve.example\n", encoding="utf-8")
    state.write_text(
        '{"version": 2, "enabled": true, "hostnames": ["focus.invalid", "www.focus.invalid"], "domains": ["focus.invalid"]}\n',
        encoding="utf-8",
    )
    state.chmod(0o600)

    assert privileged_helper_main(["helper", str(state), str(hosts), str(state.stat().st_uid), str(resolvers), "53891"]) == 0
    content = hosts.read_text(encoding="utf-8")
    assert "10.0.0.4 preserve.example" in content
    assert "127.0.0.1 focus.invalid" in content
    assert HOSTS_BLOCK_BEGIN in content
    assert (resolvers / "focus.invalid").read_text(encoding="utf-8").startswith(RESOLVER_MARKER)

    state.write_text('{"version": 2, "enabled": false, "hostnames": [], "domains": []}\n', encoding="utf-8")
    state.chmod(0o600)
    assert privileged_helper_main(["helper", str(state), str(hosts), str(state.stat().st_uid), str(resolvers), "53891"]) == 0
    assert hosts.read_text(encoding="utf-8") == "127.0.0.1 localhost\n10.0.0.4 preserve.example\n"
    assert not (resolvers / "focus.invalid").exists()


def test_privileged_helper_rejects_injected_hostname(tmp_path) -> None:
    hosts = tmp_path / "hosts"
    state = tmp_path / "state.json"
    resolvers = tmp_path / "resolver"
    hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    state.write_text(
        '{"version": 2, "enabled": true, "hostnames": ["safe.invalid\\n1.2.3.4 injected.invalid"], "domains": ["safe.invalid"]}\n',
        encoding="utf-8",
    )
    state.chmod(0o600)
    with pytest.raises(ValueError):
        privileged_helper_main(["helper", str(state), str(hosts), str(state.stat().st_uid), str(resolvers), "53891"])
    assert hosts.read_text(encoding="utf-8") == "127.0.0.1 localhost\n"


def test_resolver_sync_preserves_unrelated_files(tmp_path) -> None:
    resolvers = tmp_path / "resolver"
    resolvers.mkdir()
    unrelated = resolvers / "corp.example"
    unrelated.write_text("nameserver 10.0.0.2\n", encoding="utf-8")

    sync_resolvers(resolvers, True, ["focus.invalid"], 53891)
    assert unrelated.read_text(encoding="utf-8") == "nameserver 10.0.0.2\n"
    assert "port 53891" in (resolvers / "focus.invalid").read_text(encoding="utf-8")

    sync_resolvers(resolvers, False, [], 53891)
    assert unrelated.exists()
    assert not (resolvers / "focus.invalid").exists()


def _dns_query(hostname: str, query_id: int = 0x1234) -> bytes:
    labels = b"".join(bytes([len(label)]) + label.encode("ascii") for label in hostname.split("."))
    return struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0) + labels + b"\x00" + struct.pack("!HH", 1, 1)


def test_dns_response_is_authoritative_nxdomain() -> None:
    query = _dns_query("anything.deep.facebook.com")
    response = build_nxdomain_response(query)
    response_id, flags, questions, answers = struct.unpack("!HHHH", response[:8])
    assert response_id == 0x1234
    assert flags & 0x8000
    assert flags & 0x0400
    assert flags & 0x000F == 3
    assert questions == 1
    assert answers == 0
    with pytest.raises(DnsPacketError):
        build_nxdomain_response(b"short")


def test_local_dns_server_blocks_arbitrary_nested_subdomain() -> None:
    server = MonkDnsServer(port=0)
    server.start()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        query = _dns_query("arbitrary.nested.focus.invalid")
        udp.settimeout(1)
        udp.sendto(query, (server.host, server.port))
        response, _ = udp.recvfrom(512)
        assert struct.unpack("!H", response[2:4])[0] & 0x000F == 3

        with socket.create_connection((server.host, server.port), timeout=1) as tcp:
            tcp.sendall(struct.pack("!H", len(query)) + query)
            response_length = struct.unpack("!H", tcp.recv(2))[0]
            tcp_response = tcp.recv(response_length)
            assert struct.unpack("!H", tcp_response[2:4])[0] & 0x000F == 3
    finally:
        udp.close()
        server.stop()


def test_schedule_handles_daytime_and_overnight_windows() -> None:
    daytime = [{"days": [0], "start": "09:00", "end": "17:00", "enabled": True}]
    overnight = [{"days": [4], "start": "22:00", "end": "06:00", "enabled": True}]

    assert schedule_is_active(daytime, datetime(2026, 7, 27, 9, 0)) is True  # Monday
    assert schedule_is_active(daytime, datetime(2026, 7, 27, 17, 0)) is False
    assert schedule_is_active(overnight, datetime(2026, 7, 31, 23, 30)) is True  # Friday
    assert schedule_is_active(overnight, datetime(2026, 8, 1, 5, 59)) is True  # Saturday
    assert schedule_is_active(overnight, datetime(2026, 8, 1, 6, 0)) is False


def test_service_crud_toggle_and_manual_override_until_boundary(tmp_path) -> None:
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    service = MonkModeService(HostsFileController(hosts))
    stored = {
        "enabled": False,
        "sites": [],
        "schedule_enabled": False,
        "schedule": [],
        "schedule_state": None,
    }

    service._load = lambda: deepcopy(stored)  # type: ignore[method-assign]

    def save(config):
        stored.clear()
        stored.update(deepcopy(config))

    service._save = save  # type: ignore[method-assign]

    state = service.add_site({"url": "https://news.example/path", "label": "News"})
    site_id = state["sites"][0]["id"]
    assert state["sites"][0]["hostname"] == "news.example"

    state = service.set_enabled(True)
    assert state["enabled"] is True
    assert state["hosts_applied"] is True

    state = service.update_site(site_id, {"url": "updates.example", "label": "Updates"})
    assert "updates.example" in hosts.read_text(encoding="utf-8")
    assert "news.example" not in hosts.read_text(encoding="utf-8")

    stored["schedule_enabled"] = True
    stored["schedule"] = [{"days": [0], "start": "09:00", "end": "17:00", "enabled": True}]
    stored["schedule_state"] = False
    state = service.reconcile_schedule(datetime(2026, 7, 27, 9, 0))
    assert state["enabled"] is True
    hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    state = service.reconcile_schedule(datetime(2026, 7, 27, 12, 0))
    assert state["hosts_applied"] is True
    assert "updates.example" in hosts.read_text(encoding="utf-8")
    service.set_enabled(False)  # manual override inside the active Monday window
    state = service.reconcile_schedule(datetime(2026, 7, 27, 12, 0))
    assert state["enabled"] is False

    # At the next boundary, the schedule is applied again.
    state = service.reconcile_schedule(datetime(2026, 7, 27, 17, 1))
    assert state["enabled"] is False
    assert state["schedule_state"] is False

    state = service.remove_site(site_id)
    assert state["sites"] == []
    assert HOSTS_BLOCK_BEGIN not in hosts.read_text(encoding="utf-8")
