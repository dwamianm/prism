"""Replay the small lifecycle-wording diagnostic; not an accuracy benchmark."""

import argparse
import json
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    base = next(
        r for r in json.loads(args.input.read_text()) if r["scenario"] == "bi_temporal"
    )
    results = []
    for condition in ("reported_decision", "actually_provisional"):
        for variant in ("original", "renamed_key", "omit_active_lifecycle"):
            lines = []
            for line in base["context"].split("\n"):
                if line.startswith("{"):
                    item = json.loads(line)
                    if (
                        condition == "actually_provisional"
                        and "REST to GraphQL" in item["text"]
                    ):
                        item["text"] = (
                            "The team tentatively agreed last week to consider migrating from REST to GraphQL; no final decision has been made."
                        )
                    if variant == "renamed_key":
                        item["memory_lifecycle"] = item.pop("state")
                    if variant == "omit_active_lifecycle" and item["state"] in (
                        "tentative",
                        "stable",
                    ):
                        item.pop("state")
                    line = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                lines.append(line)
            context = "\n".join(lines)
            body = {
                "model": args.model,
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "num_ctx": 16384, "num_predict": 512},
                "messages": [
                    {"role": "system", "content": base["system_prompt"]},
                    {
                        "role": "user",
                        "content": "MEMORY:\n"
                        + context
                        + "\n\nQUESTION:\n"
                        + base["question"],
                    },
                ],
            }
            req = urllib.request.Request(
                args.base_url.rstrip("/") + "/api/chat",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            data = json.load(urllib.request.urlopen(req, timeout=120))
            answer = data["message"]["content"]
            results.append(
                {
                    "condition": condition,
                    "variant": variant,
                    "answer": answer,
                    "context": context,
                    "question": base["question"],
                    "model": args.model,
                    "options": body["options"],
                    "system_prompt": base["system_prompt"],
                }
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(results, indent=2) + "\n")
            print(condition, variant, answer, flush=True)


if __name__ == "__main__":
    main()
