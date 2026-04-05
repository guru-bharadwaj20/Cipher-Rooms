"""
Client Handler Module

This module handles individual client connections in separate threads.
Each connected client gets its own ClientHandler instance running in its own thread,
allowing the server to handle multiple clients concurrently.

Key Networking Concepts:
- Threading: Each client runs in a separate thread for concurrent handling
- Buffered Reading: Messages are read line-by-line using makefile()
- Exception Handling: Graceful handling of disconnections and errors
"""

import threading
import ssl
from typing import Callable, Optional
from utils.message_protocol import MessageProtocol


class ClientHandler:
    """
    Handles communication with a single connected client.
    Runs in a separate thread to allow concurrent client handling.
    """
    
    def __init__(
        self,
        client_socket: ssl.SSLSocket,
        client_address: tuple,
        broadcast_callback: Callable,
        room_broadcast_callback: Optional[Callable],
        remove_callback: Callable,
        login_callback: Optional[Callable] = None,
        disconnect_callback: Optional[Callable] = None,
        persist_callback: Optional[Callable] = None,
        join_room_callback: Optional[Callable] = None,
        leave_room_callback: Optional[Callable] = None,
        list_rooms_callback: Optional[Callable] = None,
        get_room_callback: Optional[Callable] = None,
        default_room: str = 'lobby'
    ):
        """
        Initialize the client handler.
        
        Args:
            client_socket: The secure SSL-wrapped socket for this client
            client_address: Tuple of (ip_address, port) for the client
            broadcast_callback: Function to call to broadcast messages to all clients
            remove_callback: Function to call when this client disconnects
        """
        self.socket = client_socket
        self.address = client_address
        self.broadcast = broadcast_callback
        self.broadcast_room = room_broadcast_callback
        self.remove_client = remove_callback
        self.on_login = login_callback
        self.on_disconnect = disconnect_callback
        self.persist_message = persist_callback
        self.join_room = join_room_callback
        self.leave_room = leave_room_callback
        self.list_rooms = list_rooms_callback
        self.get_room = get_room_callback
        self.default_room = default_room
        self.username: Optional[str] = None
        self.running = True
        
        # Create a thread for this client
        self.thread = threading.Thread(target=self.handle_client, daemon=True)
    
    def start(self):
        """Start the client handler thread."""
        self.thread.start()
    
    def handle_client(self):
        """
        Main loop for handling client communication.
        This runs in a separate thread for each client.
        
        Process:
        1. Wait for username from client
        2. Announce client join to all users
        3. Loop: receive messages and broadcast them
        4. Handle disconnection gracefully
        """
        try:
            # Use makefile() to create a file-like object for line-based reading
            # This simplifies receiving messages delimited by newlines
            client_file = self.socket.makefile('rb')
            
            # First message should contain the username
            data = client_file.readline()
            if not data:
                return
            
            message = MessageProtocol.decode_message(data)
            if message and message.get("type") == MessageProtocol.TYPE_JOIN:
                self.username = message.get("username", "Unknown")
                print(f"[SERVER] {self.username} connected from {self.address}")

                login_info = {
                    'is_returning': False,
                    'profile': {},
                    'history': []
                }
                if self.on_login:
                    info = self.on_login(self.username)
                    if isinstance(info, dict):
                        login_info.update(info)

                # Default room policy: all users start in lobby on login.
                room_result = None
                if self.join_room:
                    room_result = self.join_room(self, self.default_room)

                self._send_login_context(login_info)

                if room_result and room_result.get('ok'):
                    self.send_message(
                        MessageProtocol.create_message(
                            MessageProtocol.TYPE_SYSTEM,
                            'Server',
                            f"You are now in room '{room_result.get('room')}'."
                        )
                    )
                
                # Broadcast join message to all clients
                join_msg = MessageProtocol.create_message(
                    MessageProtocol.TYPE_JOIN,
                    self.username,
                    f"{self.username} joined the chat"
                )
                current_room = self.default_room
                if self.get_room:
                    current_room = self.get_room(self)
                join_msg['room'] = current_room
                if self.broadcast_room:
                    self.broadcast_room(current_room, join_msg, exclude=self)
                else:
                    self.broadcast(join_msg, exclude=self)
                if self.persist_message:
                    self.persist_message(join_msg)
            
            # Main message receiving loop
            while self.running:
                # Read one line (one message) from the client
                data = client_file.readline()
                
                if not data:
                    # Client disconnected
                    break
                
                # Decode and process the message
                message = MessageProtocol.decode_message(data)
                if message:
                    msg_type = message.get("type")
                    
                    if msg_type == MessageProtocol.TYPE_CHAT:
                        current_room = self.default_room
                        if self.get_room:
                            current_room = self.get_room(self)

                        # Regular chat message - broadcast only to current room
                        message['room'] = current_room
                        print(f"[{current_room}] [{self.username}]: {message.get('content', '')}")
                        if self.broadcast_room:
                            self.broadcast_room(current_room, message, exclude=None)
                        else:
                            self.broadcast(message, exclude=None)
                        if self.persist_message:
                            self.persist_message(message)

                    elif msg_type == MessageProtocol.TYPE_ROOM_JOIN:
                        requested_room = message.get('content', '').strip()
                        if not requested_room:
                            self.send_message(
                                MessageProtocol.create_message(
                                    MessageProtocol.TYPE_ERROR,
                                    'Server',
                                    "Usage: /join <room_name>"
                                )
                            )
                            continue

                        result = {'ok': False, 'error': 'Room service unavailable.'}
                        if self.join_room:
                            result = self.join_room(self, requested_room)

                        if result.get('ok'):
                            room_name = result.get('room', requested_room)
                            self.send_message(
                                MessageProtocol.create_message(
                                    MessageProtocol.TYPE_SYSTEM,
                                    'Server',
                                    f"Joined room '{room_name}'."
                                )
                            )
                        else:
                            self.send_message(
                                MessageProtocol.create_message(
                                    MessageProtocol.TYPE_ERROR,
                                    'Server',
                                    result.get('error', 'Could not join room.')
                                )
                            )

                    elif msg_type == MessageProtocol.TYPE_ROOM_LEAVE:
                        result = {'ok': False, 'error': 'Room service unavailable.'}
                        if self.leave_room:
                            result = self.leave_room(self)

                        if result.get('ok'):
                            room_name = result.get('room', self.default_room)
                            left_room = result.get('left_room')
                            if left_room:
                                content = f"Left room '{left_room}'. Moved to '{room_name}'."
                            else:
                                content = f"You are in room '{room_name}'."

                            self.send_message(
                                MessageProtocol.create_message(
                                    MessageProtocol.TYPE_SYSTEM,
                                    'Server',
                                    content
                                )
                            )
                        else:
                            self.send_message(
                                MessageProtocol.create_message(
                                    MessageProtocol.TYPE_ERROR,
                                    'Server',
                                    result.get('error', 'Could not leave room.')
                                )
                            )

                    elif msg_type == MessageProtocol.TYPE_ROOM_LIST:
                        rooms = []
                        if self.list_rooms:
                            rooms = self.list_rooms()
                        if rooms:
                            room_content = "Available rooms: " + ", ".join(rooms)
                        else:
                            room_content = "No rooms available."

                        self.send_message(
                            MessageProtocol.create_message(
                                MessageProtocol.TYPE_SYSTEM,
                                'Server',
                                room_content
                            )
                        )
                    
                    elif msg_type == MessageProtocol.TYPE_LEAVE:
                        # Client wants to leave
                        break
        
        except ConnectionResetError:
            print(f"[SERVER] Connection reset by {self.username or self.address}")
        except Exception as e:
            print(f"[SERVER] Error handling client {self.username or self.address}: {e}")
        finally:
            self.cleanup()
    
    def send_message(self, message: dict):
        """
        Send a message to this client.
        
        Args:
            message: Message dictionary to send
        """
        try:
            encoded = MessageProtocol.encode_message(message)
            self.socket.sendall(encoded)
        except Exception as e:
            print(f"[SERVER] Error sending to {self.username}: {e}")
            self.running = False

    def _send_login_context(self, login_info: dict):
        """Send profile and history context to the logging-in client."""
        is_returning = bool(login_info.get('is_returning'))
        profile = login_info.get('profile', {}) or {}
        history = login_info.get('history', []) or []

        if is_returning:
            last_seen = profile.get('last_seen')
            if last_seen:
                welcome_text = f"Welcome back {self.username}! Last seen: {last_seen}"
            else:
                welcome_text = f"Welcome back {self.username}!"
        else:
            welcome_text = f"Welcome {self.username}! Your profile has been created."

        self.send_message(
            MessageProtocol.create_message(
                MessageProtocol.TYPE_SYSTEM,
                'Server',
                welcome_text
            )
        )

        if history:
            self.send_message(
                MessageProtocol.create_message(
                    MessageProtocol.TYPE_SYSTEM,
                    'Server',
                    f"Loading {len(history)} previous messages..."
                )
            )
            for old_message in history:
                self.send_message(old_message)
        else:
            self.send_message(
                MessageProtocol.create_message(
                    MessageProtocol.TYPE_SYSTEM,
                    'Server',
                    "No previous chat history found for this user."
                )
            )
    
    def cleanup(self):
        """
        Clean up resources when client disconnects.
        This ensures proper resource management and notifies other clients.
        """
        self.running = False
        
        # Announce departure to other clients
        if self.username:
            current_room = self.default_room
            if self.get_room:
                current_room = self.get_room(self)

            leave_msg = MessageProtocol.create_message(
                MessageProtocol.TYPE_LEAVE,
                self.username,
                f"{self.username} left the chat",
                room=current_room
            )
            if self.broadcast_room:
                self.broadcast_room(current_room, leave_msg, exclude=self)
            else:
                self.broadcast(leave_msg, exclude=self)
            if self.persist_message:
                self.persist_message(leave_msg)
            if self.on_disconnect:
                self.on_disconnect(self.username)
            print(f"[SERVER] {self.username} disconnected")
        
        # Close the socket
        try:
            self.socket.close()
        except:
            pass
        
        # Remove this client from the server's client list
        self.remove_client(self)
