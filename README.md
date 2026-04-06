# Pulse-Chat: Secure Multi-Client Chat Server

A secure, multi-client chat server implementation using Python with SSL/TLS encryption over TCP. This project demonstrates core networking concepts including socket programming, TLS encryption, concurrent client handling, and message broadcasting.

**Built for:** Computer Networks Course Project  
**Language:** Python 3.7+  
**Dependencies:** Standard library only (socket, ssl, threading, json)

---

## 🎯 Features

- **Secure Communication:** SSL/TLS encryption using self-signed certificates
- **Multi-Client Support:** Concurrent client handling using threads
- **Real-Time Messaging:** Broadcast messages to all connected clients
- **Persistent User Profiles:** Returning users are recognized with login metadata
- **Chat History Replay:** User-specific previous chats are loaded on login
- **Performance Benchmarking:** Synthetic load tests for room chat, DM, and file transfer
- **Graceful Error Handling:** Proper handling of disconnections and errors
- **Modular Architecture:** Clean separation of concerns with well-documented code
- **Educational Comments:** Detailed explanations of networking concepts

---

## 📁 Project Structure

```
Pulse-Chat/
├── certs/                          # SSL/TLS Certificates
│   ├── generate_certs.sh           # Script to generate self-signed certificates
│   ├── server.key                  # Private key (generated)
│   └── server.crt                  # Public certificate (generated)
│
├── server/                         # Server implementation
│   ├── __init__.py
│   ├── chat_server.py              # Main server logic (TCP, SSL, accept loop)
│   └── client_handler.py           # Individual client handler (threading)
│
├── client/                         # Client implementation
│   ├── __init__.py
│   └── chat_client.py              # Client connection and messaging
│
├── utils/                          # Shared utilities
│   ├── __init__.py
│   └── message_protocol.py         # Message encoding/decoding (JSON)
│
├── benchmarks/
│   └── load_test.py                # Load test harness + metrics writer
│
├── reports/                        # Generated benchmark artifacts (CSV/MD)
│
├── run_server.py                   # Server entry point
├── run_client.py                   # Client entry point
├── .gitignore
└── README.md
```

---

## 🏗️ Architecture Overview

### System Design

```
┌─────────────┐                    ┌─────────────────────────────────┐
│   Client 1  │───┐                │         Chat Server             │
└─────────────┘   │                │  ┌───────────────────────────┐  │
                  │   TLS/TCP      │  │   Main Thread             │  │
┌─────────────┐   ├────────────────┼─▶│  - Accept connections     │  │
│   Client 2  │───┤                │  │  - SSL/TLS handshake      │  │
└─────────────┘   │                │  │  - Spawn client threads   │  │
                  │                │  └───────────────────────────┘  │
┌─────────────┐   │                │                                 │
│   Client N  │───┘                │  ┌───────────────────────────┐  │
└─────────────┘                    │  │   Client Handler Threads  │  │
                                   │  │  - Thread 1 (Client 1)    │  │
                                   │  │  - Thread 2 (Client 2)    │  │
                                   │  │  - Thread N (Client N)    │  │
                                   │  │  - Broadcast to all       │  │
                                   │  └───────────────────────────┘  │
                                   └─────────────────────────────────┘
```

### Key Components

**1. Server (`server/chat_server.py`)**
- Creates TCP socket and binds to port
- Wraps socket with SSL/TLS
- Accepts incoming client connections
- Spawns a new thread for each client
- Manages client list with thread-safe operations
- Coordinates message broadcasting

**2. Client Handler (`server/client_handler.py`)**
- Runs in a separate thread per client
- Handles receiving messages from one client
- Sends messages to one client
- Manages client lifecycle (join/leave)
- Sends login context (welcome + history replay)
- Thread-safe communication with server

**3. User Store (`server/user_store.py`)**
- Persists user profiles in `data/profiles.json`
- Persists per-user chat history in `data/history/*.jsonl`
- Updates `last_seen` when users disconnect

**4. Client (`client/chat_client.py`)**
- Connects to server via TLS
- Runs receive loop in separate thread
- Handles user input in main thread
- Displays incoming messages

**5. Message Protocol (`utils/message_protocol.py`)**
- JSON-based message format
- Message types: chat, join, leave, system, error
- Encoding/decoding utilities
- Message formatting for display

---

## 📐 Protocol Specification

### Protocol Version

- Current version: `1.1`
- Every frame is newline-delimited JSON (`\n` terminated)
- Every frame must include `protocol_version`

### Base Message Schema (All Types)

All messages share these required fields:

```json
{
    "type": "<message_type>",
    "username": "<sender_username>",
    "content": "<human_readable_content>",
    "timestamp": "<iso-8601>",
    "protocol_version": "1.1"
}
```

### Message Types And Required Extra Fields

- `chat`: no extra required fields
- `join`: no extra required fields
- `leave`: no extra required fields
- `system`: no extra required fields
- `error`: optional `error_code`
- `ack`: required `ack_for`
- `private`: required `to_username`, `message_id`
- `room_join`: required `room_name`
- `room_leave`: no extra required fields (server may include `room_name`)
- `room_list`: required `rooms` (array)
- `file_offer`: required `to_username`, `transfer_id`, `filename`, `size`, `checksum`, `total_chunks`
- `file_chunk`: required `to_username`, `transfer_id`, `chunk_index`, `total_chunks`, `chunk_data`
- `file_end`: required `to_username`, `transfer_id`
- `file_ack`: required `to_username`, `transfer_id`
- `file_error`: required `transfer_id`

### Example Frames

Private message:

```json
{
    "type": "private",
    "username": "alice",
    "to_username": "bob",
    "message_id": "9f3a7e7a-c2f7-4fb0-8d12-3ea2f7f55ac1",
    "content": "hello bob",
    "timestamp": "2026-04-06T18:45:00.123456",
    "protocol_version": "1.1"
}
```

Room join:

```json
{
    "type": "room_join",
    "username": "alice",
    "room_name": "networks",
    "content": "join networks",
    "timestamp": "2026-04-06T18:45:04.000000",
    "protocol_version": "1.1"
}
```

File offer:

```json
{
    "type": "file_offer",
    "username": "alice",
    "to_username": "bob",
    "transfer_id": "d71eaec0-4c7a-4bd8-8a60-c90a4f63f4d9",
    "filename": "report.pdf",
    "size": 245760,
    "checksum": "<sha256_hex>",
    "total_chunks": 16,
    "content": "file offer",
    "timestamp": "2026-04-06T18:45:09.700000",
    "protocol_version": "1.1"
}
```

Validation error response:

```json
{
    "type": "error",
    "username": "Server",
    "content": "Missing required field(s): transfer_id",
    "error_code": "ERR_MISSING_FIELD",
    "timestamp": "2026-04-06T18:45:10.400000",
    "protocol_version": "1.1"
}
```

### Error Codes

- `ERR_BAD_FORMAT`: payload is not valid JSON object or frame shape is invalid
- `ERR_UNKNOWN_TYPE`: unknown `type`
- `ERR_MISSING_FIELD`: one or more required fields are absent
- `ERR_INVALID_FIELD`: field exists but type/value is invalid

### Ordering Rules

- Transport is TCP+TLS, so per-connection byte order is preserved.
- For room chat, clients should use server receive order as canonical display order.
- For file transfer, process by `transfer_id` and `chunk_index`.
- `file_end` completes transfer assembly; `file_ack` indicates receiver verification success.
- Client implementations should ignore unknown extra fields for forward compatibility.

### Validation Behavior

- Server validates all inbound payloads in `server/client_handler.py` before command handling.
- Malformed frames are rejected early with an `error` frame and do not enter room/DM/file logic.

## 🔐 SSL/TLS Setup

### What is SSL/TLS?

**SSL/TLS** (Secure Sockets Layer / Transport Layer Security) provides:
- **Encryption:** Data transmitted is encrypted, preventing eavesdropping
- **Authentication:** Server identity is verified through certificates
- **Integrity:** Data cannot be tampered with during transmission

### How it Works in This Project

1. **Server Side:**
   - Server has a certificate (`server.crt`) and private key (`server.key`)
   - Server wraps its socket with SSL context
   - When client connects, automatic TLS handshake occurs
   - All subsequent communication is encrypted

2. **Client Side:**
   - Client creates SSL context
   - Client wraps its socket with SSL
   - Connects to server (TLS handshake)
   - For self-signed certs, certificate verification is disabled

### TLS Trust Model Limitation (Class Demo)

- Current client configuration uses `verify_mode=CERT_NONE` and `check_hostname=False` for local/demo use.
- This means transport is encrypted, but server identity is not strongly authenticated.
- For production, replace with CA-based certificate validation and hostname verification.

### File Sharing Security Controls

- Filename sanitization on receive: strips path traversal and unsafe characters.
- Path traversal blocked: file saves are restricted to client `downloads/` directory.
- Maximum file size enforced on send and receive (10 MB).
- Allowed extension/MIME policy enforced before transfer acceptance.
- Chunk-size checks enforced on client and server.
- SHA-256 checksum verified at receiver; checksum mismatch triggers `file_error` and file discard.
- Server enforces file-chunk rate limits to reduce abuse and protect shared performance.

### Self-Signed Certificates

For this educational project, we use **self-signed certificates**:
- Not signed by a trusted Certificate Authority (CA)
- Perfect for development and learning
- **Not recommended for production** (use Let's Encrypt or commercial CA)

---

## 🚀 Getting Started

### Prerequisites

- Python 3.7 or higher
- OpenSSL (usually pre-installed on macOS/Linux)

### Step 1: Generate SSL Certificates

Before running the server, you need to generate SSL certificates:

```bash
cd certs
./generate_certs.sh
```

Or manually using OpenSSL:

```bash
cd certs

# Generate private key and certificate
openssl req -x509 -newkey rsa:2048 \
    -keyout server.key \
    -out server.crt \
    -days 365 \
    -nodes \
    -subj "/C=US/ST=State/L=City/O=PulseChat/OU=Development/CN=localhost"

# Set appropriate permissions
chmod 600 server.key
chmod 644 server.crt
```

**OpenSSL Command Breakdown:**
- `-x509`: Output a self-signed certificate
- `-newkey rsa:2048`: Generate 2048-bit RSA key
- `-keyout`: Output file for private key
- `-out`: Output file for certificate
- `-days 365`: Certificate valid for 1 year
- `-nodes`: Don't encrypt the private key (no passphrase)
- `-subj`: Certificate subject information

### Step 2: Start the Server

```bash
# From project root
python run_server.py

# Or with custom host/port
python run_server.py 0.0.0.0 8080
```

The server will:
- Bind to the specified address and port (default: `0.0.0.0:5555`)
- Load SSL certificates
- Wait for client connections

### Step 3: Start Clients

Open multiple terminal windows and run:

```bash
# Terminal 1
python run_client.py

# Terminal 2
python run_client.py

# Terminal 3
python run_client.py
```

**Command-line options:**
```bash
python run_client.py [host] [port] [username]

# Examples:
python run_client.py localhost 5555         # Connect to localhost:5555
python run_client.py localhost 5555 Alice   # With username
python run_client.py 192.168.1.100 8080     # Remote server
```

### Step 4: Run Performance Benchmarks

In a new terminal (with server running):

```bash
python benchmarks/load_test.py --host localhost --port 5555 --clients 5,20,50,100
```

Outputs:
- `reports/performance_metrics.csv`
- `reports/performance_summary.md`

Measured metrics:
- latency (avg, p95)
- throughput (messages/sec)
- error rate
- disconnect rate
- reconnect time

Scenarios covered:
- room chat
- private messaging
- file transfer

---

## 💬 Usage

### Client Commands

Once connected:
- **Type any message** and press Enter to send
- **Type `/quit`** to disconnect
- **Press Ctrl+C** or **Ctrl+D** to exit

### Example Session

**Server Output:**
```
======================================================================
PULSE-CHAT SECURE SERVER
======================================================================

[SERVER] Secure chat server started on 0.0.0.0:5555
[SERVER] Using certificate: certs/server.crt
[SERVER] Waiting for connections...

[SERVER] New connection from ('127.0.0.1', 54321)
[SERVER] Alice connected from ('127.0.0.1', 54321)
[SERVER] New connection from ('127.0.0.1', 54322)
[SERVER] Bob connected from ('127.0.0.1', 54322)
[Alice]: Hello everyone!
[Bob]: Hey Alice!
```

**Client Output (Alice):**
```
======================================================================
PULSE-CHAT CLIENT
======================================================================

[CLIENT] Connecting to localhost:5555...
[CLIENT] Secure connection established!
[CLIENT] Using cipher: ('TLS_AES_256_GCM_SHA384', 'TLSv1.3', 256)

============================================================
Connected to Pulse-Chat!
Type your messages and press Enter to send.
Type '/quit' to exit.
============================================================

*** Bob joined the chat ***
Hello everyone!
[Bob]: Hey Alice!
```

---

## 🔍 Key Networking Concepts Explained

### 1. TCP Socket Programming

**Socket:** An endpoint for sending/receiving data across a network

```python
# Create a TCP socket
socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# AF_INET = IPv4, SOCK_STREAM = TCP (reliable, connection-oriented)
```

**Server Process:**
1. `socket()` - Create socket
2. `bind(address, port)` - Bind to address
3. `listen(backlog)` - Mark as server socket
4. `accept()` - Accept client connections (blocking)

**Client Process:**
1. `socket()` - Create socket
2. `connect(address, port)` - Connect to server

### 2. SSL/TLS Handshake

When a client connects to the server, a TLS handshake occurs:

1. **Client Hello:** Client sends supported cipher suites
2. **Server Hello:** Server chooses cipher suite, sends certificate
3. **Key Exchange:** Client and server establish encryption keys
4. **Finished:** Both sides confirm handshake success

After handshake, all data is encrypted.

### 3. Multi-Threading

Each client runs in its own thread, allowing:
- **Concurrent Handling:** Multiple clients simultaneously
- **Independent Execution:** One client doesn't block others
- **Shared Resources:** Threads share memory (requires locks)

```python
# Thread-safe access to shared client list
with self.clients_lock:
    self.clients.append(client_handler)
```

### 4. Message Framing

Messages need delimiters to know where one ends and another begins:
- **Line-based:** Each message ends with `\n` (our approach)
- **Length-prefix:** Message starts with its length
- **Fixed-size:** All messages same size (inefficient)

```python
# Send with newline delimiter
message_bytes = (json_str + "\n").encode('utf-8')

# Receive line-by-line
data = socket_file.readline()
```

---

## 🛠️ Development Guide

### Adding New Message Types

1. Add constant to `MessageProtocol` class:
```python
TYPE_PRIVATE = "private"
```

2. Handle in client handler:
```python
if msg_type == MessageProtocol.TYPE_PRIVATE:
    # Handle private message
    pass
```

### Implementing Private Messaging

To add private messaging:
1. Modify message format to include `recipient` field
2. Update client handler to check recipient
3. Send only to target client instead of broadcasting

### Error Handling

Key areas with error handling:
- Socket operations (connection failures)
- SSL errors (handshake failures, certificate issues)
- Message encoding/decoding (malformed JSON)
- Thread synchronization (race conditions)

---

## 📚 Educational Value

This project demonstrates:

**Networking Concepts:**
- TCP/IP socket programming
- Client-server architecture
- SSL/TLS encryption
- Message protocols and framing

**Software Engineering:**
- Modular design
- Thread safety and concurrency
- Error handling and resource management
- Clean code architecture

**Security:**
- Encryption in transit
- Certificate-based authentication
- Secure key management

---

## ⚠️ Known Limitations

- Self-signed certificates (verification disabled)
- No authentication (anyone can connect)
- No message persistence (messages not saved)
- No private messaging
- No user management
- Basic error recovery

**For Production Use, Add:**
- Proper CA-signed certificates
- User authentication and authorization
- Database for message history
- Reconnection logic
- Rate limiting
- Input validation and sanitization
- Logging and monitoring

---

## 🧪 Testing

### Test Scenarios

1. **Single Client:** Connect one client, send messages
2. **Multiple Clients:** Connect 3+ clients, verify broadcasting
3. **Client Disconnect:** Close client, verify others receive leave message
4. **Server Restart:** Stop server, restart, verify clients can reconnect
5. **Invalid Input:** Send empty messages, very long messages
6. **Network Issues:** Test with firewall, network delays

### Manual Testing

```bash
# Terminal 1: Start server
python run_server.py

# Terminal 2-4: Start multiple clients
python run_client.py localhost 5555 Alice
python run_client.py localhost 5555 Bob
python run_client.py localhost 5555 Charlie

# Test scenarios:
# - Send messages from each client
# - Close one client and observe others
# - Send rapid-fire messages
# - Test /quit command
```

---

## 📖 Further Learning

**Next Steps:**
- Implement async I/O with `asyncio` instead of threading
- Add private messaging between users
- Implement chat rooms/channels
- Add file transfer capability
- Create a GUI client with tkinter
- Implement proper authentication (OAuth2, JWT)
- Add message encryption (end-to-end)
- Use a database for message history (SQLite, PostgreSQL)

**Related Topics:**
- WebSocket for web-based chat
- HTTP/2 and gRPC
- Message queues (RabbitMQ, Kafka)
- Distributed systems
- Load balancing and scaling

---

## 🤝 Contributing

This is an educational project. Feel free to:
- Fork and experiment
- Add new features
- Improve documentation
- Report issues or suggest improvements

---

## 📝 License

This project is created for educational purposes. Feel free to use and modify for learning.

---

## 👤 Author

Created for Computer Networks Course Project

---

## 🙏 Acknowledgments

- Python standard library documentation
- OpenSSL documentation
- Computer Networks course materials

---

**Happy Coding! 🚀**