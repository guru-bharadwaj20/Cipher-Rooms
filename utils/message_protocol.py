"""
Message Protocol Module

This module defines the message format and provides functions for encoding/decoding
messages between clients and server. Using JSON for simplicity and human-readability.

Message Format:
{
    "type": "chat" | "join" | "leave" | "error",
    "username": "string",
    "content": "string",
    "timestamp": "ISO-8601 timestamp"
}
"""

import json
from datetime import datetime
from typing import Dict, Optional


class MessageProtocol:
    """
    Handles message encoding and decoding for the chat protocol.
    """
    
    # Message type constants
    TYPE_CHAT = "chat"
    TYPE_JOIN = "join"
    TYPE_LEAVE = "leave"
    TYPE_ERROR = "error"
    TYPE_SYSTEM = "system"
    TYPE_AUTH = "auth"
    TYPE_AUTH_OK = "auth_ok"
    TYPE_ROOM_JOIN = "room_join"
    TYPE_ROOM_LIST = "room_list"
    TYPE_HISTORY = "history"
    TYPE_USER_LIST = "user_list"
    TYPE_FILE = "file"
    
    @staticmethod
    def create_message(
        msg_type: str,
        username: str,
        content: str,
        **extra_fields
    ) -> Dict:
        """
        Create a message dictionary with the standard format.
        
        Args:
            msg_type: Type of message (chat, join, leave, error, system)
            username: Username of the sender
            content: Message content
            
        Returns:
            Dictionary containing the formatted message
        """
        message = {
            "type": msg_type,
            "username": username,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        message.update(extra_fields)
        return message
    
    @staticmethod
    def encode_message(message: Dict) -> bytes:
        """
        Encode a message dictionary to bytes for transmission over the network.
        
        The encoding process:
        1. Convert dictionary to JSON string
        2. Encode JSON string to UTF-8 bytes
        3. Append a newline delimiter for message framing
        
        Args:
            message: Message dictionary to encode
            
        Returns:
            Encoded message as bytes
        """
        json_str = json.dumps(message)
        return (json_str + "\n").encode('utf-8')
    
    @staticmethod
    def decode_message(data: bytes) -> Optional[Dict]:
        """
        Decode received bytes into a message dictionary.
        
        Args:
            data: Raw bytes received from the network
            
        Returns:
            Decoded message dictionary, or None if decoding fails
        """
        try:
            json_str = data.decode('utf-8').strip()
            return json.loads(json_str)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"Error decoding message: {e}")
            return None
    
    @staticmethod
    def format_display_message(message: Dict) -> str:
        """
        Format a message for display to the user.
        
        Args:
            message: Message dictionary
            
        Returns:
            Formatted string for display
        """
        msg_type = message.get("type", "")
        username = message.get("username", "Unknown")
        content = message.get("content", "")
        room = message.get("room")
        
        if msg_type == MessageProtocol.TYPE_CHAT:
            if room:
                return f"[{room}] {username}: {content}"
            return f"[{username}]: {content}"
        elif msg_type == MessageProtocol.TYPE_JOIN:
            if room:
                return f"*** {username} joined {room} ***"
            return f"*** {username} joined the chat ***"
        elif msg_type == MessageProtocol.TYPE_LEAVE:
            if room:
                return f"*** {username} left {room} ***"
            return f"*** {username} left the chat ***"
        elif msg_type == MessageProtocol.TYPE_ROOM_JOIN:
            return f"*** {content} ***"
        elif msg_type == MessageProtocol.TYPE_ROOM_LIST:
            rooms = message.get("rooms", [])
            return f"[SYSTEM]: Rooms: {', '.join(rooms) if rooms else 'No rooms available'}"
        elif msg_type == MessageProtocol.TYPE_HISTORY:
            history = message.get("messages", [])
            if not history:
                return "[SYSTEM]: No recent room history."
            lines = ["[SYSTEM]: Recent room history:"]
            for entry in history:
                entry_user = entry.get("username", "Unknown")
                entry_content = entry.get("content", "")
                lines.append(f"  {entry_user}: {entry_content}")
            return "\n".join(lines)
        elif msg_type == MessageProtocol.TYPE_AUTH_OK:
            room_name = message.get("room", "lobby")
            return f"[SYSTEM]: Logged in as {username}. Current room: {room_name}"
        elif msg_type == MessageProtocol.TYPE_USER_LIST:
            users = message.get("users", [])
            return f"[SYSTEM]: Online users: {', '.join(users) if users else 'No users online'}"
        elif msg_type == MessageProtocol.TYPE_FILE:
            filename = message.get("filename", "file")
            recipient = message.get("recipient", "")
            sender = message.get("sender", username)
            if recipient:
                return f"[FILE] {sender} -> {recipient}: {filename}"
            return f"[FILE] {sender}: {filename}"
        elif msg_type == MessageProtocol.TYPE_SYSTEM:
            return f"[SYSTEM]: {content}"
        elif msg_type == MessageProtocol.TYPE_ERROR:
            return f"[ERROR]: {content}"
        else:
            return f"[{username}]: {content}"
