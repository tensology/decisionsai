"""Local DNS responder used for whole-domain Monk Mode blocking.

macOS routes only the configured Monk domain suffixes to this responder. Every
valid DNS question receives an authoritative NXDOMAIN response, so arbitrary
subdomains are blocked without changing the computer's normal DNS resolver.
"""

from __future__ import annotations

import socketserver
import struct
import threading

DNS_PORT = 53891


class DnsPacketError(ValueError):
    """Raised when a DNS packet is incomplete or unsafe to answer."""


def question_end(packet: bytes) -> int:
    """Return the end offset of the first uncompressed DNS question."""
    if len(packet) < 12:
        raise DnsPacketError("DNS packet is shorter than its header")
    question_count = struct.unpack("!H", packet[4:6])[0]
    if question_count != 1:
        raise DnsPacketError("Exactly one DNS question is required")
    offset = 12
    labels = 0
    while True:
        if offset >= len(packet):
            raise DnsPacketError("DNS question is truncated")
        length = packet[offset]
        offset += 1
        if length == 0:
            break
        if length & 0xC0 or length > 63 or offset + length > len(packet):
            raise DnsPacketError("DNS question has an invalid name")
        offset += length
        labels += 1
        if labels > 127:
            raise DnsPacketError("DNS question has too many labels")
    if offset + 4 > len(packet):
        raise DnsPacketError("DNS question is missing its type or class")
    return offset + 4


def build_nxdomain_response(packet: bytes) -> bytes:
    """Build a minimal authoritative NXDOMAIN response for a DNS query."""
    end = question_end(packet)
    query_flags = struct.unpack("!H", packet[2:4])[0]
    # QR=response, AA=authoritative, copy RD, RCODE=NXDOMAIN.
    response_flags = 0x8403 | (query_flags & 0x0100)
    header = packet[:2] + struct.pack("!HHHHH", response_flags, 1, 0, 0, 0)
    return header + packet[12:end]


class _ThreadingUdpServer(socketserver.ThreadingUDPServer):
    allow_reuse_address = True
    daemon_threads = True


class _ThreadingTcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _UdpHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        packet, sock = self.request
        try:
            response = build_nxdomain_response(packet)
        except DnsPacketError:
            return
        sock.sendto(response, self.client_address)


class _TcpHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        length_bytes = self.request.recv(2)
        if len(length_bytes) != 2:
            return
        expected = struct.unpack("!H", length_bytes)[0]
        packet = bytearray()
        while len(packet) < expected:
            chunk = self.request.recv(expected - len(packet))
            if not chunk:
                return
            packet.extend(chunk)
        try:
            response = build_nxdomain_response(bytes(packet))
        except DnsPacketError:
            return
        self.request.sendall(struct.pack("!H", len(response)) + response)


class MonkDnsServer:
    """Run the UDP and TCP DNS responders in daemon threads."""

    def __init__(self, host: str = "127.0.0.1", port: int = DNS_PORT) -> None:
        self.host = host
        self.port = int(port)
        self._udp: _ThreadingUdpServer | None = None
        self._tcp: _ThreadingTcpServer | None = None
        self._lock = threading.RLock()

    @property
    def running(self) -> bool:
        return self._udp is not None and self._tcp is not None

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            udp = _ThreadingUdpServer((self.host, self.port), _UdpHandler)
            try:
                tcp = _ThreadingTcpServer((self.host, int(udp.server_address[1])), _TcpHandler)
            except Exception:
                udp.server_close()
                raise
            self.port = int(udp.server_address[1])
            self._udp = udp
            self._tcp = tcp
            threading.Thread(target=udp.serve_forever, name="monk-dns-udp", daemon=True).start()
            threading.Thread(target=tcp.serve_forever, name="monk-dns-tcp", daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            udp, tcp = self._udp, self._tcp
            self._udp = None
            self._tcp = None
        for server in (udp, tcp):
            if server is not None:
                server.shutdown()
                server.server_close()


_server: MonkDnsServer | None = None
_server_lock = threading.Lock()


def get_monk_dns_server() -> MonkDnsServer:
    global _server
    with _server_lock:
        if _server is None:
            _server = MonkDnsServer()
        return _server
