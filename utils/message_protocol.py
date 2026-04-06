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
from typing import Dict, Optional, Tuple


class MessageProtocol:
    """
    Handles message encoding and decoding for the chat protocol.
    """
    
    PROTOCOL_VERSION = "1.1"

    # Message type constants
    TYPE_CHAT = "chat"
    TYPE_JOIN = "join"
    TYPE_LEAVE = "leave"
    TYPE_ERROR = "error"
    TYPE_SYSTEM = "system"
    TYPE_ACK = "ack"
    TYPE_PRIVATE = "private"
    TYPE_ROOM_JOIN = "room_join"
    TYPE_ROOM_LEAVE = "room_leave"
    TYPE_ROOM_LIST = "room_list"
    TYPE_FILE_OFFER = "file_offer"
    TYPE_FILE_CHUNK = "file_chunk"
    TYPE_FILE_END = "file_end"
    TYPE_FILE_ACK = "file_ack"
    TYPE_FILE_ERROR = "file_error"

    # Protocol error code constants.
    ERR_BAD_FORMAT = "ERR_BAD_FORMAT"
    ERR_UNKNOWN_TYPE = "ERR_UNKNOWN_TYPE"
    ERR_MISSING_FIELD = "ERR_MISSING_FIELD"
    ERR_INVALID_FIELD = "ERR_INVALID_FIELD"

    # Required fields by message type (in addition to base fields).
    _BASE_FIELDS = {"type", "username", "content", "timestamp", "protocol_version"}
    _TYPE_REQUIRED_FIELDS = {
        TYPE_CHAT: set(),
        TYPE_JOIN: set(),
        TYPE_LEAVE: set(),
        TYPE_ERROR: set(),
        TYPE_SYSTEM: set(),
        TYPE_ACK: {"ack_for"},
        TYPE_PRIVATE: {"to_username", "message_id"},
        TYPE_ROOM_JOIN: {"room_name"},
        TYPE_ROOM_LEAVE: set(),
        TYPE_ROOM_LIST: {"rooms"},
        TYPE_FILE_OFFER: {"to_username", "transfer_id", "filename", "size", "checksum", "total_chunks"},
        TYPE_FILE_CHUNK: {"to_username", "transfer_id", "chunk_index", "total_chunks", "chunk_data"},
        TYPE_FILE_END: {"to_username", "transfer_id"},
        TYPE_FILE_ACK: {"to_username", "transfer_id"},
        TYPE_FILE_ERROR: {"transfer_id"},
    }
    
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
            "timestamp": datetime.now().isoformat(),
            "protocol_version": MessageProtocol.PROTOCOL_VERSION,
        }
        if extra_fields:
            message.update(extra_fields)
        return message

    @staticmethod
    def normalize_message(message: Dict) -> Dict:
        """Normalize loose payloads to protocol baseline for compatibility."""
        normalized = dict(message)
        normalized.setdefault("timestamp", datetime.now().isoformat())
        normalized.setdefault("protocol_version", MessageProtocol.PROTOCOL_VERSION)
        return normalized

    @staticmethod
    def validate_message(message: Dict) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Validate message shape and type-specific required fields.

        Returns:
            (is_valid, error_code, error_detail)
        """
        if not isinstance(message, dict):
            return False, MessageProtocol.ERR_BAD_FORMAT, "Payload must be a JSON object."

        msg_type = message.get("type")
        if not isinstance(msg_type, str) or not msg_type:
            return False, MessageProtocol.ERR_MISSING_FIELD, "Missing required field: type"

        if msg_type not in MessageProtocol._TYPE_REQUIRED_FIELDS:
            return False, MessageProtocol.ERR_UNKNOWN_TYPE, f"Unknown message type: {msg_type}"

        missing_base = [f for f in MessageProtocol._BASE_FIELDS if f not in message]
        if missing_base:
            return False, MessageProtocol.ERR_MISSING_FIELD, f"Missing required field(s): {', '.join(sorted(missing_base))}"

        required_fields = MessageProtocol._TYPE_REQUIRED_FIELDS.get(msg_type, set())
        missing_type_fields = [f for f in required_fields if f not in message]
        if missing_type_fields:
            return False, MessageProtocol.ERR_MISSING_FIELD, f"Missing required field(s): {', '.join(sorted(missing_type_fields))}"

        if not isinstance(message.get("protocol_version"), str):
            return False, MessageProtocol.ERR_INVALID_FIELD, "Field protocol_version must be a string."
        if not isinstance(message.get("timestamp"), str):
            return False, MessageProtocol.ERR_INVALID_FIELD, "Field timestamp must be a string."
        if not isinstance(message.get("username"), str):
            return False, MessageProtocol.ERR_INVALID_FIELD, "Field username must be a string."
        if not isinstance(message.get("content"), str):
            return False, MessageProtocol.ERR_INVALID_FIELD, "Field content must be a string."

        if msg_type == MessageProtocol.TYPE_PRIVATE:
            if not isinstance(message.get("to_username"), str):
                return False, MessageProtocol.ERR_INVALID_FIELD, "Field to_username must be a string for private messages."

        if msg_type in {MessageProtocol.TYPE_ROOM_JOIN, MessageProtocol.TYPE_ROOM_LEAVE}:
            room_name = message.get("room_name")
            if room_name is not None and not isinstance(room_name, str):
                return False, MessageProtocol.ERR_INVALID_FIELD, "Field room_name must be a string when present."

        if msg_type in {MessageProtocol.TYPE_FILE_OFFER, MessageProtocol.TYPE_FILE_CHUNK, MessageProtocol.TYPE_FILE_END, MessageProtocol.TYPE_FILE_ACK, MessageProtocol.TYPE_FILE_ERROR}:
            if not isinstance(message.get("transfer_id"), str):
                return False, MessageProtocol.ERR_INVALID_FIELD, "Field transfer_id must be a string for file operations."

        return True, None, None
    
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
        normalized = MessageProtocol.normalize_message(message)
        json_str = json.dumps(normalized)
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
            decoded = json.loads(json_str)
            if isinstance(decoded, dict):
                return MessageProtocol.normalize_message(decoded)
            return None
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
        room_name = message.get("room_name", "")
        to_username = message.get("to_username", "")
        
        if msg_type == MessageProtocol.TYPE_CHAT:
            if room_name:
                return f"[{room_name}] [{username}]: {content}"
            return f"[{username}]: {content}"
        elif msg_type == MessageProtocol.TYPE_PRIVATE:
            return f"[DM] [{username} -> {to_username}]: {content}"
        elif msg_type == MessageProtocol.TYPE_ROOM_JOIN:
            return f"*** {username} joined room '{room_name}' ***"
        elif msg_type == MessageProtocol.TYPE_ROOM_LEAVE:
            return f"*** {username} left room '{room_name}' ***"
        elif msg_type == MessageProtocol.TYPE_FILE_ERROR:
            transfer_id = message.get("transfer_id", "")
            return f"[FILE-ERROR] transfer={transfer_id} {content}"
        elif msg_type == MessageProtocol.TYPE_FILE_ACK:
            transfer_id = message.get("transfer_id", "")
            return f"[FILE-ACK] transfer={transfer_id} {content}"
        elif msg_type == MessageProtocol.TYPE_JOIN:
            return f"*** {username} joined the chat ***"
        elif msg_type == MessageProtocol.TYPE_LEAVE:
            return f"*** {username} left the chat ***"
        elif msg_type == MessageProtocol.TYPE_SYSTEM:
            return f"[SYSTEM]: {content}"
        elif msg_type == MessageProtocol.TYPE_ERROR:
            code = message.get("error_code", "")
            if code:
                return f"[ERROR:{code}]: {content}"
            return f"[ERROR]: {content}"
        else:
            return f"[{username}]: {content}"
