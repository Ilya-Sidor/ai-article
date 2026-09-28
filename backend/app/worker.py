"""Standalone job worker for production: ``python -m app.worker`` (set AI_ARTICLE_WORKER=external on the API)."""
import os
import threading

os.environ["AI_ARTICLE_WORKER"] = "external"  # the imported app must not start its own threads

from . import jobs  # noqa: E402
from .main import app  # noqa: E402


def main():
    jobs.recover()
    stop = threading.Event()
    threads = [threading.Thread(target=jobs._loop, args=(app, stop), daemon=True) for _ in range(jobs.WORKERS)]
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
