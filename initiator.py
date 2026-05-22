#!/usr/bin/env python3
"""
LNCPv1 - Local Network Chat Protocol
Initiator Application

Usage:
    python initiator.py <recipient_nickname> [--deadline <seconds>] [--port <tcp_port>]
"""

import socket
import threading
import uuid
import time
import json
import sys
import argparse
from datetime import datetime, timezone

# ─────────────────────────────────────────────
#  Protocol Constants
# ─────────────────────────────────────────────
LNCP_VERSION    = "LNCPv1"
UDP_BROADCAST   = "<broadcast>"
UDP_PORT        = 54321          # well-known discovery port
BUFFER_SIZE     = 4096
DEFAULT_DEADLINE = 30            # seconds
DEFAULT_TCP_PORT = 55000

MSG_TYPES = {
    "HANDSHAKE_REQ":  "HANDSHAKE_REQ",
    "HANDSHAKE_ACK":  "HANDSHAKE_ACK",
    "HANDSHAKE_NAK":  "HANDSHAKE_NAK",
    "TEXT":           "TEXT",
    "TEXT_ACK":       "TEXT_ACK",
    "CLOSE_REQ":      "CLOSE_REQ",
    "CLOSE_ACK":      "CLOSE_ACK",
    "DISCOVER":       "DISCOVER",
}

# ─────────────────────────────────────────────
#  Message Builder / Parser
# ─────────────────────────────────────────────

def build_message(msg_type: str, payload: dict) -> bytes:
    """Encode an LNCP message as UTF-8 JSON lines."""
    envelope = {
        "version":   LNCP_VERSION,
        "type":      msg_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload":   payload,
    }
    return (json.dumps(envelope) + "\n").encode("utf-8")


def parse_message(raw: bytes) -> dict:
    """Decode an LNCP message; raise ValueError on malformed input."""
    text = raw.decode("utf-8").strip()
    msg  = json.loads(text)
    if msg.get("version") != LNCP_VERSION:
        raise ValueError(f"Unknown protocol version: {msg.get('version')}")
    if msg.get("type") not in MSG_TYPES:
        raise ValueError(f"Unknown message type: {msg.get('type')}")
    return msg


def recv_message(sock: socket.socket) -> dict:
    """Read one newline-terminated LNCP message from a TCP socket."""
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(BUFFER_SIZE)
        if not chunk:
            raise ConnectionError("Connection closed by peer")
        data += chunk
    return parse_message(data)


# ─────────────────────────────────────────────
#  Initiator Logic
# ─────────────────────────────────────────────

class Initiator:
    def __init__(self, recipient: str, deadline_secs: int, tcp_port: int):
        self.recipient      = recipient
        self.deadline_secs  = deadline_secs
        self.tcp_port       = tcp_port
        self.request_uuid   = str(uuid.uuid4())
        self.deadline_ts    = time.time() + deadline_secs  # absolute epoch
        self.connected      = False
        self.peer_conn      = None

    # ── Step 1: UDP Broadcast ──────────────────────────────────────────
    def broadcast_discovery(self):
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        payload = {
            "recipient": self.recipient,
            "deadline":  self.deadline_ts,
            "tcp_port":  self.tcp_port,
            "uuid":      self.request_uuid,
        }
        msg = build_message("DISCOVER", payload)
        udp.sendto(msg, (UDP_BROADCAST, UDP_PORT))
        udp.close()
        print(f"[DISCOVER] Broadcast sent → recipient='{self.recipient}' "
              f"uuid={self.request_uuid} deadline={self.deadline_secs}s tcp_port={self.tcp_port}")

    # ── Step 2: Accept TCP connection from Recipient ───────────────────
    def wait_for_connection(self) -> socket.socket:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("", self.tcp_port))
        srv.listen(1)
        timeout = self.deadline_ts - time.time()
        if timeout <= 0:
            raise TimeoutError("Deadline already passed before TCP listen")
        srv.settimeout(timeout)
        print(f"[TCP] Listening on port {self.tcp_port} for {timeout:.1f}s …")
        conn, addr = srv.accept()
        srv.close()
        print(f"[TCP] Connection accepted from {addr}")
        return conn

    # ── Step 3: Handshaking ───────────────────────────────────────────
    def handshake(self, conn: socket.socket) -> bool:
        try:
            msg = recv_message(conn)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[HANDSHAKE] Malformed message: {e}")
            conn.sendall(build_message("HANDSHAKE_NAK", {"reason": "malformed_message"}))
            return False

        if msg["type"] != "HANDSHAKE_REQ":
            conn.sendall(build_message("HANDSHAKE_NAK", {"reason": "expected_HANDSHAKE_REQ"}))
            return False

        recv_uuid = msg["payload"].get("uuid")
        if recv_uuid != self.request_uuid:
            print(f"[HANDSHAKE] UUID mismatch: got {recv_uuid}")
            conn.sendall(build_message("HANDSHAKE_NAK", {"reason": "uuid_mismatch"}))
            return False

        if time.time() > self.deadline_ts:
            print("[HANDSHAKE] Deadline expired.")
            conn.sendall(build_message("HANDSHAKE_NAK", {"reason": "deadline_expired"}))
            return False

        conn.sendall(build_message("HANDSHAKE_ACK", {
            "uuid":    self.request_uuid,
            "message": "Welcome! Connection established.",
        }))
        print("[HANDSHAKE] ✓ Handshake successful")
        return True

    # ── Step 4: Message Exchange (simplex turn-based) ─────────────────
    def chat_loop(self, conn: socket.socket):
        print("\n─────────────────────────────────────────")
        print("  Chat session open. Type your message.")
        print("  Type '/quit' to close the connection.")
        print("─────────────────────────────────────────\n")
        self.connected = True

        # Initiator sends first
        while self.connected:
            try:
                text = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                text = "/quit"

            if text == "/quit":
                conn.sendall(build_message("CLOSE_REQ", {"reason": "user_request"}))
                try:
                    ack = recv_message(conn)
                    if ack["type"] == "CLOSE_ACK":
                        print("[SESSION] Peer acknowledged close.")
                except Exception:
                    pass
                self.connected = False
                break

            if not text:
                continue

            # Send TEXT message
            conn.sendall(build_message("TEXT", {"body": text}))

            # Wait for TEXT_ACK before sending next
            try:
                conn.settimeout(30)
                ack = recv_message(conn)
                conn.settimeout(None)
                if ack["type"] == "TEXT_ACK":
                    print(f"  [✓ delivered at {ack['timestamp']}]")
                elif ack["type"] == "CLOSE_REQ":
                    print("\n[SESSION] Peer requested close.")
                    conn.sendall(build_message("CLOSE_ACK", {}))
                    self.connected = False
                else:
                    print(f"  [?] Unexpected response type: {ack['type']}")
            except socket.timeout:
                print("  [!] No acknowledgment received (timeout).")
            except ConnectionError:
                print("\n[SESSION] Connection lost.")
                self.connected = False

    # ── Main entry ────────────────────────────────────────────────────
    def run(self):
        self.broadcast_discovery()
        try:
            conn = self.wait_for_connection()
        except (socket.timeout, TimeoutError):
            print("[ERROR] No recipient responded within deadline.")
            return

        conn.settimeout(10)
        ok = self.handshake(conn)
        conn.settimeout(None)

        if not ok:
            print("[ERROR] Handshake failed. Closing.")
            conn.close()
            return

        try:
            self.chat_loop(conn)
        finally:
            conn.close()
            print("[SESSION] Disconnected.")


# ─────────────────────────────────────────────
#  Entry Point
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LNCPv1 Initiator")
    parser.add_argument("recipient", help="Nickname of the intended recipient")
    parser.add_argument("--deadline", type=int, default=DEFAULT_DEADLINE,
                        help=f"Seconds to wait for a response (default: {DEFAULT_DEADLINE})")
    parser.add_argument("--port", type=int, default=DEFAULT_TCP_PORT,
                        help=f"TCP port to listen on (default: {DEFAULT_TCP_PORT})")
    args = parser.parse_args()

    app = Initiator(
        recipient    = args.recipient,
        deadline_secs= args.deadline,
        tcp_port     = args.port,
    )
    app.run()


if __name__ == "__main__":
    main()
