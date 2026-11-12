"""Does an accept's id (uuid4 -> os.urandom) release the GIL and stall behind a busy thread?"""
import os, sys, threading, time, uuid


def q(values):
    ordered = sorted(values)
    at = lambda f: round(ordered[min(len(ordered) - 1, int(f * len(ordered)))], 4)
    return {"p50": at(0.5), "p95": at(0.95), "p99": at(0.99), "max": round(ordered[-1], 4)}


def main():
    counter = iter(range(10**9))
    work = {"uuid4": lambda: uuid.uuid4(), "urandom16": lambda: os.urandom(16),
            "counter": lambda: next(counter), "uuid5": lambda: uuid.uuid5(uuid.NAMESPACE_URL, "prme:fact"),
            "perf": lambda: time.perf_counter(), "datetime": lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc)}
    results = {}
    for round_ in (1, 2):
        for name, fn in work.items():
            values = []
            for _ in range(2000):
                t = time.perf_counter(); fn(); values.append(1000 * (time.perf_counter() - t))
            results[f"{name}_idle_{round_}"] = q(values)
        stop = threading.Event()

        def spin():
            while not stop.is_set():
                sum(range(500))

        thread = threading.Thread(target=spin, daemon=True); thread.start()
        time.sleep(0.2)
        for name, fn in work.items():
            values = []
            for _ in range(2000):
                t = time.perf_counter(); fn(); values.append(1000 * (time.perf_counter() - t))
            results[f"{name}_busy_{round_}"] = q(values)
        stop.set(); thread.join()
    print("switchinterval", sys.getswitchinterval())
    for key in sorted(results):
        print(key, results[key], flush=True)


if __name__ == "__main__":
    main()
