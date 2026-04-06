import base64
import getpass
import os
import queue
import socket
import ssl
import threading
import time
from typing import Callable, Dict, List, Optional

from utils.message_protocol import MessageProtocol


class ChatClient:
    """Networking layer for the Pulse-Chat client."""

    MAX_FILE_SIZE = 2 * 1024 * 1024

    def __init__(
        self,
        server_host: str = "localhost",
        server_port: int = 5555,
        username: Optional[str] = None,
        password: Optional[str] = None,
        event_callback: Optional[Callable[[str, Dict], None]] = None,
    ):
        self.server_host = server_host
        self.server_port = server_port
        self.username = username
        self.password = password
        self.event_callback = event_callback

        self.socket = None
        self.running = False
        self.connected = False
        self.session_ready = False
        self.manual_disconnect = False
        self.receive_thread = None
        self.reconnect_thread = None
        self.current_room = "lobby"
        self.socket_lock = threading.Lock()
        self.pending_lock = threading.Lock()
        self.reconnect_delay = 3
        self.pending_messages: List[Dict] = []
        self.known_rooms: List[str] = []
        self.known_users: List[str] = []

    def set_credentials(self, username: str, password: str):
        self.username = username.strip()
        self.password = password

    def start(self):
        if self.running:
            return
        self.running = True
        self.manual_disconnect = False
        self.reconnect_thread = threading.Thread(
            target=self._reconnect_loop,
            daemon=True,
        )
        self.reconnect_thread.start()

    def stop(self):
        self.manual_disconnect = True
        self.running = False
        self.session_ready = False
        if self.connected:
            leave_message = MessageProtocol.create_message(
                MessageProtocol.TYPE_LEAVE,
                self.username or "unknown",
                f"{self.username or 'User'} left",
                room=self.current_room,
            )
            self._send_raw_message(leave_message)
        self._close_socket()
        self._emit_status("Disconnected.")

    def connect(self) -> bool:
        if not self.username or not self.password:
            self._emit_error("Username and password are required.")
            return False

        try:
            client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            secure_socket = ssl_context.wrap_socket(
                client_socket,
                server_hostname=self.server_host,
            )
            self._emit_status(
                f"Connecting to {self.server_host}:{self.server_port}..."
            )
            secure_socket.connect((self.server_host, self.server_port))

            auth_message = MessageProtocol.create_message(
                MessageProtocol.TYPE_AUTH,
                self.username,
                "authenticate",
                password=self.password,
            )

            with self.socket_lock:
                self.socket = secure_socket
                self.connected = True
                self.session_ready = False

            self._send_raw_message(auth_message)
            self._emit_status("Connected. Authenticating...")
            return True

        except ConnectionRefusedError:
            self._emit_status("Server unavailable. Retrying...")
            return False
        except ssl.SSLError as exc:
            self._emit_error(f"SSL error: {exc}")
            return False
        except Exception as exc:
            self._emit_error(f"Connection error: {exc}")
            return False

    def send_chat(self, content: str):
        content = content.strip()
        if not content:
            return
        self._queue_message(
            MessageProtocol.create_message(
                MessageProtocol.TYPE_CHAT,
                self.username or "unknown",
                content,
                room=self.current_room,
            ),
            buffered_notice=f"Buffered message for room '{self.current_room}'.",
        )

    def join_room(self, room_name: str):
        room_name = room_name.strip()
        if not room_name:
            self._emit_error("Enter a room name first.")
            return
        self._queue_message(
            MessageProtocol.create_message(
                MessageProtocol.TYPE_ROOM_JOIN,
                self.username or "unknown",
                f"join {room_name}",
                room=room_name,
            ),
            buffered_notice=f"Buffered room switch to '{room_name}'.",
        )

    def request_rooms(self):
        self._queue_message(
            MessageProtocol.create_message(
                MessageProtocol.TYPE_ROOM_LIST,
                self.username or "unknown",
                "list rooms",
            ),
            buffered_notice="Buffered room refresh request.",
        )

    def request_users(self):
        self._queue_message(
            MessageProtocol.create_message(
                MessageProtocol.TYPE_USER_LIST,
                self.username or "unknown",
                "list users",
            ),
            buffered_notice="Buffered user refresh request.",
        )

    def send_file(self, recipient: str, file_path: str):
        recipient = recipient.strip()
        if not recipient:
            self._emit_error("Choose a recipient before sending a file.")
            return
        if not file_path or not os.path.exists(file_path):
            self._emit_error("Choose a valid file to send.")
            return

        file_size = os.path.getsize(file_path)
        if file_size > self.MAX_FILE_SIZE:
            self._emit_error("Files must be 2 MB or smaller.")
            return

        with open(file_path, "rb") as file_handle:
            encoded_data = base64.b64encode(file_handle.read()).decode("ascii")

        filename = os.path.basename(file_path)
        self._queue_message(
            MessageProtocol.create_message(
                MessageProtocol.TYPE_FILE,
                self.username or "unknown",
                f"Send file {filename}",
                sender=self.username,
                recipient=recipient,
                filename=filename,
                filesize=file_size,
                file_data=encoded_data,
                room=self.current_room,
            ),
            buffered_notice=f"Buffered file '{filename}' for {recipient}.",
        )

    def _queue_message(self, message: Dict, buffered_notice: str):
        if self.connected and self.session_ready:
            if self._send_raw_message(message):
                return

        with self.pending_lock:
            self.pending_messages.append(message)
            pending_count = len(self.pending_messages)

        self._emit(
            "buffered",
            count=pending_count,
            message=buffered_notice,
        )
        self._emit_system(buffered_notice)

    def _send_raw_message(self, message: Dict) -> bool:
        with self.socket_lock:
            active_socket = self.socket

        if not active_socket or not self.connected:
            return False

        try:
            encoded = MessageProtocol.encode_message(message)
            active_socket.sendall(encoded)
            return True
        except Exception as exc:
            self._handle_disconnect(f"Connection dropped while sending: {exc}")
            return False

    def _start_receiver(self):
        self.receive_thread = threading.Thread(
            target=self._receive_messages,
            daemon=True,
        )
        self.receive_thread.start()

    def _receive_messages(self):
        with self.socket_lock:
            active_socket = self.socket

        if not active_socket:
            return

        try:
            server_file = active_socket.makefile("rb")

            while self.running and self.connected:
                data = server_file.readline()
                if not data:
                    self._handle_disconnect("Server stopped or connection closed.")
                    break

                message = MessageProtocol.decode_message(data)
                if not message:
                    continue
                self._handle_incoming_message(message)

        except ConnectionResetError:
            self._handle_disconnect("Connection lost.")
        except Exception as exc:
            if self.running:
                self._handle_disconnect(f"Error receiving messages: {exc}")

    def _handle_incoming_message(self, message: Dict):
        msg_type = message.get("type")

        if msg_type in (
            MessageProtocol.TYPE_AUTH_OK,
            MessageProtocol.TYPE_ROOM_JOIN,
            MessageProtocol.TYPE_CHAT,
        ):
            self.current_room = message.get("room", self.current_room)

        if msg_type == MessageProtocol.TYPE_AUTH_OK:
            self.session_ready = True
            self._emit_status(
                f"Connected as {self.username}. Current room: {self.current_room}"
            )
            self._flush_pending_messages()

        if msg_type == MessageProtocol.TYPE_ROOM_LIST:
            self.known_rooms = message.get("rooms", [])
            self._emit("rooms_updated", rooms=self.known_rooms)

        if msg_type == MessageProtocol.TYPE_USER_LIST:
            self.known_users = message.get("users", [])
            self._emit("users_updated", users=self.known_users)

        if msg_type == MessageProtocol.TYPE_FILE:
            saved_path = self._save_incoming_file(message)
            notice = MessageProtocol.create_message(
                MessageProtocol.TYPE_SYSTEM,
                "system",
                f"Received '{message.get('filename', 'file')}' from "
                f"{message.get('sender', message.get('username', 'Unknown'))}. "
                f"Saved to {saved_path}.",
            )
            self._emit("message", message=notice)
            self._emit("file_received", path=saved_path, message=message)
            return

        self._emit("message", message=message)

    def _save_incoming_file(self, message: Dict) -> str:
        sender = message.get("sender", message.get("username", "unknown"))
        filename = message.get("filename", "file")
        file_data = message.get("file_data", "")

        downloads_dir = os.path.join("downloads", self.username or "user")
        os.makedirs(downloads_dir, exist_ok=True)

        safe_sender = "".join(
            ch for ch in sender if ch.isalnum() or ch in ("_", "-")
        ) or "unknown"
        safe_filename = "".join(
            ch for ch in filename if ch.isalnum() or ch in ("_", "-", ".")
        ) or "file.bin"

        target_path = os.path.join(downloads_dir, f"{safe_sender}_{safe_filename}")
        if os.path.exists(target_path):
            base, ext = os.path.splitext(target_path)
            counter = 1
            while os.path.exists(target_path):
                target_path = f"{base}_{counter}{ext}"
                counter += 1

        with open(target_path, "wb") as output_file:
            output_file.write(base64.b64decode(file_data.encode("ascii")))

        return target_path

    def _flush_pending_messages(self):
        with self.pending_lock:
            if not self.pending_messages:
                self._emit("buffered", count=0, message="No buffered items.")
                return
            pending = list(self.pending_messages)
            self.pending_messages.clear()

        delivered_count = 0
        unsent: List[Dict] = []
        for message in pending:
            if self.connected and self.session_ready and self._send_raw_message(message):
                delivered_count += 1
            else:
                unsent.append(message)

        if unsent:
            with self.pending_lock:
                self.pending_messages = unsent + self.pending_messages
                pending_count = len(self.pending_messages)
            self._emit(
                "buffered",
                count=pending_count,
                message=f"{pending_count} buffered item(s) still waiting.",
            )
            return

        self._emit("buffered", count=0, message="Buffered items sent.")
        if delivered_count:
            self._emit_system(f"Sent {delivered_count} buffered item(s).")

    def _reconnect_loop(self):
        while self.running:
            if self.connected:
                time.sleep(1)
                continue

            if self.connect():
                self._start_receiver()
            time.sleep(self.reconnect_delay)

    def _handle_disconnect(self, reason: str):
        was_connected = self.connected or self.session_ready
        self.connected = False
        self.session_ready = False
        self._close_socket()

        if self.running and not self.manual_disconnect and was_connected:
            self._emit_error(reason)
            self._emit_status("Offline. Waiting for the server to come back...")

    def _close_socket(self):
        with self.socket_lock:
            if self.socket:
                try:
                    self.socket.close()
                except Exception:
                    pass
                self.socket = None
        self.connected = False

    def _emit_system(self, content: str):
        self._emit(
            "message",
            message=MessageProtocol.create_message(
                MessageProtocol.TYPE_SYSTEM,
                "system",
                content,
            ),
        )

    def _emit_error(self, content: str):
        self._emit(
            "message",
            message=MessageProtocol.create_message(
                MessageProtocol.TYPE_ERROR,
                "system",
                content,
            ),
        )

    def _emit_status(self, content: str):
        self._emit("status", text=content)

    def _emit(self, event_type: str, **payload):
        if self.event_callback:
            self.event_callback(event_type, payload)


class TerminalChatClient:
    """CLI wrapper around the reconnecting chat client."""

    def __init__(
        self,
        server_host: str = "localhost",
        server_port: int = 5555,
        username: Optional[str] = None,
    ):
        self.server_host = server_host
        self.server_port = server_port
        self.username = username or self._prompt_username()
        self.password = self._prompt_password()
        self.client = ChatClient(
            server_host=server_host,
            server_port=server_port,
            username=self.username,
            password=self.password,
            event_callback=self._handle_event,
        )

    def _prompt_username(self) -> str:
        username = input("Enter your username: ").strip()
        while not username:
            username = input("Username cannot be empty. Try again: ").strip()
        return username

    def _prompt_password(self) -> str:
        password = getpass.getpass("Enter your password: ").strip()
        while not password:
            password = getpass.getpass("Password cannot be empty. Try again: ").strip()
        return password

    def _handle_event(self, event_type: str, payload: Dict):
        if event_type == "message":
            print(MessageProtocol.format_display_message(payload["message"]))
        elif event_type == "status":
            print(f"[STATUS] {payload['text']}")

    def start(self):
        self.client.start()
        print("=" * 60)
        print("Pulse-Chat CLI started.")
        print("Commands: /rooms, /users, /join <room>, /sendfile <user> <path>, /quit")
        print("=" * 60)

        try:
            while True:
                user_input = input().strip()
                if not user_input:
                    continue
                if user_input == "/quit":
                    break
                if user_input == "/rooms":
                    self.client.request_rooms()
                    continue
                if user_input == "/users":
                    self.client.request_users()
                    continue
                if user_input.startswith("/join "):
                    self.client.join_room(user_input[6:].strip())
                    continue
                if user_input.startswith("/sendfile "):
                    parts = user_input.split(" ", 2)
                    if len(parts) < 3:
                        print("[ERROR] Usage: /sendfile <user> <path>")
                        continue
                    self.client.send_file(parts[1], parts[2])
                    continue
                self.client.send_chat(user_input)
        finally:
            self.client.stop()
            print("[CLIENT] Disconnected")


class ChatClientApp:
    """Simple black-and-white Tkinter UI for Pulse-Chat."""

    def __init__(self, server_host: str = "localhost", server_port: int = 5555):
        import tkinter as tk
        from tkinter import filedialog
        from tkinter import scrolledtext

        self.tk = tk
        self.filedialog = filedialog
        self.root = tk.Tk()
        self.root.title("Pulse-Chat")
        self.root.geometry("980x680")
        self.root.configure(bg="black")

        self.event_queue: "queue.Queue" = queue.Queue()
        self.client: Optional[ChatClient] = None

        self.host_var = tk.StringVar(value=server_host)
        self.port_var = tk.StringVar(value=str(server_port))
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.room_var = tk.StringVar(value="lobby")
        self.join_room_var = tk.StringVar()
        self.recipient_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Disconnected.")
        self.buffer_var = tk.StringVar(value="Buffered: 0")

        self._build_ui(scrolledtext)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(120, self._process_events)

    def _build_ui(self, scrolledtext):
        tk = self.tk

        base_font = ("Courier", 11)
        header_font = ("Courier", 12, "bold")

        top_bar = tk.Frame(self.root, bg="black", padx=12, pady=12)
        top_bar.pack(fill="x")

        self._make_label(top_bar, "Host", header_font).grid(row=0, column=0, sticky="w")
        self._make_entry(top_bar, self.host_var, 16, base_font).grid(row=1, column=0, padx=(0, 8))
        self._make_label(top_bar, "Port", header_font).grid(row=0, column=1, sticky="w")
        self._make_entry(top_bar, self.port_var, 8, base_font).grid(row=1, column=1, padx=(0, 8))
        self._make_label(top_bar, "Username", header_font).grid(row=0, column=2, sticky="w")
        self._make_entry(top_bar, self.username_var, 16, base_font).grid(row=1, column=2, padx=(0, 8))
        self._make_label(top_bar, "Password", header_font).grid(row=0, column=3, sticky="w")
        self._make_entry(
            top_bar,
            self.password_var,
            16,
            base_font,
            show="*",
        ).grid(row=1, column=3, padx=(0, 8))

        tk.Button(
            top_bar,
            text="Connect",
            command=self.connect_client,
            bg="white",
            fg="black",
            activebackground="white",
            activeforeground="black",
            relief="flat",
            padx=16,
            pady=6,
            font=header_font,
        ).grid(row=1, column=4, padx=(8, 8))

        tk.Button(
            top_bar,
            text="Disconnect",
            command=self.disconnect_client,
            bg="black",
            fg="white",
            activebackground="black",
            activeforeground="white",
            relief="solid",
            bd=1,
            padx=16,
            pady=6,
            font=header_font,
        ).grid(row=1, column=5)

        room_bar = tk.Frame(self.root, bg="black", padx=12, pady=8)
        room_bar.pack(fill="x")

        self._make_label(room_bar, "Current Room", header_font).grid(row=0, column=0, sticky="w")
        self._make_label(room_bar, "", base_font, textvariable=self.room_var).grid(row=1, column=0, sticky="w", padx=(0, 12))
        self._make_label(room_bar, "Join Room", header_font).grid(row=0, column=1, sticky="w")
        join_entry = self._make_entry(room_bar, self.join_room_var, 20, base_font)
        join_entry.grid(row=1, column=1, padx=(0, 8))
        join_entry.bind("<Return>", lambda _event: self.join_room())

        tk.Button(
            room_bar,
            text="Join",
            command=self.join_room,
            bg="white",
            fg="black",
            relief="flat",
            padx=12,
            pady=4,
            font=base_font,
        ).grid(row=1, column=2, padx=(0, 8))

        tk.Button(
            room_bar,
            text="Rooms",
            command=self.request_rooms,
            bg="black",
            fg="white",
            relief="solid",
            bd=1,
            padx=12,
            pady=4,
            font=base_font,
        ).grid(row=1, column=3, padx=(0, 8))

        tk.Button(
            room_bar,
            text="Users",
            command=self.request_users,
            bg="black",
            fg="white",
            relief="solid",
            bd=1,
            padx=12,
            pady=4,
            font=base_font,
        ).grid(row=1, column=4)

        content = tk.Frame(self.root, bg="black", padx=12, pady=12)
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=4)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(1, weight=1)

        self._make_label(content, "Conversation", header_font).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self._make_label(content, "Online Users", header_font).grid(row=0, column=1, sticky="w", pady=(0, 8), padx=(12, 0))

        self.chat_output = scrolledtext.ScrolledText(
            content,
            bg="black",
            fg="white",
            insertbackground="white",
            relief="solid",
            bd=1,
            font=base_font,
            wrap="word",
        )
        self.chat_output.grid(row=1, column=0, sticky="nsew")
        self.chat_output.configure(state="disabled")

        sidebar = tk.Frame(content, bg="black")
        sidebar.grid(row=1, column=1, sticky="nsew", padx=(12, 0))
        sidebar.grid_rowconfigure(1, weight=1)

        self._make_label(sidebar, "Recipient", header_font).grid(row=0, column=0, sticky="w")
        recipient_entry = self._make_entry(sidebar, self.recipient_var, 18, base_font)
        recipient_entry.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        self.user_list = tk.Listbox(
            sidebar,
            bg="black",
            fg="white",
            selectbackground="white",
            selectforeground="black",
            relief="solid",
            bd=1,
            font=base_font,
        )
        self.user_list.grid(row=2, column=0, sticky="nsew")
        self.user_list.bind("<<ListboxSelect>>", self._select_recipient)
        sidebar.grid_rowconfigure(2, weight=1)

        footer = tk.Frame(self.root, bg="black", padx=12, pady=12)
        footer.pack(fill="x")
        footer.grid_columnconfigure(0, weight=1)

        self.message_entry = self._make_entry(footer, None, 40, base_font)
        self.message_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.message_entry.bind("<Return>", lambda _event: self.send_message())

        tk.Button(
            footer,
            text="Send",
            command=self.send_message,
            bg="white",
            fg="black",
            relief="flat",
            padx=16,
            pady=6,
            font=header_font,
        ).grid(row=0, column=1, padx=(0, 8))

        tk.Button(
            footer,
            text="Send File",
            command=self.send_file,
            bg="black",
            fg="white",
            relief="solid",
            bd=1,
            padx=16,
            pady=6,
            font=header_font,
        ).grid(row=0, column=2)

        status_bar = tk.Frame(self.root, bg="white", padx=12, pady=8)
        status_bar.pack(fill="x")
        tk.Label(
            status_bar,
            textvariable=self.status_var,
            bg="white",
            fg="black",
            font=base_font,
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        tk.Label(
            status_bar,
            textvariable=self.buffer_var,
            bg="white",
            fg="black",
            font=base_font,
            anchor="e",
        ).pack(side="right")

    def _make_label(self, parent, text, font, textvariable=None):
        return self.tk.Label(
            parent,
            text=text,
            textvariable=textvariable,
            bg="black" if parent is not None and str(parent["bg"]) == "black" else "white",
            fg="white" if parent is not None and str(parent["bg"]) == "black" else "black",
            font=font,
        )

    def _make_entry(self, parent, variable, width, font, show=None):
        return self.tk.Entry(
            parent,
            textvariable=variable,
            width=width,
            bg="black",
            fg="white",
            insertbackground="white",
            relief="solid",
            bd=1,
            font=font,
            show=show,
        )

    def connect_client(self):
        username = self.username_var.get().strip()
        password = self.password_var.get()
        host = self.host_var.get().strip() or "localhost"

        try:
            port = int(self.port_var.get().strip() or "5555")
        except ValueError:
            self._append_text("[ERROR]: Port must be a number.")
            return

        if not username or not password:
            self._append_text("[ERROR]: Username and password are required.")
            return

        if self.client:
            self.client.stop()

        self.client = ChatClient(
            server_host=host,
            server_port=port,
            username=username,
            password=password,
            event_callback=self._queue_event,
        )
        self.client.start()

    def disconnect_client(self):
        if self.client:
            self.client.stop()

    def join_room(self):
        if self.client:
            self.client.join_room(self.join_room_var.get())
            self.join_room_var.set("")

    def request_rooms(self):
        if self.client:
            self.client.request_rooms()

    def request_users(self):
        if self.client:
            self.client.request_users()

    def send_message(self):
        if not self.client:
            self._append_text("[ERROR]: Connect first.")
            return
        message = self.message_entry.get().strip()
        if not message:
            return
        self.client.send_chat(message)
        self.message_entry.delete(0, self.tk.END)

    def send_file(self):
        if not self.client:
            self._append_text("[ERROR]: Connect first.")
            return
        recipient = self.recipient_var.get().strip()
        if not recipient:
            self._append_text("[ERROR]: Choose a recipient first.")
            return
        file_path = self.filedialog.askopenfilename()
        if file_path:
            self.client.send_file(recipient, file_path)

    def _select_recipient(self, _event):
        selected = self.user_list.curselection()
        if not selected:
            return
        username = self.user_list.get(selected[0])
        self.recipient_var.set(username)

    def _queue_event(self, event_type: str, payload: Dict):
        self.event_queue.put((event_type, payload))

    def _process_events(self):
        try:
            while True:
                event_type, payload = self.event_queue.get_nowait()
                self._handle_event(event_type, payload)
        except queue.Empty:
            pass
        self.root.after(120, self._process_events)

    def _handle_event(self, event_type: str, payload: Dict):
        if event_type == "message":
            message = payload["message"]
            if message.get("type") == MessageProtocol.TYPE_ROOM_JOIN:
                self.room_var.set(message.get("room", self.room_var.get()))
            self._append_text(MessageProtocol.format_display_message(message))
        elif event_type == "status":
            self.status_var.set(payload["text"])
        elif event_type == "buffered":
            self.buffer_var.set(f"Buffered: {payload['count']}")
        elif event_type == "rooms_updated":
            rooms = payload.get("rooms", [])
            if rooms:
                self._append_text(f"[SYSTEM]: Rooms available: {', '.join(rooms)}")
        elif event_type == "users_updated":
            self.user_list.delete(0, self.tk.END)
            current_username = self.username_var.get().strip()
            for username in payload.get("users", []):
                if username != current_username:
                    self.user_list.insert(self.tk.END, username)
        elif event_type == "file_received":
            self._append_text(f"[SYSTEM]: File saved to {payload['path']}")

    def _append_text(self, text: str):
        self.chat_output.configure(state="normal")
        self.chat_output.insert(self.tk.END, text + "\n")
        self.chat_output.see(self.tk.END)
        self.chat_output.configure(state="disabled")

    def _on_close(self):
        if self.client:
            self.client.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    import sys

    host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5555
    username = sys.argv[3] if len(sys.argv) > 3 else None

    cli_client = TerminalChatClient(host, port, username)
    cli_client.start()
