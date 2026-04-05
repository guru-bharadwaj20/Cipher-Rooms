"""
User persistence module.

Stores user profiles and per-user chat history on disk so users can
log back in and continue from previous sessions.
"""

import json
import os
import threading
from datetime import datetime
from typing import Dict, List


class UserStore:
    """Persists user profiles and chat history using JSON/JSONL files."""

    def __init__(self, base_dir: str = 'data'):
        self.base_dir = base_dir
        self.history_dir = os.path.join(self.base_dir, 'history')
        self.room_history_dir = os.path.join(self.base_dir, 'room_history')
        self.profiles_file = os.path.join(self.base_dir, 'profiles.json')
        self.lock = threading.Lock()
        self.max_room_history_messages = 1000

        os.makedirs(self.history_dir, exist_ok=True)
        os.makedirs(self.room_history_dir, exist_ok=True)
        if not os.path.exists(self.profiles_file):
            with open(self.profiles_file, 'w', encoding='utf-8') as f:
                json.dump({}, f)

    def _load_profiles_locked(self) -> Dict:
        try:
            with open(self.profiles_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return {}

    def _save_profiles_locked(self, profiles: Dict):
        with open(self.profiles_file, 'w', encoding='utf-8') as f:
            json.dump(profiles, f, indent=2)

    def list_users(self) -> List[str]:
        """Return all known usernames."""
        with self.lock:
            profiles = self._load_profiles_locked()
            return sorted(profiles.keys())

    def register_login(self, username: str) -> Dict:
        """Create/update user profile and return login metadata."""
        now = datetime.now().isoformat()
        with self.lock:
            profiles = self._load_profiles_locked()
            existing = profiles.get(username)
            is_returning = existing is not None

            if not existing:
                profile = {
                    'username': username,
                    'created_at': now,
                    'last_login': now,
                    'last_seen': None,
                    'login_count': 1
                }
            else:
                profile = existing
                profile['last_login'] = now
                profile['login_count'] = int(profile.get('login_count', 0)) + 1

            profiles[username] = profile
            self._save_profiles_locked(profiles)

        return {
            'is_returning': is_returning,
            'profile': profile
        }

    def update_last_seen(self, username: str):
        """Persist the timestamp of user's most recent disconnection."""
        if not username:
            return

        with self.lock:
            profiles = self._load_profiles_locked()
            profile = profiles.get(username)
            if not profile:
                return

            profile['last_seen'] = datetime.now().isoformat()
            profiles[username] = profile
            self._save_profiles_locked(profiles)

    def _history_file_for(self, username: str) -> str:
        # Keep username-based filename simple and safe.
        safe_username = ''.join(c for c in username if c.isalnum() or c in ('_', '-'))
        if not safe_username:
            safe_username = 'unknown'
        return os.path.join(self.history_dir, f'{safe_username}.jsonl')

    def _room_history_file_for(self, room_name: str) -> str:
        safe_room = ''.join(c for c in room_name if c.isalnum() or c in ('_', '-'))
        if not safe_room:
            safe_room = 'global'
        return os.path.join(self.room_history_dir, f'{safe_room}.jsonl')

    def append_message_for_users(self, usernames: List[str], message: Dict):
        """Append one message to each listed user's history."""
        if not usernames or not message:
            return

        line = json.dumps(message, ensure_ascii=True)
        unique_users = set(usernames)

        with self.lock:
            for username in unique_users:
                history_file = self._history_file_for(username)
                with open(history_file, 'a', encoding='utf-8') as f:
                    f.write(line + '\n')

    def get_user_history(self, username: str, limit: int = 50) -> List[Dict]:
        """Read the latest messages for a user."""
        history_file = self._history_file_for(username)
        if not os.path.exists(history_file):
            return []

        messages: List[Dict] = []
        with self.lock:
            with open(history_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        message = json.loads(line)
                        if isinstance(message, dict):
                            messages.append(message)
                    except json.JSONDecodeError:
                        continue

        if limit and len(messages) > limit:
            return messages[-limit:]
        return messages

    def _truncate_room_history_locked(self, history_file: str):
        """Keep only the latest configured number of room history messages."""
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                lines = [line for line in f if line.strip()]
        except FileNotFoundError:
            return

        if len(lines) <= self.max_room_history_messages:
            return

        trimmed = lines[-self.max_room_history_messages:]
        with open(history_file, 'w', encoding='utf-8') as f:
            f.writelines(trimmed)

    def append_room_message(self, room_name: str, message: Dict):
        """Append one message to room timeline and enforce truncation policy."""
        if not room_name or not message:
            return

        line = json.dumps(message, ensure_ascii=True)
        history_file = self._room_history_file_for(room_name)

        with self.lock:
            with open(history_file, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
            self._truncate_room_history_locked(history_file)

    def get_room_history(self, room_name: str, limit: int = 50) -> List[Dict]:
        """Read latest timeline messages for one room."""
        if not room_name:
            return []

        history_file = self._room_history_file_for(room_name)
        if not os.path.exists(history_file):
            return []

        messages: List[Dict] = []
        with self.lock:
            with open(history_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        message = json.loads(line)
                        if isinstance(message, dict):
                            messages.append(message)
                    except json.JSONDecodeError:
                        continue

        if limit and len(messages) > limit:
            return messages[-limit:]
        return messages
