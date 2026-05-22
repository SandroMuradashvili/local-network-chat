#!/usr/bin/env python3
"""
LNCPv1 - Local Network Chat Protocol
Recipient Application

Usage:
    python recipient.py <my_nickname>
"""

import socket
import threading
import time
import json
import sys
import argparse
from datetime import datetime, timezone

# ─────────────────────────────────────────────
#  Protocol Constants
# ─────────────────────────────────────────────
LNCP_VERSION = "LNCPv1"
UDP_PORT     = 54321
BUFFER_SIZE  = 4096

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
#  Message Builder / Parser  (same as initiator)
# ─────────────────────────────────────────────

def build_message(msg_type: str, payload: dict) -> bytes:
    envelope = {
        "version":   LNCP_VERSION,
        "type":      msg_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload":   payload,
    }
    return (json.dumps(envelope) + "\n").encode("utf-8")


def parse_message(raw: bytes) -> dict:
    text = raw.decode("utf-8").strip()
    msg  = json.loads(text)
    if msg.get("version") != LNCP_VERSION:
        raise ValueError(f"Unknown protocol version: {msg.get('version')}")
    if msg.get("type") not in MSG_TYPES:
        raise ValueError(f"Unknown message type: {msg.get('type')}")
    return msg


def recv_message(sock: socket.socket) -> dict:
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(BUFFER_SIZE)
        if not chunk:
            raise ConnectionError("Connection closed by peer")
        data += chunk
    return parse_message(data)


# ─────────────────────────────────────────────
#  Recipient Logic
# ─────────────────────────────────────────────

class Recipient:
    def __init__(self, nickname: str):
        self.nickname  = nickname
        self.connected = False

    # ── Step 1: Listen for UDP Broadcasts ─────────────────────────────
    def listen_for_discovery(self) -> dict:
        """Block until a DISCOVER message addressed to our nickname arrives."""
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp.bind(("", UDP_PORT))
        print(f"[DISCOVER] Listening as '{self.nickname}' on UDP port {UDP_PORT} …\n")

        while True:
            raw, addr = udp.recvfrom(BUFFER_SIZE)
            try:
                msg = parse_message(raw)
            except (json.JSONDecodeError, ValueError):
                continue  # ignore malformed broadcasts

            if msg["type"] != "DISCOVER":
                continue

            p = msg["payload"]
            if p.get("recipient") != self.nickname:
                continue

            # Check deadline before bothering to respond
            if time.time() > p.get("deadline", 0):
                print(f"[DISCOVER] Got request from {addr[0]} but deadline already passed. Ignoring.")
                continue

            print(f"[DISCOVER] Invitation from {addr[0]} → uuid={p['uuid']} "
                  f"deadline_in={p['deadline'] - time.time():.1f}s tcp_port={p['tcp_port']}")
            udp.close()

            return {
                "initiator_ip": addr[0],
                "tcp_port":     p["tcp_port"],
                "uuid":         p["uuid"],
                "deadline":     p["deadline"],
            }

    # ── Step 2: Connect via TCP to Initiator ──────────────────────────
    def connect_tcp(self, info: dict) -> socket.socket:
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conn.connect((info["initiator_ip"], info["tcp_port"]))
        print(f"[TCP] Connected to initiator at {info['initiator_ip']}:{info['tcp_port']}")
        return conn

    # ── Step 3: Handshaking ───────────────────────────────────────────
    def handshake(self, conn: socket.socket, info: dict) -> bool:
        # Send HANDSHAKE_REQ
        conn.sendall(build_message("HANDSHAKE_REQ", {"uuid": info["uuid"]}))
        print("[HANDSHAKE] Sent HANDSHAKE_REQ")

        try:
            conn.settimeout(10)
            msg = recv_message(conn)
            conn.settimeout(None)
        except (socket.timeout, ConnectionError, json.JSONDecodeError, ValueError) as e:
            print(f"[HANDSHAKE] Error receiving response: {e}")
            return False

        if msg["type"] == "HANDSHAKE_ACK":
            print(f"[HANDSHAKE] ✓ Accepted: {msg['payload'].get('message', '')}")
            return True
        elif msg["type"] == "HANDSHAKE_NAK":
            reason = msg["payload"].get("reason", "unknown")
            print(f"[HANDSHAKE] ✗ Rejected: {reason}")
            return False
        else:
            print(f"[HANDSHAKE] Unexpected message type: {msg['type']}")
            return False

    # ── Step 4: Message Exchange ──────────────────────────────────────
    def chat_loop(self, conn: socket.socket):
        print("\n─────────────────────────────────────────")
        print("  Chat session open. Waiting for messages.")
        print("  Type '/quit' to close the connection.")
        print("─────────────────────────────────────────\n")
        self.connected = True

        # Run a background thread to read input for /quit
        quit_event = threading.Event()

        def input_watcher():
            while not quit_event.is_set():
                try:
                    line = input()
                    if line.strip() == "/quit":
                        conn.sendall(build_message("CLOSE_REQ", {"reason": "user_request"}))
                        # wait for CLOSE_ACK in main thread
                        quit_event.set()
                except (EOFError, OSError):
                    quit_event.set()

        t = threading.Thread(target=input_watcher, daemon=True)
        t.start()

        # Main receive loop: recipient waits for TEXT, replies with TEXT_ACK
        while self.connected and not quit_event.is_set():
            try:
                conn.settimeout(1.0)
                msg = recv_message(conn)
                conn.settimeout(None)
            except socket.timeout:
                continue
            except (ConnectionError, json.JSONDecodeError, ValueError) as e:
                print(f"\n[SESSION] Connection error: {e}")
                break

            if msg["type"] == "TEXT":
                body = msg["payload"].get("body", "")
                print(f"\nPeer: {body}")
                # Acknowledge immediately
                conn.sendall(build_message("TEXT_ACK", {}))

            elif msg["type"] == "CLOSE_REQ":
                print("\n[SESSION] Peer requested close. Sending CLOSE_ACK.")
                conn.sendall(build_message("CLOSE_ACK", {}))
                self.connected = False

            elif msg["type"] == "CLOSE_ACK":
                print("\n[SESSION] Close acknowledged by peer.")
                self.connected = False

            else:
                print(f"[SESSION] Unexpected message type: {msg['type']}")

        quit_event.set()

    # ── Main Entry ────────────────────────────────────────────────────
    def run(self):
        while True:  # allow multiple sequential sessions
            info = self.listen_for_discovery()

            # Ask user whether to accept
            ans = input(f"\nAccept connection request? [Y/n]: ").strip().lower()
            if ans in ("n", "no"):
                print("[DISCOVER] Connection declined by user.")
                continue

            try:
                conn = self.connect_tcp(info)
            except (ConnectionRefusedError, OSError) as e:
                print(f"[TCP] Failed to connect: {e}")
                continue

            ok = self.handshake(conn, info)
            if not ok:
                conn.close()
                continue

            try:
                self.chat_loop(conn)
            finally:
                conn.close()
                print("[SESSION] Disconnected.\n")


# ─────────────────────────────────────────────
#  Entry Point
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LNCPv1 Recipient")
    parser.add_argument("nickname", help="Your nickname (must match what initiator broadcasts)")
    args = parser.parse_args()

    app = Recipient(nickname=args.nickname)
    app.run()


if __name__ == "__main__":
    main()
