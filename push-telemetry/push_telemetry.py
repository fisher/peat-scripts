#!/usr/bin/env python3
"""
Push json with telemetry to the peat-node instance

Meant to work alongside the peat-node on the same machine.
"""
import argparse
import asyncio
import json
import socket
import signal
import time
import sys
from datetime import datetime, UTC
from pathlib import Path

import httpx
import yaml

def signal_handler(_sig, _frame):
    """just a signal handler to catch sigterm and gracefully exit"""
    print("\nReceived signal, shutting down...", file=sys.stderr)
    sys.exit(0)

class PeatSidecarClient:
    """    main class to ping the peat-node    """
    def __init__(self, config_path: str):
        """Initialize client with YAML config file."""
        self.config = self._load_config(config_path)
        self.host = self.config['host']
        self.port = self.config['port']
        self.base_url = f"http://{self.config['host']}:{self.config['port']}"
        self.client = httpx.Client(timeout=30.0)

    def _load_config(self, config_path: str) -> dict:
        """Load and parse YAML configuration."""
        config_file = Path(config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_file, 'r', encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # Validate required keys
        required = ['host', 'port']
        missing = [key for key in required if key not in config]
        if missing:
            raise ValueError(f"Missing required config keys: {', '.join(missing)}")

        return config

    def is_port_open(self) -> bool:
        """Check if the TCP port is open and accepting connections."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((self.host, int(self.port)))
            sock.close()
            return result == 0
        except Exception:
            return False

    def wait_for_port(self, check_interval: float = 2.0) -> None:
        """Wait until the TCP port becomes open."""
        print(f"Waiting for {self.host}:{self.port} to become available...",
              file=sys.stderr)
        while not self.is_port_open():
            print(f"  Port {self.host}:{self.port} not open yet, "
                  "retrying in {check_interval}s...", file=sys.stderr)
            time.sleep(check_interval)
        print(f"Port {self.host}:{self.port} is now open!", file=sys.stderr)

    def call(self, method: str, payload: dict) -> dict:
        """
        Call the PeatSidecar API.

        Args:
            method: API method name (e.g., 'PutDocument')
            payload: Request payload as dict

        Returns:
            Response JSON as dict

        Raises:
            httpx.HTTPError: On HTTP errors
        """
        url = f"{self.base_url}/peat.sidecar.v1.PeatSidecar/{method}"

        try:
            response = self.client.post(
                url,
                json=payload,
                headers={'Content-Type': 'application/json'}
            )
            response.raise_for_status()
            return response.json()

        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            print(f"Connection error: {e}", file=sys.stderr)
            raise ConnectionError(f"Fail to connect to {self.host}:{self.port}") from e
        except httpx.HTTPStatusError as e:
            print(f"ERROR: {url} returned HTTP {e.response.status_code}",
                  file=sys.stderr)
            print(f"  body: {e.response.text}", file=sys.stderr)
            raise
        except httpx.RequestError as e:
            print(f"ERROR: Request failed: {e}", file=sys.stderr)
            raise

    def call_with_retry(self, method: str, payload: dict, max_retries: int = 3) -> dict:
        """Call the API with automatic retry and reconnection logic."""
        retry_count = 0

        while True:
            try:
                return self.call(method, payload)
            except ConnectionError:
                retry_count += 1
                if retry_count < max_retries:
                    print(f"  Connection failed, retry {retry_count}/{max_retries}...",
                          file=sys.stderr)
                    time.sleep(1)
                else:
                    print(f"  Connection failed after {max_retries} retries,"
                          " waiting for port...",
                          file=sys.stderr)
                    self.wait_for_port()
                    retry_count = 0  # Reset after port reopens
            except Exception:
                raise

    def get_free_pages(self) -> int:
        """Read NR_FP from /proc/vmstat."""
        try:
            with open('/proc/vmstat', 'r', encoding="utf-8") as f:
                first_line = f.readline().strip()
                # Format: "nr_free_pages 12345"
                parts = first_line.split()
                if len(parts) >= 2:
                    return int(parts[1])
                raise ValueError(f"Unexpected format: {first_line}")
        except FileNotFoundError:
            # Fallback for non-Linux systems (testing)
            print("WARNING: /proc/vmstat not found, using fallback value", file=sys.stderr)
            return 0

    def get_timestamp(self) -> str:
        """Get current UTC timestamp with nanoseconds (matching shell script format)."""
        now = datetime.now(UTC)
        # Format: YY-MM-DD HH:MM:SS microseconds (6 digits)
        ms = f"{now.microsecond:06d}"
        return now.strftime(f"%g-%m-%d %H:%M:%S {ms}")

    def put_document(self, collection: str, doc_id: str, payload: dict) -> None:
        """
        Put a document into the collection.

        Args:
            collection: Collection name
            doc_id: Document ID
            payload: Document data as dict
        """
        request_payload = {
            "collection": collection,
            "docId": doc_id,
            "jsonData": json.dumps(payload)  # stringify JSON
        }

        print(f"PutDocument {collection}/{doc_id}")
        self.call_with_retry("PutDocument", request_payload)
        print(f"  wrote: {json.dumps(payload)}")

    def close(self):
        """Close the HTTP client."""
        self.client.close()


async def main():
    """entry point"""

    # Configuration
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="push_telemetry.yaml")
    args = parser.parse_args()

    config_path = args.config
    collection = "telemetry"
    hostname = socket.gethostname()
    doc_id = f"stat-{hostname}"

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)  # This handles Ctrl-C too

    # Initialize client
    try:
        client = PeatSidecarClient(config_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # WAit for the port to be open before starting
    client.wait_for_port()

    # Main loop
    try:
        while True:
            try:
                # Gather data
                free_pages = client.get_free_pages()
                timestamp = client.get_timestamp()

                # Build payload
                payload = {
                    "name": hostname,
                    "free_pages": free_pages,
                    "time_utc": timestamp
                }

                # Send document
                client.put_document(collection, doc_id, payload)

                # Sleep for 1 second
                await asyncio.sleep(1)
            except Exception as e:
                print(f"Unexpected error in main loop: {e}", file=sys.stderr)
                await asyncio.sleep(2)

    except asyncio.exceptions.CancelledError:
        print("\n(C-c) Shutting down...", file=sys.stderr)
    except KeyboardInterrupt:
        print("\n(Keyboard) Shutting down...", file=sys.stderr)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
