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
        direct_message_callback: Callable,
        remove_callback: Callable,
        auth_callback: Callable,
        room_join_callback: Callable,
        room_list_callback: Callable,
        history_callback: Callable,
        user_list_callback: Callable,
        user_list_broadcast_callback: Callable
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
        self.send_direct_message = direct_message_callback
        self.remove_client = remove_callback
        self.authenticate = auth_callback
        self.join_room = room_join_callback
        self.list_rooms = room_list_callback
        self.get_history = history_callback
        self.list_users = user_list_callback
        self.broadcast_user_list = user_list_broadcast_callback
        self.username: Optional[str] = None
        self.current_room = "lobby"
        self.running = True
        self.authenticated = False
        
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
            
            # First message should authenticate the user
            data = client_file.readline()
            if not data:
                return
            
            message = MessageProtocol.decode_message(data)
            if not message or message.get("type") != MessageProtocol.TYPE_AUTH:
                self.send_message(
                    MessageProtocol.create_message(
                        MessageProtocol.TYPE_ERROR,
                        "system",
                        "Authentication required before chatting."
                    )
                )
                return

            auth_result = self.authenticate(
                self,
                message.get("username", "").strip(),
                message.get("password", "")
            )
            if not auth_result.get("ok"):
                self.send_message(
                    MessageProtocol.create_message(
                        MessageProtocol.TYPE_ERROR,
                        "system",
                        auth_result.get("error", "Login failed.")
                    )
                )
                return

            self.username = auth_result["username"]
            self.current_room = auth_result.get("room", "lobby")
            self.authenticated = True
            print(f"[SERVER] {self.username} connected from {self.address}")

            self.send_message(
                MessageProtocol.create_message(
                    MessageProtocol.TYPE_AUTH_OK,
                    self.username,
                    auth_result.get("message", "Login successful."),
                    room=self.current_room,
                    rooms=self.list_rooms(),
                )
            )
            self.send_message(
                MessageProtocol.create_message(
                    MessageProtocol.TYPE_HISTORY,
                    "system",
                    f"Recent messages for {self.current_room}",
                    room=self.current_room,
                    messages=self.get_history(self.current_room)
                )
            )
            self.send_message(
                MessageProtocol.create_message(
                    MessageProtocol.TYPE_USER_LIST,
                    "system",
                    "Online users",
                    users=self.list_users()
                )
            )
            join_msg = MessageProtocol.create_message(
                MessageProtocol.TYPE_JOIN,
                self.username,
                f"{self.username} joined {self.current_room}",
                room=self.current_room
            )
            self.broadcast(join_msg, exclude=self, room=self.current_room)
            
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
                        room_name = message.get("room") or self.current_room
                        message["room"] = room_name
                        self.current_room = room_name
                        print(f"[{self.username}]: {message.get('content', '')}")
                        self.broadcast(message, exclude=None, room=room_name)
                    
                    elif msg_type == MessageProtocol.TYPE_LEAVE:
                        break
                    elif msg_type == MessageProtocol.TYPE_ROOM_JOIN:
                        room_name = message.get("room", "").strip()
                        room_result = self.join_room(self, room_name)
                        if room_result.get("ok"):
                            previous_room = room_result.get("previous_room")
                            current_room = room_result.get("room", self.current_room)
                            if previous_room and previous_room != current_room:
                                self.broadcast(
                                    MessageProtocol.create_message(
                                        MessageProtocol.TYPE_LEAVE,
                                        self.username,
                                        f"{self.username} left {previous_room}",
                                        room=previous_room
                                    ),
                                    exclude=self,
                                    room=previous_room
                                )
                                self.broadcast(
                                    MessageProtocol.create_message(
                                        MessageProtocol.TYPE_JOIN,
                                        self.username,
                                        f"{self.username} joined {current_room}",
                                        room=current_room
                                    ),
                                    exclude=self,
                                    room=current_room
                                )
                            self.send_message(
                                MessageProtocol.create_message(
                                    MessageProtocol.TYPE_ROOM_JOIN,
                                    "system",
                                    room_result["message"],
                                    room=current_room
                                )
                            )
                            self.send_message(
                                MessageProtocol.create_message(
                                    MessageProtocol.TYPE_HISTORY,
                                    "system",
                                    f"Recent messages for {current_room}",
                                    room=current_room,
                                    messages=self.get_history(current_room)
                                )
                            )
                        else:
                            self.send_message(
                                MessageProtocol.create_message(
                                    MessageProtocol.TYPE_ERROR,
                                    "system",
                                    room_result.get("error", "Unable to join room.")
                                )
                            )
                    elif msg_type == MessageProtocol.TYPE_ROOM_LIST:
                        self.send_message(
                            MessageProtocol.create_message(
                                MessageProtocol.TYPE_ROOM_LIST,
                                "system",
                                "Available rooms",
                                rooms=self.list_rooms()
                            )
                        )
                    elif msg_type == MessageProtocol.TYPE_USER_LIST:
                        self.send_message(
                            MessageProtocol.create_message(
                                MessageProtocol.TYPE_USER_LIST,
                                "system",
                                "Online users",
                                users=self.list_users()
                            )
                        )
                    elif msg_type == MessageProtocol.TYPE_FILE:
                        recipient = message.get("recipient", "").strip()
                        file_data = message.get("file_data", "")
                        filename = message.get("filename", "").strip()
                        if not recipient or not filename or not file_data:
                            self.send_message(
                                MessageProtocol.create_message(
                                    MessageProtocol.TYPE_ERROR,
                                    "system",
                                    "File transfer requires a recipient and a file."
                                )
                            )
                            continue

                        direct_result = self.send_direct_message(
                            recipient,
                            MessageProtocol.create_message(
                                MessageProtocol.TYPE_FILE,
                                self.username,
                                message.get("content", f"File from {self.username}"),
                                sender=self.username,
                                recipient=recipient,
                                filename=filename,
                                file_data=file_data,
                                filesize=message.get("filesize", 0),
                                room=self.current_room
                            )
                        )
                        if direct_result.get("ok"):
                            self.send_message(
                                MessageProtocol.create_message(
                                    MessageProtocol.TYPE_SYSTEM,
                                    "system",
                                    f"Sent '{filename}' to {recipient}."
                                )
                            )
                        else:
                            self.send_message(
                                MessageProtocol.create_message(
                                    MessageProtocol.TYPE_ERROR,
                                    "system",
                                    direct_result.get("error", "Unable to send file.")
                                )
                            )
        
        except ConnectionResetError:
            print(f"[SERVER] Connection reset by {self.username or self.address}")
        except Exception as e:
            print(f"[SERVER] Error handling client {self.username or self.address}: {e}")
        finally:
            self.cleanup()
    
    def send_message(self, message: dict) -> bool:
        """
        Send a message to this client.
        
        Args:
            message: Message dictionary to send
        """
        try:
            encoded = MessageProtocol.encode_message(message)
            self.socket.sendall(encoded)
            return True
        except Exception as e:
            print(f"[SERVER] Error sending to {self.username}: {e}")
            self.running = False
            return False
    
    def cleanup(self):
        """
        Clean up resources when client disconnects.
        This ensures proper resource management and notifies other clients.
        """
        self.running = False

        room_name = self.current_room
        username = self.username

        # Remove this client from the server's active lists before broadcasting.
        # This prevents later broadcasts from trying to reuse a dead socket.
        self.remove_client(self)
        
        # Announce departure to other clients
        if username:
            leave_msg = MessageProtocol.create_message(
                MessageProtocol.TYPE_LEAVE,
                username,
                f"{username} left {room_name}",
                room=room_name
            )
            self.broadcast(leave_msg, exclude=self, room=room_name)
            print(f"[SERVER] {username} disconnected")
        
        # Close the socket
        try:
            self.socket.close()
        except:
            pass
