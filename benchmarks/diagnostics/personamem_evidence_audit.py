"""Post-hoc annotated-snippet coverage, never a causal or label-validity claim."""

import argparse
import json
from pathlib import Path

from benchmarks.diagnostics.packing_reader import canonical, digest
from benchmarks.diagnostics.verify_personamem import require
from benchmarks.personamem import cohort_identity, conversation_sources, history_path, case_id


def audit(plan_path, verification_path, result_path, data, artifacts):
    plan_raw, verification_raw = plan_path.read_bytes(), verification_path.read_bytes()
    plan, verification = json.loads(plan_raw), json.loads(verification_raw)
    require(verification["native_study_exit_code"] == 0 and verification["questions"] == 96
            and verification["contexts_reproduced"] == 288 and verification["plan_sha256"] == digest(plan_raw),
            "Completed verified pilot required")
    result_raw = result_path.read_bytes()
    require(digest(result_raw) == verification["source_output_sha256"], "Verified source output changed")
    result = json.loads(result_raw)
    selected, identity = cohort_identity(data / "benchmark/text/benchmark.csv", data)
    require(identity == plan["cohort"], "Cohort changed")
    prepared_raw = (artifacts / "prepared.json").read_bytes()
    require(digest(prepared_raw) == result["prepared_sha256"], "Verified prepared contexts changed")
    prepared = json.loads(prepared_raw)
    prepared_by_id = {r["question"]["id"]: r for r in prepared}
    scores = {r["question_id"]: r for r in verification["details"]}
    captures = {}
    for i, owner in enumerate(identity["selected_persona_ids"]):
        raw = (artifacts / f"capture-{i:03d}.json").read_bytes()
        ref = result["captures"][i]
        require(ref["persona_id"] == owner and ref["path"] == f"capture-{i:03d}.json"
                and digest(raw) == ref["sha256"], "Verified candidate capture changed")
        captures[owner] = json.loads(raw)
    results = []
    for row in selected:
        qid = case_id(row)
        turns = conversation_sources((data / history_path(row)).read_bytes())
        snippet = json.loads(row["related_conversation_snippet"])
        require(isinstance(snippet, list) and bool(snippet), "Expected a nonempty annotated dialog snippet")
        matches = []
        for message in snippet:
            require(isinstance(message, dict) and message["role"] in ("user", "assistant")
                    and isinstance(message["content"], str), "Unexpected snippet format")
            ids = [t.id for t in turns if t.role == message["role"] and t.content == message["content"]]
            matches.append({"role": message["role"], "source_ids": ids})
        candidate_ids = {c["node"]["metadata"]["source_turn"] for c in captures[row["persona_id"]]["captures"][qid]["candidates"]}
        arms = {}
        for arm in ("density", "quarter", "score"):
            retained = set(prepared_by_id[qid]["contexts"][arm]["measurement"]["content_source_ids"])
            # Multiple identical occurrences are equivalent for this text-only
            # diagnostic. Never choose the occurrence with the highest score.
            arms[arm] = {"matched_snippet_messages_retained": sum(bool(set(m["source_ids"]) & retained) for m in matches),
                         "all_snippet_messages_retained": all(bool(set(m["source_ids"]) & retained) for m in matches),
                         "reader_correct": scores[qid]["correct"][arm]}
        results.append({"question_id": qid, "persona_id": row["persona_id"], "who": row["who"], "pref_type": row["pref_type"],
            "snippet_messages": len(matches), "matches": matches,
            "matched_snippet_messages": sum(bool(m["source_ids"]) for m in matches),
            "candidate_snippet_messages": sum(bool(set(m["source_ids"]) & candidate_ids) for m in matches), "arms": arms})
    summary = {"questions": len(results), "snippet_messages": sum(r["snippet_messages"] for r in results),
               "matched_snippet_messages": sum(r["matched_snippet_messages"] for r in results),
               "candidate_snippet_messages": sum(r["candidate_snippet_messages"] for r in results),
               "all_snippet_messages_matched_questions": sum(r["matched_snippet_messages"] == r["snippet_messages"] for r in results),
               "arms": {}}
    for arm in ("density", "quarter", "score"):
        summary["arms"][arm] = {
            "snippet_messages_retained": sum(r["arms"][arm]["matched_snippet_messages_retained"] for r in results),
            "all_snippet_messages_retained_questions": sum(r["arms"][arm]["all_snippet_messages_retained"] for r in results),
            "wrong_despite_all_snippet_messages_retained": sum(r["arms"][arm]["all_snippet_messages_retained"] and not r["arms"][arm]["reader_correct"] for r in results),
        }
    return {"analysis": "post-hoc exact annotated-snippet message coverage", "verification_sha256": digest(verification_raw),
            "plan_sha256": digest(plan_raw), "summary": summary, "details": results,
            "limits": "Related snippets are synthetic annotations, not a complete or independently validated set of necessary/sufficient evidence. Exact role/content matching does not establish semantic support or a causal explanation for answer errors. No retrieval or reader input is changed."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "verification", "result", "data", "artifacts", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "Refusing to overwrite prior audit")
    report = audit(args.plan, args.verification, args.result, args.data, args.artifacts)
    report["runner_sha256"] = digest(Path(__file__).read_bytes())
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
