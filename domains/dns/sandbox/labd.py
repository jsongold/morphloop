"""Lab daemon for the ``dns.resolver_lab`` fixture. Runs INSIDE the lab only.

Usage: ``python3 labd.py <config.json>``. It serves, in one process:

- an authoritative UDP DNS server for the configured A records (anything
  else under any name: NXDOMAIN; other types for a known name: NODATA);
- one HTTP service per configured service, answering ``GET``/``HEAD`` on its
  health path with the configured body (everything else: 404).

Once every socket is bound it creates the ``ready_file`` named in the config.
Standard library only, Python 3.8+ (it runs on the lab image's interpreter).
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_TYPE_A = 1
_CLASS_IN = 1
_RCODE_FORMERR = 1
_RCODE_NXDOMAIN = 3
_RCODE_NOTIMP = 4


def _parse_question(packet: bytes) -> tuple[str, int, int, int] | None:
    """Return (lowercased name without trailing dot, qtype, qclass, end offset)."""
    labels: list[str] = []
    pos = 12
    while True:
        if pos >= len(packet):
            return None
        length = packet[pos]
        pos += 1
        if length == 0:
            break
        if length & 0xC0 or pos + length > len(packet):
            return None  # queries never use compression
        labels.append(packet[pos : pos + length].decode("ascii", "replace").lower())
        pos += length
    if pos + 4 > len(packet):
        return None
    qtype, qclass = struct.unpack("!HH", packet[pos : pos + 4])
    return ".".join(labels), qtype, qclass, pos + 4


def answer(packet: bytes, records: dict[str, list[bytes]], ttl: int) -> bytes | None:
    """Build the response to one DNS query packet, or ``None`` to drop it."""
    if len(packet) < 12:
        return None
    ident, flags, qdcount = struct.unpack("!HHH", packet[:6])
    if flags & 0x8000:
        return None  # a response, not a query
    opcode = (flags >> 11) & 0xF
    rd = flags & 0x0100
    base_flags = 0x8000 | (opcode << 11) | 0x0400 | rd | 0x0080  # QR, AA, RD copy, RA

    def reply(rcode: int, question: bytes, answers: list[bytes]) -> bytes:
        header = struct.pack(
            "!HHHHHH", ident, base_flags | rcode, 1 if question else 0, len(answers), 0, 0
        )
        return header + question + b"".join(answers)

    if opcode != 0:
        return reply(_RCODE_NOTIMP, b"", [])
    parsed = _parse_question(packet) if qdcount == 1 else None
    if parsed is None:
        return reply(_RCODE_FORMERR, b"", [])
    name, qtype, qclass, end = parsed
    question = packet[12:end]
    addresses = records.get(name)
    if addresses is None:
        return reply(_RCODE_NXDOMAIN, question, [])
    answers: list[bytes] = []
    if qclass == _CLASS_IN and qtype == _TYPE_A:
        for address in addresses:
            # Name is a pointer to the question name at offset 12.
            answers.append(struct.pack("!HHHIH", 0xC00C, _TYPE_A, _CLASS_IN, ttl, 4) + address)
    return reply(0, question, answers)


def _serve_dns(sock: socket.socket, records: dict[str, list[bytes]], ttl: int) -> None:
    while True:
        try:
            packet, peer = sock.recvfrom(4096)
        except OSError:
            continue
        response = answer(packet, records, ttl)
        if response is not None:
            try:
                sock.sendto(response, peer)
            except OSError:
                pass


def _http_handler(health_path: str, body: bytes) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _respond(self, send_body: bool) -> None:
            path = self.path.split("?", 1)[0]
            ok = path == health_path
            payload = body if ok else b"not found\n"
            self.send_response(200 if ok else 404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if send_body:
                self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            self._respond(True)

        def do_HEAD(self) -> None:  # noqa: N802 - http.server naming
            self._respond(False)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    return Handler


def main(argv: list[str]) -> int:
    with open(argv[1], encoding="utf-8") as f:
        config = json.load(f)
    zone = config["zone_server"]
    records: dict[str, list[bytes]] = {}
    for record in zone["records"]:
        name = record["name"].lower().rstrip(".")
        records.setdefault(name, []).append(socket.inet_aton(record["address"]))

    dns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dns.bind((zone["listen_address"], zone["port"]))
    threads = [
        threading.Thread(target=_serve_dns, args=(dns, records, zone["ttl_seconds"]), daemon=True)
    ]
    for service in config["services"]:
        handler = _http_handler(service["health_path"], service["health_body"].encode("utf-8"))
        server = ThreadingHTTPServer((service["listen_address"], service["port"]), handler)
        threads.append(threading.Thread(target=server.serve_forever, daemon=True))
    for thread in threads:
        thread.start()
    with open(config["ready_file"], "w", encoding="utf-8") as f:
        f.write("ready\n")
    for thread in threads:
        thread.join()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
