from typing import Dict, Any

from config import config
from cpu_monitor import cpu_monitor
from ram_monitor import ram_monitor
from response_monitor import response_monitor
from error_monitor import error_monitor
from network_monitor import network_monitor
from request_monitor import request_monitor


class HealthEngine:
    def calculate_overall_health(self) -> Dict[str, Any]:
        cpu_score = cpu_monitor.calculate_health_score()
        ram_score = ram_monitor.calculate_health_score()
        response_score = response_monitor.calculate_health_score()
        error_score = error_monitor.calculate_health_score()
        network_score = network_monitor.calculate_health_score()
        request_score = request_monitor.calculate_health_score()

        weighted_score = (
            cpu_score * config.weights["cpu"]
            + ram_score * config.weights["ram"]
            + response_score * config.weights["response_time"]
            + error_score * config.weights["error_rate"]
            + network_score * config.weights["network"]
            + request_score * config.weights["request_rate"]
        )

        health_score = max(0.0, min(100.0, round(weighted_score)))

        if health_score < config.status_bands["critical"]:
            status = "CRITICAL"
        elif health_score < config.status_bands["high_load"]:
            status = "HIGH_LOAD"
        elif health_score < config.status_bands["warning"]:
            status = "WARNING"
        elif health_score < config.status_bands["good"]:
            status = "GOOD"
        else:
            status = "EXCELLENT"

        return {
            "healthScore": health_score,
            "status": status,
            "breakdown": {
                "cpuScore": cpu_score,
                "ramScore": ram_score,
                "responseScore": response_score,
                "errorScore": error_score,
                "networkScore": network_score,
                "requestScore": request_score,
            },
        }


health_engine = HealthEngine()
