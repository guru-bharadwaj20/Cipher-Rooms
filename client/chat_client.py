"""
Chat Client Module

This module implements the chat client that:
1. Connects to the server over TLS
2. Sends messages typed by the user
3. Receives and displays messages from other users
4. Handles the connection lifecycle

Key Networking Concepts:
- TCP Client Socket: Initiates connection to server
- SSL/TLS Client: Validates server certificate, encrypts traffic
- Threading: Separate thread for receiving messages (allows concurrent send/receive)
- Non-blocking I/O: User can type while receiving messages
"""

import socket
import ssl
import threading
import sys
import time
import os
import uuid
import base64
import hashlib
import re
import mimetypes
from utils.message_protocol import MessageProtocol


class ChatClient:
    """
    Secure chat client that connects to the chat server via TLS.
    """

    MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
    MAX_CHUNK_SIZE_BYTES = 32 * 1024        # 32 KB
    ALLOWED_EXTENSIONS = {
        '.txt', '.md', '.csv', '.json', '.log',
        '.png', '.jpg', '.jpeg', '.gif', '.webp',
        '.pdf'
    }
    
    def __init__(
        self,
        server_host: str = 'localhost',
        server_port: int = 5555,
        username: str = None
    ):
        """
        Initialize the chat client.
        
        Args:
            server_host: Hostname or IP address of the chat server
            server_port: Port number the server is listening on
            username: Username for this client
        """
        self.server_host = server_host
        self.server_port = server_port
        self.username = username or self._get_username()
        self.reconnect_delay = 3
        
        self.socket = None
        self.running = False
        self.connected = False
        self.receive_thread = None
        self.connection_thread = None
        self.socket_lock = threading.Lock()
        self.current_room = 'lobby'
        self.file_chunk_size = 16 * 1024
        self.download_dir = os.path.join(os.getcwd(), 'downloads')
        self.incoming_file_transfers = {}
        self.last_dm_target = None
        self.last_file_send_command = None

    def _sanitize_filename(self, raw_name: str) -> str:
        """Sanitize incoming filename to prevent traversal and unsafe paths."""
        candidate = os.path.basename((raw_name or '').strip())
        candidate = candidate.replace('..', '')
        candidate = re.sub(r'[^A-Za-z0-9._-]', '_', candidate)
        if not candidate or candidate in {'.', '..'}:
            return ''
        return candidate[:128]

    def _is_allowed_file_type(self, filename: str) -> bool:
        ext = os.path.splitext(filename)[1].lower()
        if ext not in self.ALLOWED_EXTENSIONS:
            return False
        guessed, _ = mimetypes.guess_type(filename)
        if guessed and not guessed.startswith(('text/', 'image/', 'application/pdf', 'application/json')):
            return False
        return True
    
    def _get_username(self) -> str:
        """Prompt user for their username."""
        username = input("Enter your username: ").strip()
        while not username:
            username = input("Username cannot be empty. Try again: ").strip()
        return username
    
    def connect(self):
        """
        Connect to the chat server using TLS.
        
        Process:
        1. Create a TCP socket
        2. Create an SSL context with certificate verification disabled
           (since we're using self-signed certs)
        3. Wrap the socket with SSL
        4. Connect to the server (TLS handshake occurs here)
        5. Send join message with username
        """
        try:
            with self.socket_lock:
                if self.connected:
                    return True

            # Step 1: Create a TCP socket
            client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            
            # Step 2: Create SSL context for the client
            # We use PROTOCOL_TLS_CLIENT which enforces certificate checking by default
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            
            # For self-signed certificates, we need to disable certificate verification
            # In production, you would load the CA certificate and verify properly
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            # Step 3: Wrap the socket with SSL
            secure_socket = ssl_context.wrap_socket(
                client_socket,
                server_hostname=self.server_host
            )
            
            # Step 4: Connect to the server
            # The TLS handshake happens here automatically
            print(f"[CLIENT] Connecting to {self.server_host}:{self.server_port}...")
            secure_socket.connect((self.server_host, self.server_port))
            print(f"[CLIENT] Secure connection established!")
            print(f"[CLIENT] Using cipher: {secure_socket.cipher()}\n")

            with self.socket_lock:
                self.socket = secure_socket
                self.connected = True
            
            # Step 5: Send join message
            join_message = MessageProtocol.create_message(
                MessageProtocol.TYPE_JOIN,
                self.username,
                f"{self.username} joined"
            )
            self._send_message(join_message)

            self.receive_thread = threading.Thread(
                target=self._receive_messages,
                daemon=True
            )
            self.receive_thread.start()

            return True
        
        except ConnectionRefusedError:
            print("[CLIENT] Error: Could not connect to server. Is it running?")
            return False
        except ssl.SSLError as e:
            print(f"[CLIENT] SSL Error: {e}")
            return False
        except Exception as e:
            print(f"[CLIENT] Connection error: {e}")
            return False

    def _mark_disconnected(self, reason: str = None):
        """Mark the connection as disconnected and safely close the socket."""
        socket_to_close = None
        with self.socket_lock:
            if self.connected and reason:
                print(f"\n[CLIENT] {reason}")
            self.connected = False
            socket_to_close = self.socket
            self.socket = None

        if socket_to_close:
            try:
                socket_to_close.close()
            except Exception:
                pass
    
    def _send_message(self, message: dict):
        """
        Send a message to the server.
        
        Args:
            message: Message dictionary to send
        """
        try:
            with self.socket_lock:
                sock = self.socket

            if not sock:
                raise ConnectionError("Not connected to server")

            encoded = MessageProtocol.encode_message(message)
            sock.sendall(encoded)
        except Exception as e:
            print(f"[CLIENT] Error sending message: {e}")
            self._mark_disconnected("Connection lost while sending message")
    
    def _receive_messages(self):
        """
        Continuously receive messages from the server.
        Runs in a separate thread to allow simultaneous send/receive.
        """
        try:
            with self.socket_lock:
                sock = self.socket

            if not sock:
                return

            # Create a file-like object for line-based reading
            server_file = sock.makefile('rb')
            
            while self.running and self.connected:
                # Read one line (one message) from the server
                data = server_file.readline()
                
                if not data:
                    # Server disconnected
                    self._mark_disconnected("Disconnected from server. Reconnecting...")
                    break
                
                # Decode and display the message
                message = MessageProtocol.decode_message(data)
                if message:
                    msg_type = message.get("type")

                    if msg_type == MessageProtocol.TYPE_FILE_OFFER:
                        self._handle_file_offer(message)
                        continue
                    if msg_type == MessageProtocol.TYPE_FILE_CHUNK:
                        self._handle_file_chunk(message)
                        continue
                    if msg_type == MessageProtocol.TYPE_FILE_END:
                        self._handle_file_end(message)
                        continue
                    if msg_type == MessageProtocol.TYPE_FILE_ACK:
                        print(MessageProtocol.format_display_message(message))
                        continue
                    if msg_type == MessageProtocol.TYPE_FILE_ERROR:
                        print(MessageProtocol.format_display_message(message))
                        print("[CLIENT] Retry: run /sendfile <username> <file_path> again.")
                        continue
                    if msg_type == MessageProtocol.TYPE_ERROR:
                        print(MessageProtocol.format_display_message(message))
                        if "DM" in str(message.get("content", "")).upper() or "offline" in str(message.get("content", "")).lower():
                            if self.last_dm_target:
                                print(f"[CLIENT] Retry DM later: /dm {self.last_dm_target} <message>")
                        continue

                    # Format and display the message
                    display_text = MessageProtocol.format_display_message(message)
                    print(display_text)
        
        except ConnectionResetError:
            self._mark_disconnected("Connection lost. Reconnecting...")
        except Exception as e:
            if self.running and self.connected:
                self._mark_disconnected(f"Error receiving messages: {e}")

    def _connection_manager(self):
        """Maintain connection by reconnecting until the client is stopped."""
        while self.running:
            if not self.connected:
                connected = self.connect()
                if not connected and self.running:
                    print(f"[CLIENT] Reconnect failed. Retrying in {self.reconnect_delay}s...")
                    time.sleep(self.reconnect_delay)
            else:
                time.sleep(0.5)
    
    def start(self):
        """
        Start the client's main loop.
        
        Creates a separate thread for receiving messages, then handles
        user input in the main thread.
        """
        self.running = True

        # Keep trying to connect while this client process is running.
        self.connection_thread = threading.Thread(
            target=self._connection_manager,
            daemon=True
        )
        self.connection_thread.start()
        
        # Display usage instructions
        print("=" * 60)
        print("Connected to Pulse-Chat!")
        print("Type your messages and press Enter to send.")
        print("Commands: /join <room>, /leave, /dm <user> <message>, /sendfile <user> <path>")
        print("Type '/quit' to exit.")
        print("=" * 60)
        print()
        
        # Main loop - handle user input
        try:
            while self.running:
                try:
                    # Read user input
                    user_input = input()
                    
                    if not self.running:
                        break
                    
                    # Handle special commands
                    if user_input.lower() == '/quit':
                        break

                    if user_input.lower().startswith('/join '):
                        room = user_input[6:].strip()
                        if not room:
                            print("[CLIENT] Usage: /join <room>")
                            continue
                        self.current_room = room
                        join_message = MessageProtocol.create_message(
                            MessageProtocol.TYPE_ROOM_JOIN,
                            self.username,
                            room,
                            room_name=room
                        )
                        self._send_message(join_message)
                        continue

                    if user_input.lower() == '/leave':
                        leave_room_message = MessageProtocol.create_message(
                            MessageProtocol.TYPE_ROOM_LEAVE,
                            self.username,
                            self.current_room,
                            room_name=self.current_room
                        )
                        self._send_message(leave_room_message)
                        self.current_room = 'lobby'
                        continue

                    if user_input.lower().startswith('/dm '):
                        parts = user_input.split(' ', 2)
                        if len(parts) < 3 or not parts[1].strip() or not parts[2].strip():
                            print("[CLIENT] Usage: /dm <user> <message>")
                            continue
                        target = parts[1].strip()
                        content = parts[2].strip()
                        self.last_dm_target = target
                        dm_message = MessageProtocol.create_message(
                            MessageProtocol.TYPE_PRIVATE,
                            self.username,
                            content,
                            to_username=target,
                            message_id=str(uuid.uuid4())
                        )
                        self._send_message(dm_message)
                        continue

                    if user_input.lower().startswith('/sendfile '):
                        parts = user_input.split(' ', 2)
                        if len(parts) < 3 or not parts[1].strip() or not parts[2].strip():
                            print("[CLIENT] Usage: /sendfile <user> <file_path>")
                            continue
                        target = parts[1].strip()
                        file_path = parts[2].strip().strip('"')
                        self._send_file(target, file_path)
                        self.last_file_send_command = f"/sendfile {target} {file_path}"
                        continue
                    
                    if user_input.strip():
                        if not self.connected:
                            print("[CLIENT] Not connected. Message was not sent.")
                            continue

                        # Create and send chat message
                        chat_message = MessageProtocol.create_message(
                            MessageProtocol.TYPE_CHAT,
                            self.username,
                            user_input,
                            room_name=self.current_room
                        )
                        self._send_message(chat_message)
                
                except EOFError:
                    # Handle Ctrl+D
                    break
                except KeyboardInterrupt:
                    # Handle Ctrl+C
                    break
        
        finally:
            self.disconnect()
    
    def disconnect(self):
        """
        Disconnect from the server and clean up resources.
        """
        if self.running and self.connected:
            # Send leave message
            leave_message = MessageProtocol.create_message(
                MessageProtocol.TYPE_LEAVE,
                self.username,
                f"{self.username} left"
            )
            self._send_message(leave_message)
        
        self.running = False

        self._mark_disconnected()

        # Purge partial downloads on disconnect to avoid hanging transfer state.
        for transfer in list(self.incoming_file_transfers.values()):
            temp_path = transfer.get('temp_path')
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
        self.incoming_file_transfers.clear()

        if self.connection_thread and self.connection_thread.is_alive():
            self.connection_thread.join(timeout=1)
        
        print("\n[CLIENT] Disconnected")

    def _handle_file_offer(self, message: dict):
        transfer_id = (message.get('transfer_id') or '').strip()
        if not transfer_id:
            return

        raw_filename = message.get('filename', 'download.bin')
        filename = self._sanitize_filename(raw_filename)
        if not filename:
            self._send_message(
                MessageProtocol.create_message(
                    MessageProtocol.TYPE_FILE_ERROR,
                    self.username,
                    'Rejected unsafe filename.',
                    transfer_id=transfer_id,
                    to_username=message.get('username', '')
                )
            )
            return

        if not self._is_allowed_file_type(filename):
            self._send_message(
                MessageProtocol.create_message(
                    MessageProtocol.TYPE_FILE_ERROR,
                    self.username,
                    f"Rejected disallowed file type: {os.path.splitext(filename)[1] or 'unknown'}",
                    transfer_id=transfer_id,
                    to_username=message.get('username', '')
                )
            )
            return

        offered_size = int(message.get('size', 0) or 0)
        if offered_size <= 0 or offered_size > self.MAX_FILE_SIZE_BYTES:
            self._send_message(
                MessageProtocol.create_message(
                    MessageProtocol.TYPE_FILE_ERROR,
                    self.username,
                    f"Rejected file size {offered_size} bytes (max {self.MAX_FILE_SIZE_BYTES}).",
                    transfer_id=transfer_id,
                    to_username=message.get('username', '')
                )
            )
            return

        os.makedirs(self.download_dir, exist_ok=True)
        final_path = os.path.join(self.download_dir, filename)
        temp_path = final_path + '.part'

        self.incoming_file_transfers[transfer_id] = {
            'final_path': final_path,
            'temp_path': temp_path,
            'checksum': message.get('checksum', ''),
            'size': offered_size,
            'from': message.get('username', '')
        }

        ack = MessageProtocol.create_message(
            MessageProtocol.TYPE_FILE_ACK,
            self.username,
            f"accepted:{filename}",
            transfer_id=transfer_id,
            to_username=message.get('username', '')
        )
        self._send_message(ack)
        print(f"[CLIENT] Receiving file '{filename}' from {message.get('username', '')}")

    def _handle_file_chunk(self, message: dict):
        transfer_id = (message.get('transfer_id') or '').strip()
        state = self.incoming_file_transfers.get(transfer_id)
        if not state:
            return

        chunk_data = message.get('chunk_data', '')
        try:
            raw = base64.b64decode(chunk_data.encode('ascii'))
            if len(raw) > self.MAX_CHUNK_SIZE_BYTES:
                raise ValueError('chunk too large')

            existing_size = 0
            if os.path.exists(state['temp_path']):
                existing_size = os.path.getsize(state['temp_path'])
            if existing_size + len(raw) > self.MAX_FILE_SIZE_BYTES:
                raise ValueError('transfer exceeds max file size')

            with open(state['temp_path'], 'ab') as f:
                f.write(raw)
        except Exception as e:
            self._send_message(
                MessageProtocol.create_message(
                    MessageProtocol.TYPE_FILE_ERROR,
                    self.username,
                    f'Failed to decode/write file chunk: {e}',
                    transfer_id=transfer_id,
                    to_username=state.get('from')
                )
            )

    def _handle_file_end(self, message: dict):
        transfer_id = (message.get('transfer_id') or '').strip()
        state = self.incoming_file_transfers.pop(transfer_id, None)
        if not state:
            return

        temp_path = state['temp_path']
        final_path = state['final_path']
        expected_size = state['size']
        expected_checksum = state['checksum']

        if not os.path.exists(temp_path):
            return

        actual_size = os.path.getsize(temp_path)
        if expected_size and actual_size != expected_size:
            os.remove(temp_path)
            self._send_message(
                MessageProtocol.create_message(
                    MessageProtocol.TYPE_FILE_ERROR,
                    self.username,
                    f"Size mismatch ({actual_size} != {expected_size}).",
                    transfer_id=transfer_id,
                    to_username=state.get('from')
                )
            )
            return

        hasher = hashlib.sha256()
        with open(temp_path, 'rb') as f:
            while True:
                block = f.read(1024 * 1024)
                if not block:
                    break
                hasher.update(block)
        actual_checksum = hasher.hexdigest()
        if expected_checksum and actual_checksum != expected_checksum:
            os.remove(temp_path)
            self._send_message(
                MessageProtocol.create_message(
                    MessageProtocol.TYPE_FILE_ERROR,
                    self.username,
                    'Checksum mismatch; file corrupted.',
                    transfer_id=transfer_id,
                    to_username=state.get('from')
                )
            )
            return

        os.replace(temp_path, final_path)
        self._send_message(
            MessageProtocol.create_message(
                MessageProtocol.TYPE_FILE_ACK,
                self.username,
                f"saved:{os.path.basename(final_path)}",
                transfer_id=transfer_id,
                to_username=state.get('from')
            )
        )
        print(f"[CLIENT] File saved: {final_path}")

    def _send_file(self, target_user: str, file_path: str):
        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            print(f"[CLIENT] File not found: {file_path}")
            return

        filename = self._sanitize_filename(os.path.basename(file_path))
        if not filename:
            print("[CLIENT] Rejected unsafe filename.")
            return

        if not self._is_allowed_file_type(filename):
            print(f"[CLIENT] Disallowed file type: {os.path.splitext(filename)[1] or 'unknown'}")
            return

        transfer_id = str(uuid.uuid4())
        file_size = os.path.getsize(file_path)
        if file_size <= 0:
            print("[CLIENT] Empty files are not allowed.")
            return
        if file_size > self.MAX_FILE_SIZE_BYTES:
            print(f"[CLIENT] File too large ({file_size} bytes). Max allowed: {self.MAX_FILE_SIZE_BYTES} bytes")
            return

        total_chunks = (file_size + self.file_chunk_size - 1) // self.file_chunk_size

        hasher = hashlib.sha256()
        with open(file_path, 'rb') as f:
            while True:
                block = f.read(1024 * 1024)
                if not block:
                    break
                hasher.update(block)
        checksum = hasher.hexdigest()

        offer = MessageProtocol.create_message(
            MessageProtocol.TYPE_FILE_OFFER,
            self.username,
            f"sending:{filename}",
            transfer_id=transfer_id,
            filename=filename,
            size=file_size,
            checksum=checksum,
            total_chunks=total_chunks,
            to_username=target_user
        )
        self._send_message(offer)

        with open(file_path, 'rb') as f:
            chunk_index = 0
            while True:
                block = f.read(self.file_chunk_size)
                if not block:
                    break
                encoded = base64.b64encode(block).decode('ascii')
                chunk_msg = MessageProtocol.create_message(
                    MessageProtocol.TYPE_FILE_CHUNK,
                    self.username,
                    f"chunk:{chunk_index}",
                    transfer_id=transfer_id,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    chunk_data=encoded,
                    to_username=target_user
                )
                self._send_message(chunk_msg)
                chunk_index += 1

        end_msg = MessageProtocol.create_message(
            MessageProtocol.TYPE_FILE_END,
            self.username,
            'complete',
            transfer_id=transfer_id,
            to_username=target_user
        )
        self._send_message(end_msg)
        print(f"[CLIENT] File sent with transfer id: {transfer_id}")


if __name__ == "__main__":
    # Allow running this module directly
    import sys
    
    host = sys.argv[1] if len(sys.argv) > 1 else 'localhost'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5555
    username = sys.argv[3] if len(sys.argv) > 3 else None
    
    client = ChatClient(host, port, username)
    client.start()
