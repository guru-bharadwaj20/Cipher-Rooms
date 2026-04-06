#!/usr/bin/env python3
"""
Pulse-Chat performance benchmark harness.

Runs synthetic load tests for:
- Room chat
- Private messaging
- File transfer

Collects and writes metrics:
- Latency (avg, p95)
- Throughput (messages/sec)
- Error rate
- Reconnect time (avg, success rate)

Outputs:
- reports/performance_metrics.csv
- reports/performance_summary.md
"""

import argparse
import base64
import csv
import hashlib
import json
import os
import socket
import ssl
import statistics
import sys
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple


def now_ns() -> int:
    return time.perf_counter_ns()


def ms_between(start_ns: int, end_ns: int) -> float:
    return (end_ns - start_ns) / 1_000_000.0


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    values_sorted = sorted(values)
    idx = (len(values_sorted) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(values_sorted) - 1)
    frac = idx - lo
    return values_sorted[lo] * (1.0 - frac) + values_sorted[hi] * frac


class SyntheticClient:
    def __init__(self, username: str, host: str, port: int):
        self.username = username
        self.host = host
        self.port = port

        self.sock: Optional[ssl.SSLSocket] = None
        self.reader = None
        self.running = False
        self.listen_thread: Optional[threading.Thread] = None

        self.lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.connect_failures = 0
        self.disconnects = 0
        self.errors = 0

        self.room_latencies: Dict[str, List[float]] = {}
        self.dm_latencies: Dict[str, List[float]] = {}
        self.file_ack_events: Dict[str, Tuple[threading.Event, Optional[float]]] = {}

        self.file_incoming: Dict[str, Dict] = {}

    def _build_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def connect(self) -> bool:
        base = None
        try:
            base = socket.create_connection((self.host, self.port), timeout=5)
            self.sock = self._build_ssl_context().wrap_socket(base, server_hostname=self.host)
            self.reader = self.sock.makefile("rb")
            self.running = True

            if not self.safe_send({
                "type": "join",
                "username": self.username,
                "content": f"{self.username} joined",
                "timestamp": datetime.now().isoformat()
            }):
                return False

            self.listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
            self.listen_thread.start()
            return True
        except Exception:
            self.running = False
            try:
                if self.reader:
                    self.reader.close()
            except Exception:
                pass
            self.reader = None
            try:
                if self.sock:
                    self.sock.close()
            except Exception:
                pass
            self.sock = None
            try:
                if base:
                    base.close()
            except Exception:
                pass
            with self.lock:
                self.connect_failures += 1
            return False

    def disconnect(self):
        self.running = False
        reader = self.reader
        sock = self.sock
        self.reader = None
        self.sock = None

        try:
            if reader:
                reader.close()
        except Exception:
            pass

        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

    def reconnect(self) -> Optional[float]:
        start = now_ns()
        self.disconnect()
        ok = self.connect()
        if not ok:
            return None
        end = now_ns()
        return ms_between(start, end)

    def send(self, message: dict):
        data = (json.dumps(message) + "\n").encode("utf-8")
        with self.send_lock:
            sock = self.sock
            if not sock:
                raise ConnectionError("Client socket not connected")
            sock.sendall(data)

    def safe_send(self, message: dict) -> bool:
        try:
            self.send(message)
            return True
        except (ConnectionError, ConnectionResetError, BrokenPipeError, OSError, ssl.SSLError):
            with self.lock:
                self.errors += 1
            self.disconnect()
            return False

    def _listen_loop(self):
        while self.running:
            try:
                if not self.reader:
                    break
                line = self.reader.readline()
                if not line:
                    if self.running:
                        with self.lock:
                            self.disconnects += 1
                    break
                message = json.loads(line.decode("utf-8").strip())
                self._handle_message(message)
            except (ssl.SSLError, OSError):
                if self.running:
                    with self.lock:
                        self.errors += 1
                break
            except Exception:
                if self.running:
                    with self.lock:
                        self.errors += 1
                    break

    def _handle_message(self, message: dict):
        msg_type = message.get("type")
        content = str(message.get("content", ""))

        if msg_type == "chat" and content.startswith("BENCHROOM|"):
            # BENCHROOM|<scenario_id>|<sent_ns>|<payload>
            parts = content.split("|", 3)
            if len(parts) >= 3:
                scenario_id = parts[1]
                try:
                    sent_ns = int(parts[2])
                except ValueError:
                    return
                latency = ms_between(sent_ns, now_ns())
                with self.lock:
                    self.room_latencies.setdefault(scenario_id, []).append(latency)
            return

        if msg_type == "private" and content.startswith("BENCHDM|"):
            # BENCHDM|<scenario_id>|<sent_ns>|<payload>
            parts = content.split("|", 3)
            if len(parts) >= 3:
                scenario_id = parts[1]
                try:
                    sent_ns = int(parts[2])
                except ValueError:
                    return
                latency = ms_between(sent_ns, now_ns())
                with self.lock:
                    self.dm_latencies.setdefault(scenario_id, []).append(latency)
            return

        if msg_type == "file_offer":
            self._handle_file_offer(message)
            return

        if msg_type == "file_chunk":
            self._handle_file_chunk(message)
            return

        if msg_type == "file_end":
            self._handle_file_end(message)
            return

        if msg_type == "file_ack":
            transfer_id = message.get("transfer_id", "")
            with self.lock:
                event_data = self.file_ack_events.get(transfer_id)
                if event_data:
                    ev, _ = event_data
                    self.file_ack_events[transfer_id] = (ev, ms_between(int(message.get("sent_ns", now_ns())), now_ns()))
                    ev.set()
            return

        if msg_type in {"error", "file_error"}:
            with self.lock:
                self.errors += 1

    def _handle_file_offer(self, message: dict):
        transfer_id = message.get("transfer_id")
        if not transfer_id:
            return
        self.file_incoming[transfer_id] = {
            "from": message.get("username", ""),
            "size": int(message.get("size", 0)),
            "checksum": message.get("checksum", ""),
            "buffer": bytearray(),
        }

    def _handle_file_chunk(self, message: dict):
        transfer_id = message.get("transfer_id")
        if not transfer_id:
            return
        state = self.file_incoming.get(transfer_id)
        if not state:
            return
        chunk_data = message.get("chunk_data", "")
        try:
            state["buffer"].extend(base64.b64decode(chunk_data.encode("ascii")))
        except Exception:
            self.safe_send(
                {
                    "type": "file_error",
                    "username": self.username,
                    "content": "chunk decode failed",
                    "transfer_id": transfer_id,
                    "to_username": state.get("from", ""),
                    "timestamp": datetime.now().isoformat(),
                }
            )

    def _handle_file_end(self, message: dict):
        transfer_id = message.get("transfer_id")
        if not transfer_id:
            return
        state = self.file_incoming.pop(transfer_id, None)
        if not state:
            return

        payload = bytes(state["buffer"])
        checksum = hashlib.sha256(payload).hexdigest()
        ok = checksum == state["checksum"] and len(payload) == state["size"]

        if ok:
            self.safe_send(
                {
                    "type": "file_ack",
                    "username": self.username,
                    "content": "receiver_verified",
                    "transfer_id": transfer_id,
                    "to_username": state.get("from", ""),
                    "sent_ns": now_ns(),
                    "timestamp": datetime.now().isoformat(),
                }
            )
        else:
            self.safe_send(
                {
                    "type": "file_error",
                    "username": self.username,
                    "content": "receiver_verification_failed",
                    "transfer_id": transfer_id,
                    "to_username": state.get("from", ""),
                    "timestamp": datetime.now().isoformat(),
                }
            )


class LoadBenchmark:
    def __init__(self, host: str, port: int, client_counts: List[int], output_csv: str, output_md: str):
        self.host = host
        self.port = port
        self.client_counts = client_counts
        self.output_csv = output_csv
        self.output_md = output_md
        self.rows: List[dict] = []

    def _probe_server(self) -> Tuple[bool, str]:
        """Quick TLS probe so we fail fast when server is not running."""
        try:
            base = socket.create_connection((self.host, self.port), timeout=2)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            secure = ctx.wrap_socket(base, server_hostname=self.host)
            try:
                secure.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            secure.close()
            return True, ""
        except Exception as e:
            return False, str(e)

    def run(self):
        ok, err = self._probe_server()
        if not ok:
            self.rows.append(
                {
                    "timestamp": datetime.now().isoformat(),
                    "scenario": "all",
                    "client_count": 0,
                    "messages_sent": 0,
                    "messages_received": 0,
                    "latency_avg_ms": 0.0,
                    "latency_p95_ms": 0.0,
                    "throughput_msgs_per_sec": 0.0,
                    "error_rate": 1.0,
                    "disconnect_rate": 1.0,
                    "reconnect_avg_ms": 0.0,
                    "reconnect_success_rate": 0.0,
                    "file_bytes_sent": 0,
                    "file_bytes_verified": 0,
                    "notes": f"server_unreachable: {err}",
                }
            )
            self._write_csv()
            self._write_markdown_summary()
            raise RuntimeError(
                f"Could not connect to server at {self.host}:{self.port}. "
                "Start run_server.py first and retry."
            )

        for count in self.client_counts:
            self._run_for_client_count(count)

        self._write_csv()
        self._write_markdown_summary()

    def _run_for_client_count(self, count: int):
        scenario_stamp = int(time.time() * 1000)
        clients = [SyntheticClient(f"bench_{scenario_stamp}_{i}", self.host, self.port) for i in range(count)]
        try:
            start_connect_ns = now_ns()
            connected = 0
            for c in clients:
                if c.connect():
                    connected += 1
            connect_ms = ms_between(start_connect_ns, now_ns())

            if connected == 0:
                self.rows.append(
                    {
                        "timestamp": datetime.now().isoformat(),
                        "scenario": "all",
                        "client_count": count,
                        "messages_sent": 0,
                        "messages_received": 0,
                        "latency_avg_ms": 0.0,
                        "latency_p95_ms": 0.0,
                        "throughput_msgs_per_sec": 0.0,
                        "error_rate": 1.0,
                        "disconnect_rate": 1.0,
                        "reconnect_avg_ms": 0.0,
                        "reconnect_success_rate": 0.0,
                        "file_bytes_sent": 0,
                        "file_bytes_verified": 0,
                        "notes": "all client connections failed",
                    }
                )
                return

            time.sleep(0.8)

            room_metrics = self._run_room_chat(clients, count)
            dm_metrics = self._run_dm(clients, count)
            file_metrics = self._run_file_transfer(clients, count)
            reconnect_metrics = self._run_reconnect(clients)

            total_errors = sum(c.errors for c in clients)
            total_disconnects = sum(c.disconnects for c in clients)
            total_ops = max(1, room_metrics["messages_sent"] + dm_metrics["messages_sent"] + file_metrics["messages_sent"])

            for scenario_name, metrics in [
                ("room_chat", room_metrics),
                ("dm", dm_metrics),
                ("file", file_metrics),
            ]:
                self.rows.append(
                    {
                        "timestamp": datetime.now().isoformat(),
                        "scenario": scenario_name,
                        "client_count": count,
                        "messages_sent": metrics["messages_sent"],
                        "messages_received": metrics["messages_received"],
                        "latency_avg_ms": round(metrics["latency_avg_ms"], 3),
                        "latency_p95_ms": round(metrics["latency_p95_ms"], 3),
                        "throughput_msgs_per_sec": round(metrics["throughput_msgs_per_sec"], 3),
                        "error_rate": round(total_errors / total_ops, 6),
                        "disconnect_rate": round(total_disconnects / max(1, connected), 6),
                        "reconnect_avg_ms": round(reconnect_metrics["reconnect_avg_ms"], 3),
                        "reconnect_success_rate": round(reconnect_metrics["reconnect_success_rate"], 6),
                        "file_bytes_sent": file_metrics["file_bytes_sent"],
                        "file_bytes_verified": file_metrics["file_bytes_verified"],
                        "notes": f"connect_ms={round(connect_ms, 2)}",
                    }
                )
        finally:
            for c in clients:
                c.disconnect()

    def _run_room_chat(self, clients: List[SyntheticClient], count: int) -> dict:
        scenario_id = f"room_{count}_{int(time.time() * 1000)}"
        room_name = f"bench_room_{count}"

        for c in clients:
            c.safe_send(
                {
                    "type": "room_join",
                    "username": c.username,
                    "content": room_name,
                    "room_name": room_name,
                    "timestamp": datetime.now().isoformat(),
                }
            )

        time.sleep(0.6)

        messages_per_sender = 8
        sender = next((c for c in clients if c.sock), None)
        if not sender:
            return {
                "messages_sent": 0,
                "messages_received": 0,
                "latency_avg_ms": 0.0,
                "latency_p95_ms": 0.0,
                "throughput_msgs_per_sec": 0.0,
                "file_bytes_sent": 0,
                "file_bytes_verified": 0,
            }
        sent_count = 0
        started_ns = now_ns()

        for i in range(messages_per_sender):
            msg = {
                "type": "chat",
                "username": sender.username,
                "content": f"BENCHROOM|{scenario_id}|{now_ns()}|msg_{i}",
                "room_name": room_name,
                "timestamp": datetime.now().isoformat(),
            }
            if not sender.safe_send(msg):
                break
            sent_count += 1

        deadline = time.time() + 8
        expected_min = max(1, (count - 1) * messages_per_sender)
        while time.time() < deadline:
            received = sum(len(c.room_latencies.get(scenario_id, [])) for c in clients)
            if received >= expected_min:
                break
            time.sleep(0.05)

        elapsed_s = max(0.001, (now_ns() - started_ns) / 1_000_000_000.0)
        latencies = []
        for c in clients:
            latencies.extend(c.room_latencies.get(scenario_id, []))

        return {
            "messages_sent": sent_count,
            "messages_received": len(latencies),
            "latency_avg_ms": statistics.mean(latencies) if latencies else 0.0,
            "latency_p95_ms": percentile(latencies, 0.95),
            "throughput_msgs_per_sec": len(latencies) / elapsed_s,
            "file_bytes_sent": 0,
            "file_bytes_verified": 0,
        }

    def _run_dm(self, clients: List[SyntheticClient], count: int) -> dict:
        scenario_id = f"dm_{count}_{int(time.time() * 1000)}"
        pairs = count // 2
        messages_per_pair = 6

        sent_count = 0
        started_ns = now_ns()

        for p in range(pairs):
            sender = clients[2 * p]
            receiver = clients[2 * p + 1]
            if not sender.sock or not receiver.sock:
                continue
            for i in range(messages_per_pair):
                if not sender.safe_send(
                    {
                        "type": "private",
                        "username": sender.username,
                        "to_username": receiver.username,
                        "message_id": str(uuid.uuid4()),
                        "content": f"BENCHDM|{scenario_id}|{now_ns()}|pair_{p}_msg_{i}",
                        "timestamp": datetime.now().isoformat(),
                    }
                ):
                    break
                sent_count += 1

        deadline = time.time() + 8
        expected_min = max(1, pairs * messages_per_pair)
        while time.time() < deadline:
            received = sum(len(c.dm_latencies.get(scenario_id, [])) for c in clients)
            if received >= expected_min:
                break
            time.sleep(0.05)

        elapsed_s = max(0.001, (now_ns() - started_ns) / 1_000_000_000.0)
        latencies = []
        for c in clients:
            latencies.extend(c.dm_latencies.get(scenario_id, []))

        return {
            "messages_sent": sent_count,
            "messages_received": len(latencies),
            "latency_avg_ms": statistics.mean(latencies) if latencies else 0.0,
            "latency_p95_ms": percentile(latencies, 0.95),
            "throughput_msgs_per_sec": len(latencies) / elapsed_s,
            "file_bytes_sent": 0,
            "file_bytes_verified": 0,
        }

    def _run_file_transfer(self, clients: List[SyntheticClient], count: int) -> dict:
        if count < 2:
            return {
                "messages_sent": 0,
                "messages_received": 0,
                "latency_avg_ms": 0.0,
                "latency_p95_ms": 0.0,
                "throughput_msgs_per_sec": 0.0,
                "file_bytes_sent": 0,
                "file_bytes_verified": 0,
            }

        sender = clients[0]
        receiver = clients[1]
        if not sender.sock or not receiver.sock:
            return {
                "messages_sent": 0,
                "messages_received": 0,
                "latency_avg_ms": 0.0,
                "latency_p95_ms": 0.0,
                "throughput_msgs_per_sec": 0.0,
                "file_bytes_sent": 0,
                "file_bytes_verified": 0,
            }
        sizes = [64 * 1024, 512 * 1024]

        file_latencies = []
        message_count = 0
        total_bytes = 0
        verified_bytes = 0
        started_ns = now_ns()

        for size in sizes:
            payload = os.urandom(size)
            transfer_id = str(uuid.uuid4())
            checksum = hashlib.sha256(payload).hexdigest()
            chunk_size = 16 * 1024
            chunks = [payload[i:i + chunk_size] for i in range(0, len(payload), chunk_size)]

            ack_event = threading.Event()
            with sender.lock:
                sender.file_ack_events[transfer_id] = (ack_event, None)

            send_start = now_ns()
            if not sender.safe_send(
                {
                    "type": "file_offer",
                    "username": sender.username,
                    "to_username": receiver.username,
                    "transfer_id": transfer_id,
                    "filename": f"bench_{size}.bin",
                    "size": size,
                    "checksum": checksum,
                    "total_chunks": len(chunks),
                    "content": "bench file offer",
                    "timestamp": datetime.now().isoformat(),
                }
            ):
                continue
            message_count += 1

            for idx, chunk in enumerate(chunks):
                if not sender.safe_send(
                    {
                        "type": "file_chunk",
                        "username": sender.username,
                        "to_username": receiver.username,
                        "transfer_id": transfer_id,
                        "chunk_index": idx,
                        "total_chunks": len(chunks),
                        "chunk_data": base64.b64encode(chunk).decode("ascii"),
                        "content": f"chunk_{idx}",
                        "timestamp": datetime.now().isoformat(),
                    }
                ):
                    break
                message_count += 1
            else:
                if sender.safe_send(
                    {
                        "type": "file_end",
                        "username": sender.username,
                        "to_username": receiver.username,
                        "transfer_id": transfer_id,
                        "content": "file end",
                        "timestamp": datetime.now().isoformat(),
                    }
                ):
                    message_count += 1
                else:
                    continue

            if ack_event.wait(timeout=12):
                file_latencies.append(ms_between(send_start, now_ns()))
                verified_bytes += size
            total_bytes += size

        elapsed_s = max(0.001, (now_ns() - started_ns) / 1_000_000_000.0)

        return {
            "messages_sent": message_count,
            "messages_received": len(file_latencies),
            "latency_avg_ms": statistics.mean(file_latencies) if file_latencies else 0.0,
            "latency_p95_ms": percentile(file_latencies, 0.95),
            "throughput_msgs_per_sec": message_count / elapsed_s,
            "file_bytes_sent": total_bytes,
            "file_bytes_verified": verified_bytes,
        }

    def _run_reconnect(self, clients: List[SyntheticClient]) -> dict:
        reconnect_ms = []
        success = 0
        for c in clients:
            ms = c.reconnect()
            if ms is not None:
                reconnect_ms.append(ms)
                success += 1

        return {
            "reconnect_avg_ms": statistics.mean(reconnect_ms) if reconnect_ms else 0.0,
            "reconnect_success_rate": success / max(1, len(clients)),
        }

    def _write_csv(self):
        os.makedirs(os.path.dirname(self.output_csv), exist_ok=True)
        fields = [
            "timestamp",
            "scenario",
            "client_count",
            "messages_sent",
            "messages_received",
            "latency_avg_ms",
            "latency_p95_ms",
            "throughput_msgs_per_sec",
            "error_rate",
            "disconnect_rate",
            "reconnect_avg_ms",
            "reconnect_success_rate",
            "file_bytes_sent",
            "file_bytes_verified",
            "notes",
        ]
        with open(self.output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(self.rows)

    def _write_markdown_summary(self):
        os.makedirs(os.path.dirname(self.output_md), exist_ok=True)

        lines = []
        lines.append("# Performance Summary")
        lines.append("")
        lines.append(f"Generated at: {datetime.now().isoformat()}")
        lines.append("")
        lines.append("| Scenario | Clients | Avg Latency (ms) | P95 Latency (ms) | Throughput (msg/s) | Error Rate | Reconnect Avg (ms) |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")

        for row in self.rows:
            lines.append(
                "| {scenario} | {client_count} | {latency_avg_ms} | {latency_p95_ms} | {throughput_msgs_per_sec} | {error_rate} | {reconnect_avg_ms} |".format(
                    **row
                )
            )

        lines.append("")
        lines.append("## Bottleneck Hints")
        lines.append("")

        grouped: Dict[str, List[dict]] = {}
        for row in self.rows:
            grouped.setdefault(row["scenario"], []).append(row)

        for scenario, rows in grouped.items():
            rows_sorted = sorted(rows, key=lambda r: int(r["client_count"]))
            if len(rows_sorted) < 2:
                continue
            first = rows_sorted[0]
            last = rows_sorted[-1]
            lat_growth = float(last["latency_avg_ms"]) - float(first["latency_avg_ms"])
            thr_change = float(last["throughput_msgs_per_sec"]) - float(first["throughput_msgs_per_sec"])
            lines.append(
                f"- {scenario}: latency delta from {first['client_count']} to {last['client_count']} clients = {lat_growth:.3f} ms; throughput delta = {thr_change:.3f} msg/s."
            )

        lines.append("")
        lines.append("Use reports/performance_metrics.csv to create plots in Excel/Sheets (latency vs clients, throughput vs clients, error rate vs clients).")

        with open(self.output_md, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def parse_counts(text: str) -> List[int]:
    out = []
    for part in text.split(","):
        value = part.strip()
        if not value:
            continue
        out.append(int(value))
    return out or [5, 20, 50, 100]


def main():
    parser = argparse.ArgumentParser(description="Pulse-Chat load benchmark")
    parser.add_argument("--host", default="localhost", help="Server host")
    parser.add_argument("--port", type=int, default=5555, help="Server port")
    parser.add_argument(
        "--clients",
        default="5,20,50,100",
        help="Comma-separated client counts (e.g. 5,20,50,100)",
    )
    parser.add_argument(
        "--output-csv",
        default="reports/performance_metrics.csv",
        help="CSV output path",
    )
    parser.add_argument(
        "--output-md",
        default="reports/performance_summary.md",
        help="Markdown summary output path",
    )

    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)

    def resolve_output_path(path_value: str) -> str:
        if os.path.isabs(path_value):
            return path_value
        return os.path.join(project_root, path_value)

    benchmark = LoadBenchmark(
        host=args.host,
        port=args.port,
        client_counts=parse_counts(args.clients),
        output_csv=resolve_output_path(args.output_csv),
        output_md=resolve_output_path(args.output_md),
    )

    try:
        benchmark.run()
        print(f"[BENCH] Wrote CSV: {benchmark.output_csv}")
        print(f"[BENCH] Wrote summary: {benchmark.output_md}")
    except RuntimeError as e:
        print(f"[BENCH][ERROR] {e}")
        sys.exit(2)
    except KeyboardInterrupt:
        print("\n[BENCH] Benchmark interrupted by user.")
        sys.exit(130)


if __name__ == "__main__":
    main()
