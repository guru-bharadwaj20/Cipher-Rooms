# Pulse-Chat: Secure Multi-Client Chat System

Pulse-Chat is a TLS-enabled Python chat system built for a computer networks course project. It supports multi-client messaging, room-based chat, direct messaging, secure file transfer, reconnect behavior, persistence, benchmarking, and protocol hardening.

Built for: Computer Networks Course Project  
Language: Python 3.7+  
Dependencies: Python standard library only

---

## Project Structure

```text
Pulse-Chat/
├── certs/                      # TLS cert/key generation + generated certs
├── client/                     # Interactive terminal client
├── server/                     # Server + per-client handler + persistence
├── utils/                      # Protocol constants/encode/decode/validation
├── benchmarks/                 # Load/performance harness
├── reports/                    # Generated benchmark reports
├── run_server.py               # Server entry point
├── run_client.py               # Client entry point
├── requirements.txt            # Course scaffold (stdlib project)
└── README.md
```

---

## Implemented Updates (10)

1. Client reconnection thread
- Client continues running when server disconnects and reconnects automatically.

2. Persistent user profiles and relogin history
- Login metadata and chat history are persisted in data files and replayed on login.

3. Multi-room chat support
- Room join/leave operations and room-scoped broadcast behavior are implemented.

4. Private messaging
- Direct messaging with recipient routing and server-side error feedback.

5. File transfer flow
- Offer/chunk/end/ack/error message flow implemented with transfer IDs.

6. Join/leave/failure handling
- Robust disconnect cleanup, transfer-failure notification, and room transition handling.

7. Performance evaluation harness
- Synthetic load script measures latency/throughput/error/disconnect/reconnect metrics and writes CSV/summary reports.

8. Protocol completeness and versioning
- Protocol v1.1 with required fields, validation, and structured error codes.

9. File-sharing security hardening
- Filename sanitization, path traversal blocking, file size/type policy, chunk limits, and checksum verification.

10. Scalability architecture hardening
- Per-client bounded outbound queue and sender loop to isolate slow clients and protect room throughput.

---

## Core Features

- TLS-encrypted client/server transport over TCP.
- Thread-per-client server model.
- Room chat, direct messages, and file transfer.
- Persistent user profiles + history replay.
- Protocol validation with early malformed-payload rejection.
- Backpressure and bounded queues for slow-client isolation.
- Benchmark tooling for scalability/performance analysis.

---

## Protocol Specification (v1.1)

Transport:
- Newline-delimited JSON frames.
- Every frame includes protocol_version.

Base fields (all frames):
- type
- username
- content
- timestamp (ISO-8601)
- protocol_version (1.1)

Message types:
- chat, join, leave, system, error, ack
- private
- room_join, room_leave, room_list
- file_offer, file_chunk, file_end, file_ack, file_error

Validation behavior:
- Server validates required fields and message type before business logic.
- Malformed payloads are rejected with error + error_code.

Error codes:
- ERR_BAD_FORMAT
- ERR_UNKNOWN_TYPE
- ERR_MISSING_FIELD
- ERR_INVALID_FIELD

Ordering rules:
- TCP preserves per-connection byte order.
- Room display order follows server receive order.
- File reassembly uses transfer_id + chunk_index.

---

## Security Hardening

File sharing protections:
- Filename sanitization on receive.
- Path traversal blocked (files restricted to downloads folder).
- Maximum file size policy enforced.
- Allowed extension/MIME policy checks before acceptance.
- Chunk-size guards in client and server paths.
- SHA-256 checksum verification at receiver.
- Checksum mismatch triggers file_error + discard.
- Server-side file chunk rate limit to reduce abuse.

TLS trust model note (important):
- For class/demo use, client certificate verification is disabled.
- This keeps transport encrypted but does not strongly authenticate server identity.
- Production setup should use CA trust + hostname verification.

---

## Scalability Design

Current model:
- Thread per client (course-appropriate).

Hardening added:
- Bounded per-client outgoing queue.
- Dedicated sender loop per client.
- Queue overflow policy disconnects persistently slow consumers.
- Broadcast path enqueues only (prevents slow clients from blocking room send path).
- Bounded active transfer state and bounded in-memory history reads.

Goal achieved:
- One slow client should not degrade whole room performance by blocking broadcast writes.

---

## Running the Project

1) Start server

```bash
python run_server.py
```

2) Start client

```bash
python run_client.py localhost 5555 <username>
```

Client commands:
- /join <room>
- /leave
- /dm <user> <message>
- /sendfile <user> <path>
- /quit

---

## Benchmark and Reports

Run load test:

```bash
python benchmarks/load_test.py --host localhost --port 5555 --clients 5,20,50,100
```

Generated artifacts:
- reports/performance_metrics.csv
- reports/performance_summary.md

Measured metrics:
- latency (avg, p95)
- throughput (msg/sec)
- error rate
- disconnect rate
- reconnect average/success rate

Scenarios covered:
- room chat
- direct messaging
- file transfer

---

## Persistence

Stored under data/:
- profiles.json
- history/*.jsonl

What is persisted:
- user profile metadata
- login count and last seen
- per-user message history replay window

---

## Current Limitations

- Uses self-signed certs by default.
- Demo trust model disables strict certificate verification on client side.
- Thread-per-client (not asyncio/event-loop architecture).
- No external DB; persistence is local JSON/JSONL.

---

## Next Possible Enhancements

- Optional asyncio server path for higher fan-out.
- Strong authn/authz (tokens/user credentials).
- Production TLS trust configuration.
- Extended conformance tests for protocol compatibility.
- Automated plotting pipeline from benchmark CSV.
