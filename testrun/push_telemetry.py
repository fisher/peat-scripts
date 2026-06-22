#!/usr/bin/env python3
import asyncio
import json
import socket
import sys
from datetime import datetime, UTC
from pathlib import Path

import httpx
import yaml


class PeatSidecarClient:
    def __init__(self, config_path: str):
        """Initialize client with YAML config file."""
        self.config = self._load_config(config_path)
        self.base_url = f"http://{self.config['extip']}:{self.config['tcpp']}"
        self.client = httpx.Client(timeout=30.0)

    def _load_config(self, config_path: str) -> dict:
        """Load and parse YAML configuration."""
        config_file = Path(config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)

        # Validate required keys
        required = ['extip', 'tcpp']
        missing = [key for key in required if key not in config]
        if missing:
            raise ValueError(f"Missing required config keys: {', '.join(missing)}")

        return config

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

        except httpx.HTTPStatusError as e:
            print(f"ERROR: {url} returned HTTP {e.response.status_code}", file=sys.stderr)
            print(f"  body: {e.response.text}", file=sys.stderr)
            raise
        except httpx.RequestError as e:
            print(f"ERROR: Request failed: {e}", file=sys.stderr)
            raise

    def get_free_pages(self) -> int:
        """Read NR_FP from /proc/vmstat."""
        try:
            with open('/proc/vmstat', 'r') as f:
                first_line = f.readline().strip()
                # Format: "nr_free_pages 12345"
                parts = first_line.split()
                if len(parts) >= 2:
                    return int(parts[1])
                else:
                    raise ValueError(f"Unexpected format: {first_line}")
        except FileNotFoundError:
            # Fallback for non-Linux systems (testing)
            print("WARNING: /proc/vmstat not found, using fallback value", file=sys.stderr)
            return 0

    def get_timestamp(self) -> str:
        """Get current UTC timestamp with nanoseconds (matching shell script format)."""
        now = datetime.now(UTC)
        # Format: YY-MM-DD HH:MM:SS nanoseconds (9 digits)
        ns = f"{now.microsecond:06d}000"  # Convert microseconds to nanoseconds
        return now.strftime(f"%g-%m-%d %H:%M:%S {ns}")

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
        self.call("PutDocument", request_payload)
        print(f"  wrote: {json.dumps(payload)}")

    def close(self):
        """Close the HTTP client."""
        self.client.close()


async def main():
    # Configuration
    config_path = "push_telemetry.yaml"
    collection = "telemetry"
    hostname = socket.gethostname()
    doc_id = f"stat-{hostname}"

    # Initialize client
    try:
        client = PeatSidecarClient(config_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Main loop
    try:
        while True:
            # Gather data
            free_pages = client.get_free_pages()
            timestamp = client.get_timestamp()

            # Build payload
            payload = {
                "name": hostname,
                "free_pages": str(free_pages),
                "time_hr": timestamp
            }

            # Send document
            client.put_document(collection, doc_id, payload)

            # Sleep for 1 second
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        print("\nShutting down...", file=sys.stderr)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
