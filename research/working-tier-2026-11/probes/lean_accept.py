"""A lean accept: validation, a preallocated id, a metadata snapshot, a dict put and mmap record write."""
import hashlib, json, mmap, os, statistics, sys, threading, time, uuid


def q(values):
    ordered = sorted(values)
    at = lambda f: round(ordered[min(len(ordered) - 1, int(f * len(ordered)))], 4)
    return {"p50": at(0.5), "p95": at(0.95), "p99": at(0.99), "max": round(ordered[-1], 4)}


def main():
    sys.path.insert(0, os.path.expanduser("~/Sites/prism-working-tier"))
    os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked"})
    from prme.ingestion.assertions import checked_assertions, prepare_assertions
    from prme.storage.metadata import snapshot_metadata

    row = {"subject": "red mug", "subject_type": "product", "relation": "located_in",
           "object": "kitchen shelf", "object_type": "location", "status": "current"}
    content = "The red mug is on the kitchen shelf."
    table, lock = {}, threading.Lock()
    ids = [uuid.uuid4() for _ in range(100000)]
    fd = os.open("/tmp/p6/probe/lean.log", os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
    os.ftruncate(fd, 4096 * 20000)
    mapping = mmap.mmap(fd, 0, access=mmap.ACCESS_WRITE)
    position = [0]
    counter = [0]

    def hashes():
        hashlib.sha256(content.encode()).hexdigest()

    def json_only():
        json.dumps({"user_id": "robot-1", "content": content})

    def lean():
        counter[0] += 1
        identifier = ids[counter[0]]
        _, prepared = prepare_assertions(content, checked_assertions([row]), speaker=None)
        metadata = snapshot_metadata({"prme_assertions_v1": {"version": 1, "items": prepared}})
        record = json.dumps({"id": str(identifier), "content": content, "metadata": metadata,
                             "seq": counter[0]}, separators=(",", ":"))
        payload = record.encode()
        with lock:
            offset = position[0]
            if offset + len(payload) > len(mapping):
                offset = 0
            mapping[offset:offset + len(payload)] = payload
            position[0] = offset + len(payload)
            table[("robot-1", "personal/red mug/product/located_in")] = identifier

    work = {"sha256": hashes, "json": json_only, "lean_accept": lean}
    results = {}
    for name, fn in work.items():
        values = []
        for _ in range(3000):
            t = time.perf_counter(); fn(); values.append(1000 * (time.perf_counter() - t))
        results[f"{name}_idle"] = q(values)
    for round_ in (1, 2):
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
            results[f"{name}_busy_{round_}"] = q(values)
        stop.set(); thread.join()
    mapping.close(); os.close(fd)
    for key in sorted(results):
        print(key, results[key], flush=True)


if __name__ == "__main__":
    main()
