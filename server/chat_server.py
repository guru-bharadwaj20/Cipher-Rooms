"""
Chat Server Module

This is the main server implementation that:
1. Creates a TCP socket and binds to a port
2. Wraps the socket with SSL/TLS for secure communication
3. Accepts incoming client connections
4. Spawns a ClientHandler thread for each client
5. Manages broadcasting messages to all connected clients

Key Networking Concepts:
- TCP Socket: Reliable, connection-oriented communication
- SSL/TLS: Encrypts data in transit, authenticates server identity
- Server Socket: Listens for incoming connections (accept)
- Thread Safety: Uses locks to protect shared data structures
"""

import socket
import ssl
import threading
import os
from typing import Dict, List, Optional
from server.client_handler import ClientHandler
from server.user_store import UserStore
from utils.message_protocol import MessageProtocol


class ChatServer:
    """
    Secure multi-client chat server using SSL/TLS over TCP.
    """

    MAX_ACTIVE_FILE_TRANSFERS = 1024
    
    def __init__(
        self,
        host: str = '0.0.0.0',
        port: int = 5555,
        cert_file: str = 'certs/server.crt',
        key_file: str = 'certs/server.key'
    ):
        """
        Initialize the chat server.
        
        Args:
            host: IP address to bind to (0.0.0.0 = all interfaces)
            port: Port number to listen on
            cert_file: Path to the SSL certificate file
            key_file: Path to the SSL private key file
        """
        self.host = host
        self.port = port
        self.cert_file = cert_file
        self.key_file = key_file
        self.user_store = UserStore(base_dir='data')
        self.default_room = 'lobby'
        
        # List of connected clients - needs thread-safe access
        self.clients: List[ClientHandler] = []
        # Lock for thread-safe access to the clients list
        self.clients_lock = threading.Lock()

        # Active users and room membership maps.
        self.user_lock = threading.Lock()
        self.user_to_client: Dict[str, ClientHandler] = {}
        self.room_to_users: Dict[str, set] = {self.default_room: set()}
        self.user_to_room: Dict[str, str] = {}

        # Active file transfer state for cleanup and failure notifications.
        self.transfer_lock = threading.Lock()
        self.active_file_transfers: Dict[str, Dict[str, str]] = {}
        
        # Server state
        self.running = False
        self.server_socket: Optional[socket.socket] = None
        self.secure_socket: Optional[socket.socket] = None
    
    def start(self):
        """
        Start the chat server.
        
        This method:
        1. Creates a TCP socket
        2. Wraps it with SSL/TLS
        3. Binds to the specified address and port
        4. Listens for incoming connections
        5. Accepts and handles clients in a loop
        """
        try:
            # Step 1: Create a TCP socket
            # AF_INET = IPv4, SOCK_STREAM = TCP
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            
            # Allow reusing the address immediately after server shutdown
            # Prevents "Address already in use" errors
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            # Step 2: Bind the socket to an address and port
            # This associates the socket with a specific network interface and port
            self.server_socket.bind((self.host, self.port))
            
            # Step 3: Create an SSL context for secure communication
            # This configures the TLS settings
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            
            # Load the server's certificate and private key
            # Certificate proves server's identity to clients
            ssl_context.load_cert_chain(
                certfile=self.cert_file,
                keyfile=self.key_file
            )
            
            # Step 4: Wrap the server socket with SSL
            # All accepted connections will automatically use TLS
            secure_socket = ssl_context.wrap_socket(
                self.server_socket,
                server_side=True
            )
            self.secure_socket = secure_socket
            
            # Step 5: Listen for incoming connections
            # Backlog of 5 means up to 5 pending connections can wait
            secure_socket.listen(5)
            
            self.running = True
            print(f"[SERVER] Secure chat server started on {self.host}:{self.port}")
            print(f"[SERVER] Using certificate: {self.cert_file}")
            print(f"[SERVER] Waiting for connections...\n")
            
            # Main server loop - accept clients
            while self.running:
                try:
                    # Accept a new client connection
                    # This blocks until a client connects
                    # The TLS handshake happens automatically here
                    client_socket, client_address = secure_socket.accept()
                    
                    print(f"[SERVER] New connection from {client_address}")
                    
                    # Create a handler for this client
                    client_handler = ClientHandler(
                        client_socket=client_socket,
                        client_address=client_address,
                        broadcast_callback=self.broadcast_message,
                        room_broadcast_callback=self.broadcast_to_room,
                        remove_callback=self.remove_client,
                        login_callback=self.handle_user_login,
                        register_user_callback=self.register_active_user,
                        unregister_user_callback=self.unregister_active_user,
                        join_room_callback=self.join_user_room,
                        leave_room_callback=self.leave_user_room,
                        room_lookup_callback=self.get_user_room,
                        private_message_callback=self.route_private_message,
                        file_frame_callback=self.route_file_frame,
                        fail_transfers_callback=self.fail_transfers_for_user,
                        disconnect_callback=self.handle_user_disconnect,
                        persist_callback=self.persist_message_for_users,
                        default_room=self.default_room
                    )
                    
                    # Add to clients list (thread-safe)
                    with self.clients_lock:
                        self.clients.append(client_handler)
                    
                    # Start the handler thread
                    client_handler.start()
                
                except ssl.SSLError as e:
                    # Common during abrupt client disconnects under load tests.
                    text = str(e).lower()
                    if "unexpected eof" in text or "eof occurred in violation of protocol" in text:
                        continue
                    print(f"[SERVER] SSL Error: {e}")
                except OSError as e:
                    # Socket closed, server stopping
                    if not self.running:
                        break

                    # Windows can emit recoverable connection abort/reset during accept.
                    # Keep server alive for these transient client-side failures.
                    winerror = getattr(e, 'winerror', None)
                    if winerror in {10053, 10054}:
                        print(f"[SERVER] Connection aborted/reset during accept: {e}")
                        continue

                    raise
                    break
        
        except KeyboardInterrupt:
            print("\n[SERVER] Shutting down...")
        except FileNotFoundError as e:
            print(f"[SERVER] Certificate files not found: {e}")
            print("[SERVER] Please generate certificates first (see README.md)")
        except Exception as e:
            print(f"[SERVER] Error: {e}")
        finally:
            self.stop()
    
    def broadcast_message(self, message: dict, exclude: Optional[ClientHandler] = None):
        """
        Broadcast a message to all connected clients.
        
        This is thread-safe and can be called from any client handler thread.
        
        Args:
            message: Message dictionary to broadcast
            exclude: Optional client to exclude from broadcast (e.g., the sender)
        """
        failed_clients = []

        # Use lock to safely iterate over clients list
        # Multiple threads might be adding/removing clients simultaneously
        with self.clients_lock:
            clients_snapshot = list(self.clients)

        for client in clients_snapshot:
                # Skip the excluded client if specified
            if client != exclude:
                delivered = client.send_message(message)
                if delivered is False:
                    failed_clients.append(client)

        for failed in failed_clients:
            self.remove_client(failed)

    def broadcast_to_room(self, room_name: str, message: dict, exclude: Optional[ClientHandler] = None):
        """Broadcast to users in a room only."""
        room = (room_name or self.default_room).strip() or self.default_room
        with self.user_lock:
            usernames = list(self.room_to_users.get(room, set()))
            recipients = []
            for username in usernames:
                handler = self.user_to_client.get(username)
                if handler and handler != exclude:
                    recipients.append(handler)

        failed_clients = []
        for recipient in recipients:
            delivered = recipient.send_message(message)
            if delivered is False:
                failed_clients.append(recipient)

        for failed in failed_clients:
            self.remove_client(failed)
    
    def remove_client(self, client: ClientHandler):
        """
        Remove a client from the active clients list.
        Called when a client disconnects.
        
        Args:
            client: ClientHandler instance to remove
        """
        with self.clients_lock:
            if client in self.clients:
                self.clients.remove(client)
                print(f"[SERVER] Removed client. Active clients: {len(self.clients)}")

        if getattr(client, 'username', None):
            self.unregister_active_user(client.username, client)
            self.remove_user_from_room_membership(client.username)

    def handle_user_login(self, username: str) -> dict:
        """Register user login and return profile/history payload."""
        login_info = self.user_store.register_login(username)
        history = self.user_store.get_user_history(username, limit=100)
        payload = dict(login_info)
        payload['history'] = history
        return payload

    def register_active_user(self, username: str, client: ClientHandler):
        if not username:
            return
        with self.user_lock:
            self.user_to_client[username] = client
            self.room_to_users.setdefault(self.default_room, set()).add(username)
            self.user_to_room[username] = self.default_room

    def unregister_active_user(self, username: str, client: Optional[ClientHandler] = None):
        if not username:
            return
        with self.user_lock:
            existing = self.user_to_client.get(username)
            if existing is None:
                return
            if client is None or existing == client:
                self.user_to_client.pop(username, None)

    def remove_user_from_room_membership(self, username: str):
        if not username:
            return
        with self.user_lock:
            room = self.user_to_room.pop(username, None)
            if not room:
                return
            users = self.room_to_users.get(room)
            if users:
                users.discard(username)
                if room != self.default_room and not users:
                    self.room_to_users.pop(room, None)

    def join_user_room(self, username: str, room_name: str) -> dict:
        """Join room with idempotent semantics."""
        if not username:
            return {'ok': False, 'error': 'Missing username.'}
        room = (room_name or '').strip()
        if not room:
            return {'ok': False, 'error': 'Missing room name.'}

        with self.user_lock:
            previous_room = self.user_to_room.get(username, self.default_room)
            if previous_room == room:
                return {'ok': True, 'changed': False, 'room': room, 'previous_room': previous_room}

            prev_members = self.room_to_users.setdefault(previous_room, set())
            prev_members.discard(username)
            if previous_room != self.default_room and not prev_members:
                self.room_to_users.pop(previous_room, None)

            self.room_to_users.setdefault(room, set()).add(username)
            self.user_to_room[username] = room

        return {'ok': True, 'changed': True, 'room': room, 'previous_room': previous_room}

    def leave_user_room(self, username: str) -> dict:
        """Leave current room and move to default room; idempotent for default room."""
        if not username:
            return {'ok': False, 'error': 'Missing username.'}

        with self.user_lock:
            current_room = self.user_to_room.get(username, self.default_room)
            if current_room == self.default_room:
                return {
                    'ok': True,
                    'changed': False,
                    'room': self.default_room,
                    'previous_room': self.default_room
                }

            current_members = self.room_to_users.setdefault(current_room, set())
            current_members.discard(username)
            if current_room != self.default_room and not current_members:
                self.room_to_users.pop(current_room, None)

            self.room_to_users.setdefault(self.default_room, set()).add(username)
            self.user_to_room[username] = self.default_room

        return {'ok': True, 'changed': True, 'room': self.default_room, 'previous_room': current_room}

    def get_user_room(self, username: str) -> str:
        with self.user_lock:
            return self.user_to_room.get(username, self.default_room)

    def route_private_message(self, from_username: str, to_username: str, content: str, message_id: str = '') -> dict:
        """Route direct message to target or return delivery error."""
        target = (to_username or '').strip()
        if not target:
            return {'ok': False, 'error': 'Missing recipient username.'}

        with self.user_lock:
            recipient = self.user_to_client.get(target)

        if not recipient:
            return {'ok': False, 'error': f"User '{target}' is offline. DM not delivered."}

        dm_message = MessageProtocol.create_message(
            MessageProtocol.TYPE_PRIVATE,
            from_username,
            content,
            to_username=target,
            message_id=message_id
        )
        recipient.send_message(dm_message)
        return {'ok': True}

    def route_file_frame(self, from_username: str, message: dict) -> dict:
        """Route file frames and maintain transfer state for recovery handling."""
        msg_type = message.get('type')
        transfer_id = (message.get('transfer_id') or '').strip()
        if not transfer_id:
            return {'ok': False, 'error': 'Missing transfer_id.'}

        if msg_type == MessageProtocol.TYPE_FILE_OFFER:
            target = (message.get('to_username') or '').strip()
            if not target:
                return {'ok': False, 'error': 'Missing file recipient username.'}
            with self.user_lock:
                recipient = self.user_to_client.get(target)
            if not recipient:
                return {'ok': False, 'error': f"User '{target}' is offline. File not delivered."}

            with self.transfer_lock:
                if len(self.active_file_transfers) >= self.MAX_ACTIVE_FILE_TRANSFERS:
                    return {
                        'ok': False,
                        'error': 'Server is busy with active file transfers. Try again shortly.'
                    }
                self.active_file_transfers[transfer_id] = {'from': from_username, 'to': target}

            recipient.send_message(message)
            return {'ok': True}

        with self.transfer_lock:
            transfer_state = self.active_file_transfers.get(transfer_id)

        if not transfer_state:
            return {'ok': False, 'error': f"Transfer '{transfer_id}' not found or already finished."}

        if from_username == transfer_state['from']:
            target = transfer_state['to']
        else:
            target = transfer_state['from']

        with self.user_lock:
            recipient = self.user_to_client.get(target)

        if not recipient:
            return {'ok': False, 'error': f"Transfer peer '{target}' is offline."}

        recipient.send_message(message)

        if msg_type in {MessageProtocol.TYPE_FILE_END, MessageProtocol.TYPE_FILE_ERROR}:
            with self.transfer_lock:
                self.active_file_transfers.pop(transfer_id, None)

        return {'ok': True}

    def fail_transfers_for_user(self, username: str):
        """Fail and purge active transfers for a disconnecting user."""
        if not username:
            return

        to_fail = []
        with self.transfer_lock:
            for transfer_id, state in list(self.active_file_transfers.items()):
                if state.get('from') == username or state.get('to') == username:
                    peer = state.get('to') if state.get('from') == username else state.get('from')
                    to_fail.append((transfer_id, peer))
                    self.active_file_transfers.pop(transfer_id, None)

        with self.user_lock:
            for transfer_id, peer in to_fail:
                peer_handler = self.user_to_client.get(peer)
                if peer_handler:
                    peer_handler.send_message(
                        MessageProtocol.create_message(
                            MessageProtocol.TYPE_FILE_ERROR,
                            'Server',
                            f"Transfer aborted because peer '{username}' disconnected.",
                            transfer_id=transfer_id
                        )
                    )

    def handle_user_disconnect(self, username: str):
        """Persist last-seen timestamp when user disconnects."""
        self.user_store.update_last_seen(username)

    def persist_message_for_users(self, message: dict):
        """Persist broadcasted message for every known user."""
        usernames = self.user_store.list_users()
        if usernames:
            self.user_store.append_message_for_users(usernames, message)
    
    def stop(self):
        """
        Stop the server and clean up resources.
        """
        self.running = False

        with self.transfer_lock:
            self.active_file_transfers.clear()

        with self.user_lock:
            self.user_to_client.clear()
            self.room_to_users.clear()
            self.user_to_room.clear()
        
        # Close all client connections
        with self.clients_lock:
            for client in self.clients:
                try:
                    client.socket.close()
                except:
                    pass
            self.clients.clear()
        
        # Close the server socket
        if self.secure_socket:
            try:
                self.secure_socket.close()
            except:
                pass
            self.secure_socket = None

        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass
            self.server_socket = None
        
        print("[SERVER] Server stopped")


if __name__ == "__main__":
    # Allow running this module directly for testing
    server = ChatServer()
    server.start()
