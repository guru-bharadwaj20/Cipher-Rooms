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
    TYPE_PRIVATE = "private"
    TYPE_FILE_OFFER = "file_offer"
    TYPE_FILE_CHUNK = "file_chunk"
    TYPE_FILE_END = "file_end"
    TYPE_FILE_ACK = "file_ack"
    TYPE_FILE_ERROR = "file_error"
    
    @staticmethod
    def create_message(msg_type: str, username: str, content: str, **extra_fields) -> Dict:
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
        if extra_fields:
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
        to_username = message.get("to_username")
        
        if msg_type == MessageProtocol.TYPE_CHAT:
            return f"[{username}]: {content}"
        elif msg_type == MessageProtocol.TYPE_PRIVATE:
            if to_username:
                return f"[DM] [{username} -> {to_username}]: {content}"
            return f"[DM] [{username}]: {content}"
        elif msg_type == MessageProtocol.TYPE_FILE_OFFER:
            filename = message.get("filename", "unknown")
            size = message.get("size", 0)
            if to_username:
                return f"[FILE] {username} -> {to_username}: {filename} ({size} bytes)"
            return f"[FILE] {username}: {filename} ({size} bytes)"
        elif msg_type == MessageProtocol.TYPE_FILE_ACK:
            transfer_id = message.get("transfer_id", "")
            stage = message.get("stage", "")
            return f"[FILE-ACK] transfer={transfer_id} stage={stage}"
        elif msg_type == MessageProtocol.TYPE_FILE_ERROR:
            transfer_id = message.get("transfer_id", "")
            return f"[FILE-ERROR] transfer={transfer_id} {content}"
        elif msg_type == MessageProtocol.TYPE_JOIN:
            return f"*** {username} joined the chat ***"
        elif msg_type == MessageProtocol.TYPE_LEAVE:
            return f"*** {username} left the chat ***"
        elif msg_type == MessageProtocol.TYPE_SYSTEM:
            return f"[SYSTEM]: {content}"
        elif msg_type == MessageProtocol.TYPE_ERROR:
            return f"[ERROR]: {content}"
        else:
            return f"[{username}]: {content}"
