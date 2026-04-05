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
        print("Commands: /rooms, /join <room>, /leave, /roommsg <message>")
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

                    if user_input.lower() == '/rooms':
                        rooms_message = MessageProtocol.create_message(
                            MessageProtocol.TYPE_ROOM_LIST,
                            self.username,
                            ""
                        )
                        self._send_message(rooms_message)
                        continue

                    if user_input.lower().startswith('/join '):
                        room_name = user_input[6:].strip()
                        if not room_name:
                            print("[CLIENT] Usage: /join <room_name>")
                            continue

                        join_room_message = MessageProtocol.create_message(
                            MessageProtocol.TYPE_ROOM_JOIN,
                            self.username,
                            room_name
                        )
                        self._send_message(join_room_message)
                        continue

                    if user_input.lower() == '/leave':
                        leave_room_message = MessageProtocol.create_message(
                            MessageProtocol.TYPE_ROOM_LEAVE,
                            self.username,
                            ""
                        )
                        self._send_message(leave_room_message)
                        continue

                    if user_input.lower().startswith('/roommsg '):
                        room_text = user_input[9:].strip()
                        if not room_text:
                            print("[CLIENT] Usage: /roommsg <message>")
                            continue

                        room_chat_message = MessageProtocol.create_message(
                            MessageProtocol.TYPE_CHAT,
                            self.username,
                            room_text
                        )
                        self._send_message(room_chat_message)
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
