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
import uuid
import os
import math
import base64
import hashlib
from utils.message_protocol import MessageProtocol


class ChatClient:
    """
    Secure chat client that connects to the chat server via TLS.
    """
    
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
        self.transfer_lock = threading.Lock()
        self.incoming_transfers = {}
        self.outgoing_transfers = {}
        self.transfer_timeout_seconds = 120
        self.chunk_size = 16 * 1024
        self.download_dir = os.path.join(os.getcwd(), 'downloads')
        self.transfer_cleanup_thread = None
        self.last_seq_per_room = {}
    
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
                    msg_type = message.get('type')

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
                        self._handle_file_ack(message)
                        continue
                    if msg_type == MessageProtocol.TYPE_FILE_ERROR:
                        self._handle_file_error(message)
                        continue

                    if msg_type == MessageProtocol.TYPE_CHAT:
                        self._check_room_sequence(message)

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

    def _transfer_cleanup_worker(self):
        """Clean up stale partial transfers after timeout."""
        while self.running:
            now = time.time()
            stale_ids = []

            with self.transfer_lock:
                for transfer_id, state in self.incoming_transfers.items():
                    if now - state.get('last_update', now) > self.transfer_timeout_seconds:
                        stale_ids.append(transfer_id)

                for transfer_id in stale_ids:
                    state = self.incoming_transfers.pop(transfer_id, None)
                    if not state:
                        continue
                    temp_path = state.get('temp_path')
                    if temp_path and os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass

            for transfer_id in stale_ids:
                print(f"[CLIENT] Transfer {transfer_id} timed out and was cleaned up.")

            time.sleep(2)

    def _safe_download_path(self, file_name: str) -> str:
        os.makedirs(self.download_dir, exist_ok=True)
        base_name = os.path.basename(file_name) or 'download.bin'
        candidate = os.path.join(self.download_dir, base_name)

        if not os.path.exists(candidate):
            return candidate

        name, ext = os.path.splitext(base_name)
        index = 1
        while True:
            candidate = os.path.join(self.download_dir, f"{name}_{index}{ext}")
            if not os.path.exists(candidate):
                return candidate
            index += 1

    def _handle_file_offer(self, message: dict):
        transfer_id = message.get('transfer_id')
        if not transfer_id:
            return

        filename = message.get('filename', 'download.bin')
        size = int(message.get('size', 0))
        checksum = message.get('checksum', '')
        total_chunks = int(message.get('total_chunks', 0))
        sender = message.get('username', 'Unknown')

        save_path = self._safe_download_path(filename)
        temp_path = save_path + '.part'

        with self.transfer_lock:
            self.incoming_transfers[transfer_id] = {
                'filename': filename,
                'save_path': save_path,
                'temp_path': temp_path,
                'size': size,
                'checksum': checksum,
                'total_chunks': total_chunks,
                'received_chunks': 0,
                'bytes_written': 0,
                'from_username': sender,
                'last_update': time.time()
            }

        ack = MessageProtocol.create_message(
            MessageProtocol.TYPE_FILE_ACK,
            self.username,
            'offer_received',
            transfer_id=transfer_id,
            stage='offer',
            to_username=sender
        )
        self._send_message(ack)
        print(f"[CLIENT] Receiving file '{filename}' ({size} bytes) from {sender}")

    def _handle_file_chunk(self, message: dict):
        transfer_id = message.get('transfer_id')
        if not transfer_id:
            return

        chunk_data = message.get('chunk_data', '')
        chunk_index = int(message.get('chunk_index', -1))

        with self.transfer_lock:
            state = self.incoming_transfers.get(transfer_id)

        if not state:
            return

        try:
            raw_bytes = base64.b64decode(chunk_data.encode('ascii'))
            with open(state['temp_path'], 'ab') as f:
                f.write(raw_bytes)

            with self.transfer_lock:
                state = self.incoming_transfers.get(transfer_id)
                if state:
                    state['received_chunks'] += 1
                    state['bytes_written'] += len(raw_bytes)
                    state['last_update'] = time.time()
        except Exception as e:
            err = MessageProtocol.create_message(
                MessageProtocol.TYPE_FILE_ERROR,
                self.username,
                f"Failed to write chunk {chunk_index}: {e}",
                transfer_id=transfer_id,
                to_username=message.get('username')
            )
            self._send_message(err)
            return

        ack = MessageProtocol.create_message(
            MessageProtocol.TYPE_FILE_ACK,
            self.username,
            f"chunk_{chunk_index}_received",
            transfer_id=transfer_id,
            stage='chunk',
            chunk_index=chunk_index,
            to_username=message.get('username')
        )
        self._send_message(ack)

    def _handle_file_end(self, message: dict):
        transfer_id = message.get('transfer_id')
        if not transfer_id:
            return

        with self.transfer_lock:
            state = self.incoming_transfers.pop(transfer_id, None)

        if not state:
            return

        sender = state.get('from_username')
        temp_path = state['temp_path']
        save_path = state['save_path']

        if not os.path.exists(temp_path):
            err = MessageProtocol.create_message(
                MessageProtocol.TYPE_FILE_ERROR,
                self.username,
                'Transfer ended but no partial file exists.',
                transfer_id=transfer_id,
                to_username=sender
            )
            self._send_message(err)
            return

        calculated_size = os.path.getsize(temp_path)
        if calculated_size != state['size']:
            try:
                os.remove(temp_path)
            except Exception:
                pass
            err = MessageProtocol.create_message(
                MessageProtocol.TYPE_FILE_ERROR,
                self.username,
                f"Size mismatch. expected={state['size']} actual={calculated_size}",
                transfer_id=transfer_id,
                to_username=sender
            )
            self._send_message(err)
            return

        hasher = hashlib.sha256()
        with open(temp_path, 'rb') as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        actual_checksum = hasher.hexdigest()

        if actual_checksum != state['checksum']:
            try:
                os.remove(temp_path)
            except Exception:
                pass
            err = MessageProtocol.create_message(
                MessageProtocol.TYPE_FILE_ERROR,
                self.username,
                'Checksum mismatch. Transfer corrupted.',
                transfer_id=transfer_id,
                to_username=sender
            )
            self._send_message(err)
            return

        os.replace(temp_path, save_path)
        ack = MessageProtocol.create_message(
            MessageProtocol.TYPE_FILE_ACK,
            self.username,
            f"file_saved:{os.path.basename(save_path)}",
            transfer_id=transfer_id,
            stage='end',
            to_username=sender
        )
        self._send_message(ack)
        print(f"[CLIENT] File received and verified: {save_path}")

    def _handle_file_ack(self, message: dict):
        transfer_id = message.get('transfer_id', '')
        stage = message.get('stage', '')
        print(f"[CLIENT] File ACK transfer={transfer_id} stage={stage}")

    def _handle_file_error(self, message: dict):
        transfer_id = message.get('transfer_id', '')
        print(f"[CLIENT] File ERROR transfer={transfer_id}: {message.get('content', '')}")

    def _check_room_sequence(self, message: dict):
        """Warn if room sequence appears to have a gap on this client."""
        room_name = (message.get('room_name') or message.get('room') or 'global').strip() or 'global'
        room_seq = message.get('room_seq')
        if room_seq is None:
            return

        try:
            room_seq = int(room_seq)
        except (TypeError, ValueError):
            return

        last_seq = self.last_seq_per_room.get(room_name)
        if last_seq is not None and room_seq != last_seq + 1:
            print(
                f"[CLIENT][WARN] Sequence gap in room '{room_name}': "
                f"expected {last_seq + 1}, got {room_seq}"
            )

        if last_seq is None or room_seq > last_seq:
            self.last_seq_per_room[room_name] = room_seq

    def _send_file(self, target: str, file_path: str):
        """Send file in base64 chunks with checksum metadata."""
        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            print(f"[CLIENT] File not found: {file_path}")
            return

        with open(file_path, 'rb') as f:
            raw = f.read()

        file_size = len(raw)
        checksum = hashlib.sha256(raw).hexdigest()
        total_chunks = max(1, math.ceil(file_size / self.chunk_size))
        transfer_id = str(uuid.uuid4())

        to_username = None
        room = None
        if target.lower() == 'all':
            room = 'all'
        elif target.startswith('#'):
            room = target[1:]
        else:
            to_username = target

        file_name = os.path.basename(file_path)
        offer = MessageProtocol.create_message(
            MessageProtocol.TYPE_FILE_OFFER,
            self.username,
            f"Offering file {file_name}",
            transfer_id=transfer_id,
            filename=file_name,
            size=file_size,
            checksum=checksum,
            total_chunks=total_chunks,
            to_username=to_username,
            room=room
        )
        self._send_message(offer)

        for chunk_index in range(total_chunks):
            start = chunk_index * self.chunk_size
            end = min(start + self.chunk_size, file_size)
            chunk_bytes = raw[start:end]
            encoded_chunk = base64.b64encode(chunk_bytes).decode('ascii')

            chunk_message = MessageProtocol.create_message(
                MessageProtocol.TYPE_FILE_CHUNK,
                self.username,
                f"chunk {chunk_index}",
                transfer_id=transfer_id,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                chunk_data=encoded_chunk,
                to_username=to_username,
                room=room
            )
            self._send_message(chunk_message)

        end_message = MessageProtocol.create_message(
            MessageProtocol.TYPE_FILE_END,
            self.username,
            f"Completed transfer of {file_name}",
            transfer_id=transfer_id,
            to_username=to_username,
            room=room
        )
        self._send_message(end_message)
        print(f"[CLIENT] File sent: {file_name} ({file_size} bytes, transfer_id={transfer_id})")
    
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

        self.transfer_cleanup_thread = threading.Thread(
            target=self._transfer_cleanup_worker,
            daemon=True
        )
        self.transfer_cleanup_thread.start()
        
        # Display usage instructions
        print("=" * 60)
        print("Connected to Pulse-Chat!")
        print("Type your messages and press Enter to send.")
        print("Type '/dm <username> <message>' to send a direct message.")
        print("Type '/sendfile <username|all> <file_path>' to send a file.")
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

                    if user_input.lower().startswith('/dm '):
                        parts = user_input.split(' ', 2)
                        if len(parts) < 3 or not parts[1].strip() or not parts[2].strip():
                            print("[CLIENT] Usage: /dm <username> <message>")
                            continue

                        target_user = parts[1].strip()
                        dm_text = parts[2].strip()
                        dm_message = MessageProtocol.create_message(
                            MessageProtocol.TYPE_PRIVATE,
                            self.username,
                            dm_text,
                            to_username=target_user,
                            message_id=str(uuid.uuid4())
                        )
                        self._send_message(dm_message)
                        continue

                    if user_input.lower().startswith('/sendfile '):
                        parts = user_input.split(' ', 2)
                        if len(parts) < 3 or not parts[1].strip() or not parts[2].strip():
                            print("[CLIENT] Usage: /sendfile <username|all> <file_path>")
                            continue

                        target = parts[1].strip()
                        file_path = parts[2].strip().strip('"')
                        self._send_file(target, file_path)
                        continue
                    
                    if user_input.strip():
                        if not self.connected:
                            print("[CLIENT] Not connected. Message was not sent.")
                            continue

                        # Create and send chat message
                        chat_message = MessageProtocol.create_message(
                            MessageProtocol.TYPE_CHAT,
                            self.username,
                            user_input
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

        with self.transfer_lock:
            stale = list(self.incoming_transfers.values())
            self.incoming_transfers.clear()

        for state in stale:
            temp_path = state.get('temp_path')
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

        if self.connection_thread and self.connection_thread.is_alive():
            self.connection_thread.join(timeout=1)
        
        print("\n[CLIENT] Disconnected")


if __name__ == "__main__":
    # Allow running this module directly
    import sys
    
    host = sys.argv[1] if len(sys.argv) > 1 else 'localhost'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5555
    username = sys.argv[3] if len(sys.argv) > 3 else None
    
    client = ChatClient(host, port, username)
    client.start()
