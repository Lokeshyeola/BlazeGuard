def make_decision(cpu_percent, ram_percent, eta_seconds):
    """
    Decide what BlazeGuard should do based on server conditions.
    """

    # Critical condition → send request to queue
    if cpu_percent >= 90 or ram_percent >= 90 or eta_seconds > 10:
        return "QUEUE"

    # Warning condition → allow but monitor
    if cpu_percent >= 75 or ram_percent >= 75 or eta_seconds > 5:
        return "MONITOR"

    # Normal condition → process request
    return "PROCESS"