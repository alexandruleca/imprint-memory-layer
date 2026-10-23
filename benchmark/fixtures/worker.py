"""Synthetic reference project for controlled cross-project comparisons."""
import os
from queue import Queue
from threading import Thread

def setting(key, persisted, defaults):
    return os.environ.get('REFERENCE_' + key.upper(), persisted.get(key, defaults[key]))

def start_worker(execute):
    jobs = Queue()
    def work():
        while True:
            payload = jobs.get()
            try:
                execute(payload)
            finally:
                jobs.task_done()
    Thread(target=work, daemon=True).start()
    return jobs
