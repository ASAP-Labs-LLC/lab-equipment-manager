import threading
import logging
import time

LOG = logging.getLogger(__name__)

class ProcessManager:
    """Manages background tasks."""

    def __init__(self):
        self.stop_event = threading.Event()
        self.threads = []

    def start_task(self, target, *args, **kwargs):
        t = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        t.start()
        self.threads.append(t)

    def stop_all(self):
        self.stop_event.set()
        for t in self.threads:
            t.join(timeout=5)
        LOG.info("All background processes stopped.")
