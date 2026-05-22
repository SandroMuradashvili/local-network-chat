# LNCPv1 — Local Network Chat Protocol

> **Course Assignment — Computer Networking (.NET)**
> Submission Deadline: June 15, 2026 — 9:00 AM

A fully custom application-layer protocol for real-time, text-based peer-to-peer communication over a local area network. Includes a complete **protocol specification document** and a working **Python reference implementation**.

---

## Assignment Requirements vs. Implementation

### ✅ Part 1 — Two Applications

| Required | Implemented |
|---|---|
| Initiator Application — starts communication | `initiator.py` — broadcasts discovery, listens for TCP, drives the chat |
| Recipient Application — receives and responds | `recipient.py` — listens on UDP, accepts/rejects, responds to messages |

---

### ✅ Part 2 — Communication Workflow

#### Step 1: Discovery via UDP Broadcast

The **Initiator** sends a UDP datagram to the local broadcast address (`255.255.255.255`) on well-known port `54321`. The broadcast payload includes all four required fields:

| Required Field | Implementation |
|---|---|
| Recipient's nickname | `payload.recipient` — string nickname of the intended partner |
| Deadline | `payload.deadline` — Unix epoch float; requests after this time are rejected |
| TCP port number | `payload.tcp_port` — port the Initiator will listen on (default `55000`) |
| Random UUID | `payload.uuid` — RFC-4122 UUID v4 generated fresh per request |

After broadcasting, the Initiator immediately begins listening on the specified TCP port.

#### Step 2: TCP Connection

- Every Recipient on the LAN receives the UDP broadcast.
- It checks whether its own nickname matches `payload.recipient`.
- It checks whether the current time is before `payload.deadline`.
- If both pass, the user is prompted: `Accept connection request? [Y/n]`.
- On acceptance, the Recipient opens a **TCP connection back to the Initiator** using the source IP from the UDP datagram and the `tcp_port` from the payload.

#### Step 3: Handshaking

| Step | Message | Direction | Purpose |
|---|---|---|---|
| 1 | `HANDSHAKE_REQ` | Recipient → Initiator | Sends UUID from DISCOVER |
| 2a | `HANDSHAKE_ACK` | Initiator → Recipient | UUID valid + deadline not expired |
| 2b | `HANDSHAKE_NAK` | Initiator → Recipient | UUID mismatch or deadline expired |

The Initiator verifies two conditions before accepting:
1. The UUID in `HANDSHAKE_REQ` matches the UUID from the original broadcast.
2. The current time is still before `deadline`.

Rejection includes a machine-readable `reason` field: `uuid_mismatch`, `deadline_expired`, or `malformed_message`.

#### Step 4: Message Exchange

- Communication proceeds over TCP in **simplex, stop-and-wait mode**: the sender must receive a `TEXT_ACK` before sending the next `TEXT`.
- The Initiator always sends first.
- Either party can send `CLOSE_REQ` at any time to begin graceful termination.
- The peer responds with `CLOSE_ACK`, then both sides close the TCP socket.

All messages carry a `type` field identifying their purpose — satisfying the requirement for a type prefix on every message.

---

### ✅ Part 3 — Protocol Design

The protocol is formally named **LNCPv1** (Local Network Chat Protocol, Version 1). It was designed by studying existing application-layer protocols (HTTP, SMTP, FTP) and applying the same principles of structured message envelopes, explicit state transitions, and defined error codes.

#### Message Format

Every LNCPv1 message is a **single-line UTF-8 JSON object**, terminated by a newline (`0x0A`). All messages share a common envelope:

```json
{
  "version":   "LNCPv1",
  "type":      "<MSG_TYPE>",
  "timestamp": "<ISO-8601-UTC>",
  "payload":   { ... }
}
```

The newline delimiter allows simple line-reader parsing — the same approach used by SMTP and Redis's RESP protocol.

#### All 8 Message Types

| Type | Transport | Direction | Purpose |
|---|---|---|---|
| `DISCOVER` | UDP | I → R | Broadcast discovery with UUID, deadline, TCP port |
| `HANDSHAKE_REQ` | TCP | R → I | UUID correlation request |
| `HANDSHAKE_ACK` | TCP | I → R | Session accepted |
| `HANDSHAKE_NAK` | TCP | I → R | Session rejected with reason code |
| `TEXT` | TCP | I ↔ R | Chat message (max 4,000 chars) |
| `TEXT_ACK` | TCP | I ↔ R | Delivery confirmation |
| `CLOSE_REQ` | TCP | I ↔ R | Graceful termination request |
| `CLOSE_ACK` | TCP | I ↔ R | Termination acknowledged |

#### Encoding Rules

1. All messages encoded as **UTF-8**
2. Newline (`0x0A`) framing — one message per line
3. Timestamps in **ISO-8601 UTC** format
4. UUIDs as **RFC-4122 v4** lowercase with hyphens
5. Deadline as **Unix epoch float**
6. No `null` values — absent optional fields are omitted entirely
7. Field ordering within JSON is not significant
8. Max message size: **8,192 bytes**

#### State Transitions

**Initiator:** `IDLE → DISCOVERING → HANDSHAKING → CHATTING → CLOSING → IDLE`

**Recipient:** `LISTENING → CONNECTING → HANDSHAKING → CHATTING → CLOSING → LISTENING`

Receiving an unexpected message type in any state is a protocol error and triggers graceful close.

#### Error Handling

| Error | Detected By | Response |
|---|---|---|
| UUID mismatch | Initiator | `HANDSHAKE_NAK {reason: uuid_mismatch}` |
| Deadline expired | Initiator | `HANDSHAKE_NAK {reason: deadline_expired}` |
| Malformed JSON | Either | `HANDSHAKE_NAK` or `CLOSE_REQ`, then close |
| Unknown message type | Either | Log and close |
| Wrong protocol version | Either | Discard (UDP) or close (TCP) |
| TEXT_ACK timeout (30s) | Sender | Log warning; send `CLOSE_REQ` |
| TCP connection reset | Either | Return to `IDLE`/`LISTENING` |
| Recipient not found | Initiator | Log and exit after deadline |
| TEXT body > 4,000 chars | Recipient | Truncate or reject with `CLOSE_REQ` |

---

### ✅ Part 4 — Deliverables

#### 1. Protocol Specification Document

`LNCP_Protocol_Specification.docx` — a 9-section technical document:

1. **Overview and Design Goals** — design rationale, comparison table vs. HTTP/SMTP
2. **System Architecture** — roles, port assignments, transport design decisions
3. **Message Format** — common envelope, all 8 message types with field tables and examples
4. **Communication Workflow** — 4 phases with numbered steps and ASCII sequence diagram
5. **State Transitions** — state tables for both Initiator and Recipient
6. **Error Handling** — 9 error conditions with required behaviour
7. **Encoding Rules** — 8 numbered encoding rules
8. **Assumptions and Limitations** — scope boundaries, security notes
9. **Implementation Notes** — how to run and test

#### 2. Implementation

`initiator.py` and `recipient.py` — working Python 3 implementation using **standard library only** (`socket`, `threading`, `uuid`, `json`, `argparse`). No third-party packages required.

---

## How to Run

### Requirements
- Python 3.7+
- Two terminal windows (same machine or two machines on the same LAN)

### Terminal 1 — Start the Recipient
```bash
python recipient.py alice
```

### Terminal 2 — Start the Initiator
```bash
python initiator.py alice
```

### Optional flags
```bash
python initiator.py alice --deadline 60   # wait up to 60 seconds
python initiator.py alice --port 55001    # use a different TCP port
```

### Expected flow
```
Terminal 2: [DISCOVER] Broadcast sent → recipient='alice' uuid=... deadline=30s
Terminal 1: Accept connection request? [Y/n]: Y
Terminal 2: [HANDSHAKE] ✓ Handshake successful
Terminal 2: You: Hello!
Terminal 1: Peer: Hello!
```

Type `/quit` in either terminal to close the session gracefully.

### Single-machine note
If Terminal 1 never sees the discovery broadcast, replace `UDP_BROADCAST = "<broadcast>"` in `initiator.py` with your machine's LAN IP address (find it with `ipconfig` on Windows or `ip a` on Linux/Mac).

---

## Project Structure

```
├── initiator.py                    # Initiator application
├── recipient.py                    # Recipient application
├── LNCP_Protocol_Specification.docx  # Full protocol spec document
└── README.md                       # This file
```

---

## Design Decisions

**Why JSON instead of plain text?**
The assignment requires a "type prefix" on every message. Plain text like `TEXT:hello` works but is fragile and hard to extend. JSON with a typed envelope mirrors how production protocols (MQTT, JSON-RPC, WebSocket subprotocols) actually work, and makes the protocol versioned and parseable without custom string splitting.

**Why UDP for discovery + TCP for chat?**
The same separation used by SIP (VoIP) and mDNS: connectionless broadcast for low-overhead peer discovery where reliability doesn't matter, followed by a reliable ordered byte stream for the actual data. UDP broadcast also means no pre-shared IP addresses are needed.

**Why newline framing?**
A newline-delimited JSON stream (NDJSON) is the simplest possible framing: no length prefix to compute, no binary header to parse. The same approach is used by SMTP, Redis RESP, and Docker's log streaming.