"""Cost of the ways an accept could make its fact restart-proof without a syscall.

The drainer thread keeps Python busy, which is the worst case for anything that releases the GIL: the caller then
waits up to ``sys.setswitchinterval`` to get it back. Each variant is timed idle and busy, at the default interval
and at 1 ms.
"""
import mmap, os, statistics, sys, threading, time


def q(values):
    ordered = sorted(values)
    at = lambda f: round(ordered[min(len(ordered) - 1, int(f * len(ordered)))], 4)
    return {"p50": at(0.5), "p95": at(0.95), "p99": at(0.99), "max": round(ordered[-1], 4)}


def sample(fn, count):
    out = []
    for _ in range(count):
        t = time.perf_counter(); fn(); out.append(1000 * (time.perf_counter() - t))
    return out


def busy_for(seconds, results, work):
    stop = threading.Event()

    def spin():
        while not stop.is_set():
            sum(range(500))

    thread = threading.Thread(target=spin, daemon=True); thread.start()
    time.sleep(0.2)
    for name, fn in work.items():
        results[f"{name}_busy"] = q(sample(fn, 3000))
    stop.set(); thread.join()


def main():
    payload = (b'{"user_id":"robot-1","scope":"personal","content":"The red mug is on the kitchen shelf.",'
               b'"assertions":[{"subject":"red mug","relation":"located_in","object":"kitchen shelf"}]}')
    record = payload.ljust(512, b" ")
    fd = os.open("/tmp/p6/probe/working.log", os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
    os.ftruncate(fd, 512 * 300000)
    mapping = mmap.mmap(fd, 0, access=mmap.ACCESS_WRITE)
    position = [0]
    buffer = bytearray()

    def mmap_write():
        offset = position[0]
        if offset + len(record) > len(mapping):
            offset = 0
        mapping[offset:offset + len(record)] = record
        position[0] = offset + len(record)

    def log_write():
        os.write(fd, record)

    def buffer_extend():
        buffer.extend(record)

    for interval in (sys.getswitchinterval(), 0.001, 0.0005):
        sys.setswitchinterval(interval)
        results = {}
        for name, fn in (("mmap_write", mmap_write), ("os_write", log_write), ("bytearray", buffer_extend)):
            results[f"{name}_idle"] = q(sample(fn, 3000))
        busy_for(0.5, results, {"mmap_write": mmap_write, "os_write": log_write, "bytearray": buffer_extend})
        print(f"switchinterval {interval}", flush=True)
        for key in sorted(results):
            print("  ", key, results[key], flush=True)
    mapping.close()
    os.close(fd)


if __name__ == "__main__":
    main()
