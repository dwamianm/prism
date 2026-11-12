"""Cost of the pieces a working-tier accept could do on the caller's thread.

Idle, and while another thread runs pure Python (the drainer's worst case). A working-log write is one
``os.write`` of the source row; a durable DuckDB append is the current path's transaction on a copy of the
20,500 pack; a dict put is the working table itself.
"""
import os, statistics, sys, threading, time

BLOCK = 0.0
STOP = threading.Event()


def spin():
    while not STOP.is_set():
        sum(range(500))


def q(values):
    ordered = sorted(values)
    at = lambda f: round(ordered[min(len(ordered) - 1, int(f * len(ordered)))], 4)
    return {"p50": at(0.5), "p95": at(0.95), "p99": at(0.99), "max": round(ordered[-1], 4)}


def sample(fn, count):
    out = []
    for _ in range(count):
        t = time.perf_counter(); fn(); out.append(1000 * (time.perf_counter() - t))
    return out


def main():
    sys.path.insert(0, os.path.expanduser("~/Sites/prism-working-tier"))
    os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked",
                       "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
                       "PRME_EXTRACTION_API_KEY": ""})
    sys.setswitchinterval(BLOCK or sys.getswitchinterval())

    from prme.ingestion.assertions import Assertion, checked_assertions, prepare_assertions

    row = {"subject": "red mug", "subject_type": "product", "relation": "located_in",
           "object": "kitchen shelf", "object_type": "location", "status": "current"}
    content = "The red mug is on the kitchen shelf."
    payload = (b'{"user_id":"robot-1","scope":"personal","content":"The red mug is on the kitchen shelf.",'
               b'"assertions":[{"subject":"red mug","relation":"located_in","object":"kitchen shelf"}]}\n')

    def validate():
        checked = checked_assertions([row])
        prepare_assertions(content, checked, speaker=None)

    def put():
        table["robot-1/personal/red mug/product/located_in"] = 1

    fd = os.open("/tmp/p6/probe/working.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_TRUNC, 0o600)

    def log_write():
        os.write(fd, payload)

    table = {}
    work = {"validate": validate, "dict_put": put, "log_write": log_write}
    results = {}
    for name, fn in work.items():
        results[f"{name}_idle"] = q(sample(fn, 3000))
    thread = threading.Thread(target=spin, daemon=True); thread.start()
    time.sleep(0.2)
    for name, fn in work.items():
        results[f"{name}_busy"] = q(sample(fn, 3000))
    STOP.set(); thread.join()
    os.close(fd)
    for key, value in results.items():
        print(key, value, flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        BLOCK = float(sys.argv[1])
    main()
