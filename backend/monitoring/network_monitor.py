from typing import Dict


class NetworkMonitor:
    def __init__(self):
        self.bytes_rx = 0
        self.bytes_tx = 0

    def record_traffic(
        self,
        incoming_bytes: int = 0,
        outgoing_bytes: int = 0,
    ) -> None:
        self.bytes_rx += max(0, incoming_bytes or 0)
        self.bytes_tx += max(0, outgoing_bytes or 0)

    def get_metrics(self) -> Dict[str, int]:
        return {
            "bytesRx": self.bytes_rx,
            "bytesTx": self.bytes_tx,
        }

    def calculate_health_score(self) -> float:
        total_mb = (self.bytes_rx + self.bytes_tx) / (1024.0 * 1024.0)

        if total_mb < 50:
            return 100.0
        if total_mb < 200:
            return 85.0
        if total_mb < 500:
            return 70.0
        return 50.0


network_monitor = NetworkMonitor()
