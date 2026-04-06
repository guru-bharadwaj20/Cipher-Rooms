#!/usr/bin/env python3
"""
Client Entry Point

Run this script to start a Pulse-Chat client and connect to the server.

Usage:
    python run_client.py [host] [port] [username]

Examples:
    python run_client.py                        # Default: localhost:5555
    python run_client.py localhost 8080         # Custom host and port
    python run_client.py localhost 5555 Alice   # With username
"""

import os
import subprocess
import sys

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from client.chat_client import ChatClientApp, TerminalChatClient


def can_launch_gui() -> tuple[bool, str]:
    """Probe Tk in a child process so a native Tk crash cannot kill the main client."""
    probe_code = (
        "import tkinter as tk; "
        "root = tk.Tk(); "
        "root.withdraw(); "
        "root.update_idletasks(); "
        "root.destroy()"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe_code],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True, ""

    error_text = (result.stderr or result.stdout).strip()
    if not error_text:
        error_text = "Tkinter could not start in this terminal session."
    return False, error_text


def main():
    """Start a chat client with optional command-line arguments."""
    
    # Parse command-line arguments
    cli_mode = "--cli" in sys.argv
    args = [arg for arg in sys.argv[1:] if arg != "--cli"]
    host = args[0] if len(args) > 0 else 'localhost'
    port = int(args[1]) if len(args) > 1 else 5555
    username = args[2] if len(args) > 2 else None
    
    # Display banner
    print("=" * 70)
    print("PULSE-CHAT CLIENT")
    print("=" * 70)
    print()
    
    if cli_mode:
        client = TerminalChatClient(
            server_host=host,
            server_port=port,
            username=username
        )
        try:
            client.start()
        except KeyboardInterrupt:
            print("\nDisconnecting...")
    else:
        gui_ok, gui_error = can_launch_gui()
        if gui_ok:
            app = ChatClientApp(server_host=host, server_port=port)
            if username:
                app.username_var.set(username)
            app.run()
            return

        print(f"[CLIENT] GUI unavailable: {gui_error}")
        print("[CLIENT] Falling back to terminal mode.")
        try:
            client = TerminalChatClient(
                server_host=host,
                server_port=port,
                username=username
            )
            try:
                client.start()
            except KeyboardInterrupt:
                print("\nDisconnecting...")
        except EOFError:
            print("[CLIENT] No interactive input available for terminal mode.")
        except Exception as exc:
            print(f"[CLIENT] Terminal client failed: {exc}")


if __name__ == "__main__":
    main()
