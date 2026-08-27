def make_decision(cpu_percent, ram_percent, eta_seconds):
    """
    Decide the server action based on current metrics.
    """

    if cpu_percent >= 90 or ram_percent >= 90:
        return "CRITICAL"

    if cpu_percent >= 75 or ram_percent >= 75:
        return "WARNING"

    if eta_seconds >= 300:
        return "DELAY"

    return "NORMAL"