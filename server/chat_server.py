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
import uuid
from typing import Dict, List, Optional
from server.client_handler import ClientHandler
from server.user_store import UserStore
from utils.message_protocol import MessageProtocol


class ChatServer:
    """
    Secure multi-client chat server using SSL/TLS over TCP.
    """
    
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
        
        # List of connected clients - needs thread-safe access
        self.clients: List[ClientHandler] = []
        # Lock for thread-safe access to the clients list
        self.clients_lock = threading.Lock()

        # Active user routing map for direct messages
        self.user_lock = threading.Lock()
        self.user_to_client: Dict[str, ClientHandler] = {}
        
        # Server state
        self.running = False
        self.server_socket: Optional[socket.socket] = None
    
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
                        remove_callback=self.remove_client,
                        login_callback=self.handle_user_login,
                        disconnect_callback=self.handle_user_disconnect,
                        persist_callback=self.persist_message_for_users,
                        register_user_callback=self.register_active_user,
                        unregister_user_callback=self.unregister_active_user,
                        private_message_callback=self.route_private_message
                    )
                    
                    # Add to clients list (thread-safe)
                    with self.clients_lock:
                        self.clients.append(client_handler)
                    
                    # Start the handler thread
                    client_handler.start()
                
                except ssl.SSLError as e:
                    print(f"[SERVER] SSL Error: {e}")
                except OSError:
                    # Socket closed, server stopping
                    if self.running:
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
        # Use lock to safely iterate over clients list
        # Multiple threads might be adding/removing clients simultaneously
        with self.clients_lock:
            for client in self.clients:
                # Skip the excluded client if specified
                if client != exclude:
                    client.send_message(message)
    
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

    def register_active_user(self, username: str, handler: ClientHandler):
        """Register or refresh active user route for private messaging."""
        if not username:
            return
        with self.user_lock:
            self.user_to_client[username] = handler

    def unregister_active_user(self, username: str, handler: Optional[ClientHandler] = None):
        """Remove active user route when user disconnects."""
        if not username:
            return
        with self.user_lock:
            existing = self.user_to_client.get(username)
            if existing is None:
                return
            if handler is None or existing == handler:
                self.user_to_client.pop(username, None)

    def route_private_message(
        self,
        from_username: str,
        to_username: str,
        content: str,
        message_id: Optional[str] = None
    ) -> dict:
        """Route a private message only to the addressed online user."""
        with self.user_lock:
            recipient = self.user_to_client.get(to_username)

        if not recipient:
            # Offline policy: reject private message delivery when recipient is offline.
            return {
                'ok': False,
                'error': f"User '{to_username}' is offline. DM not delivered.",
                'offline': True
            }

        msg_id = message_id or str(uuid.uuid4())
        dm_message = MessageProtocol.create_message(
            MessageProtocol.TYPE_PRIVATE,
            from_username,
            content,
            to_username=to_username,
            message_id=msg_id
        )
        recipient.send_message(dm_message)

        # Persist delivered DMs for both sender and receiver history replay.
        self.user_store.append_message_for_users([from_username, to_username], dm_message)

        return {
            'ok': True,
            'message_id': msg_id
        }

    def handle_user_login(self, username: str) -> dict:
        """Register user login and return profile/history payload."""
        login_info = self.user_store.register_login(username)
        history = self.user_store.get_user_history(username, limit=100)
        payload = dict(login_info)
        payload['history'] = history
        return payload

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

        with self.user_lock:
            self.user_to_client.clear()
        
        # Close all client connections
        with self.clients_lock:
            for client in self.clients:
                try:
                    client.socket.close()
                except:
                    pass
            self.clients.clear()
        
        # Close the server socket
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass
        
        print("[SERVER] Server stopped")


if __name__ == "__main__":
    # Allow running this module directly for testing
    server = ChatServer()
    server.start()
