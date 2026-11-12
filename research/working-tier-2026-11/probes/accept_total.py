"""The whole accept, pure Python, on the caller's thread while a drainer-like thread keeps Python busy."""
import os, statistics, sys, threading, time


def q(values):
    ordered = sorted(values)
    at = lambda f: round(ordered[min(len(ordered) - 1, int(f * len(ordered)))], 4)
    return {"p50": at(0.5), "p95": at(0.95), "p99": at(0.99), "max": round(ordered[-1], 4)}


def main():
    sys.path.insert(0, os.path.expanduser("~/Sites/prism-working-tier"))
    os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked",
                       "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9"})
    from prme.ingestion.assertions import checked_assertions, prepare_assertions
    from prme.models.events import Event
    from prme.storage.metadata import snapshot_metadata

    row = {"subject": "red mug", "subject_type": "product", "relation": "located_in",
           "object": "kitchen shelf", "object_type": "location", "status": "current"}
    content = "The red mug is on the kitchen shelf."
    table = {}
    parts = {}

    def event_only():
        Event(content=content, user_id="robot-1", role="system", metadata=None)

    def validate_only():
        prepare_assertions(content, checked_assertions([row]), speaker=None)

    def accept():
        t = time.perf_counter()
        _, prepared = prepare_assertions(content, checked_assertions([row]), speaker=None)
        metadata = snapshot_metadata({"prme_assertions_v1": {"version": 1, "items": prepared}})
        event = Event(content=content, user_id="robot-1", role="system", metadata=metadata)
        address = (prepared[0]["subject"].casefold(), prepared[0]["subject_type"],
                   prepared[0]["relation"])
        table[("robot-1", "personal") + address] = event
        parts["accept"] = parts.get("accept", 0) + (time.perf_counter() - t)

    work = {"event_only": event_only, "validate_only": validate_only, "accept": accept}
    results = {}
    for name, fn in work.items():
        values = []
        for _ in range(3000):
            t = time.perf_counter(); fn(); values.append(1000 * (time.perf_counter() - t))
        results[f"{name}_idle"] = q(values)
    stop = threading.Event()

    def spin():
        while not stop.is_set():
            sum(range(500))

    thread = threading.Thread(target=spin, daemon=True); thread.start()
    time.sleep(0.2)
    for name, fn in work.items():
        values = []
        for _ in range(3000):
            t = time.perf_counter(); fn(); values.append(1000 * (time.perf_counter() - t))
        results[f"{name}_busy"] = q(values)
    stop.set(); thread.join()
    for key in sorted(results):
        print(key, results[key], flush=True)


if __name__ == "__main__":
    main()
