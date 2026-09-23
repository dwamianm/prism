"""Authenticate complete GPT-5.4 cohorts before publishing their comparison."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics

import numpy as np

from benchmarks.integrations import run_gpt54_comparison as s
from benchmarks.integrations.gpt54_budget import (
    MODEL, READER_LIMIT, JUDGE_LIMIT, digest, response_text, sha, usage_cost, write_new,
)
from benchmarks.integrations.gpt54_official_prompt_loader import load_prompt


def verify_protocol():
    """Authenticate the prospective amendments as well as the original freeze."""
    s.validate()
    bindings = [
        ('gpt54-official-loader-amendment.json', 'loader_sha256',
         'gpt54_official_prompt_loader.py'),
        ('gpt54-locomo-source-scheduling.json', 'coordinator_sha256',
         'gpt54_locomo_sources.py'),
        ('gpt54-locomo-queue-plan.json', 'queue_sha256', 'gpt54_locomo_queue.py'),
    ]
    identities = {}
    for filename, key, source in bindings:
        path = s.PUBLIC/filename
        value = json.loads(path.read_text())
        if (value['registration_sha256'] != digest(s.REG)
            or value[key] != digest(s.ROOT/'benchmarks/integrations'/source)):
            raise ValueError('Execution amendment identity differs')
        identities[filename] = digest(path)
    return identities


def verify_statistics(result):
    """Recompute reported measurements from authenticated rows."""
    rows = result['rows']
    groups = defaultdict(list)
    for row in rows:
        if type(row['correct']) is not bool:
            raise ValueError('Score is not a Boolean verdict')
        groups[row['question_type']].append(row)
    categories = {cat: {'correct':sum(r['correct'] for r in values), 'total':len(values)}
                  for cat, values in sorted(groups.items())}
    expected = {
        'correct':sum(r['correct'] for r in rows),
        'accuracy':sum(r['correct'] for r in rows)/len(rows),
        'categories':categories,
        'ci95_questions':s.confidence(rows),
        'ci95_source_clusters':s.confidence(rows, True),
        'context_tokens':{'mean':statistics.mean(r['context_tokens'] for r in rows),
                          'p50':float(np.median([r['context_tokens'] for r in rows]))},
    }
    for key, value in expected.items():
        if result[key] != value:
            raise ValueError(f'Aggregate measurement differs: {key}')
    for key, quantile in [('p50', .5), ('p95', .95)]:
        value = float(np.quantile([r['retrieval_seconds'] for r in rows], quantile))
        if result['retrieval_seconds'][key] != value:
            raise ValueError('Retrieval latency aggregate differs')


def verify_call(path, prompt, limit):
    value = json.loads(path.read_text())
    expected = {'model':MODEL,'input':prompt,'reasoning':{'effort':'medium'},
                'service_tier':'flex','max_output_tokens':limit,'store':False}
    if json.loads(path.with_suffix('.request.json').read_text()) != expected:
        raise ValueError('Provider request differs from registered prompt/settings')
    if value['request_sha256'] != sha(expected) or response_text(value['response']) != value['text']:
        raise ValueError('Provider response binding differs')
    if value['response']['service_tier'] != 'flex':
        raise ValueError('Provider tier changed')
    final = json.loads(path.with_suffix(f".attempt-{value['attempts']}.json").read_text())
    if (final.get('http_status') != 200 or final['response'] != value['response']
        or final['request_sha256'] != sha(expected)):
        raise ValueError('Provider attempt ledger differs')
    return value


def verify_benchmark(benchmark):
    result_path = s.PUBLIC/f'gpt54-{benchmark}-v1-result.json'
    result = json.loads(result_path.read_text())
    cases = s.question_rows(benchmark)
    if (not result['complete'] or result['errors'] or result['completed'] != len(cases)
        or result['registration_sha256'] != digest(s.REG)
        or [r['question_id'] for r in result['rows']] != [q['question_id'] for q in cases]):
        raise ValueError('Incomplete/changed benchmark coverage')
    folder = s.PRIVATE/benchmark
    prepared = json.loads((folder/'prepared.json').read_text())
    if (not prepared['complete'] or prepared['questions'] != len(cases)
        or prepared['registration_sha256'] != digest(s.REG)
        or [r['question_id'] for r in prepared['contexts']] != [q['question_id'] for q in cases]
        or set(p.name for p in (folder/'execution').iterdir()) != {q['question_id'] for q in cases}
        or list((folder/'execution').glob('*/failure.json'))):
        raise ValueError('Preparation or execution coverage differs')
    manifest = {r['question_id']:r['sha256'] for r in prepared['contexts']}
    usage = {'input_tokens':0,'cached_input_tokens':0,'output_tokens':0,'reasoning_tokens':0,
             'successful_calls':0,'http_attempts':0,'observed_nanodollars':0}
    for q, row in zip(cases,result['rows'],strict=True):
        qid = q['question_id']
        path = folder/'contexts'/(qid+'.json')
        if digest(path) != manifest[qid] or digest(path) != row['context_sha256']:
            raise ValueError('Frozen context binding differs')
        capture = json.loads(path.read_text())
        if (capture['question_id'] != qid or row['question_type'] != q['question_type']
            or row['cluster'] != (q.get('conversation_id') or sha(q['haystack_sessions']))
            or row['context_tokens'] != capture['context_tokens']
            or row['retrieval_seconds'] != capture['retrieval_seconds']
            or s.count_tokens(capture['context'], s.clean_config()['packing']['tokenizer'])
               != capture['context_tokens'] or capture['context_tokens'] > 3996):
            raise ValueError('Context or row metadata differs')
        if benchmark == 'longmemeval':
            if (digest(capture['source_capture']) != capture['source_capture_sha256']
                or hashlib.sha256(capture['context'].encode()).hexdigest() != capture['context_sha256']):
                raise ValueError('Historical source capture identity differs')
        elif (capture['metadata']['backend_failures'] or not capture['metadata']['receipt_persisted']
              or hashlib.sha256(capture['context'].encode()).hexdigest() != capture['receipt']['context_sha256']):
            raise ValueError('Retrieval receipt or backend availability differs')
        dest = folder/'execution'/qid
        if json.loads((dest/'result.json').read_text()) != row:
            raise ValueError('Per-question result changed')
        reader_path, judge_path = dest/'reader.json', dest/'judge.json'
        if digest(reader_path) != row['reader_sha256'] or digest(judge_path) != row['judge_sha256']:
            raise ValueError('Result record identity differs')
        reader = verify_call(reader_path,s.reader_prompt(benchmark,q,capture['context']),READER_LIMIT)
        judge = verify_call(judge_path,s.judge_prompt(benchmark,q,reader['text']),JUDGE_LIMIT)
        if s.verdict(judge['text']) != row['correct']:
            raise ValueError('Score rule differs')
        for call in [reader,judge]:
            u = call['response']['usage']
            usage['input_tokens'] += u['input_tokens']
            usage['cached_input_tokens'] += u.get('input_tokens_details',{}).get('cached_tokens',0)
            usage['output_tokens'] += u['output_tokens']
            usage['reasoning_tokens'] += u.get('output_tokens_details',{}).get('reasoning_tokens',0)
            usage['successful_calls'] += 1
            usage['http_attempts'] += call['attempts']
            usage['observed_nanodollars'] += usage_cost(call['response'])
    verify_statistics(result)
    if benchmark == 'locomo':
        if len(prepared['packs']) != 10:
            raise ValueError('Missing conversation pack')
        for record in prepared['packs']:
            pack = Path(record['config']['db_path']).parent
            for entry in record['final_artifact']['files']:
                if digest(pack/entry['path']) != entry['sha256']:
                    raise ValueError('Memory artifact changed after capture')
        usage['ingestion_seconds_sum'] = sum(p['ingestion_seconds'] for p in prepared['packs'])
        usage['stored_turns'] = sum(p['turns'] for p in prepared['packs'])
    return {'complete':True,'questions':len(cases),'result_sha256':digest(result_path),
            'prepared_sha256':digest(folder/'prepared.json'),'usage':usage},result


def main():
    amendments = verify_protocol()
    s.lme._load_official_prompt_function = load_prompt
    verified, results = {}, {}
    for b in ['longmemeval','locomo']:
        verified[b],results[b] = verify_benchmark(b)
    ledger = json.loads((s.PRIVATE/'spending.json').read_text())
    settled = sum(r['charge'] for r in ledger['entries'].values() if r['settled'])
    outstanding = sum(r['charge'] for r in ledger['entries'].values() if not r['settled'])
    probes = json.loads((s.PUBLIC/'gpt54-funded-preflight-20260923-result.json').read_text())
    probes['service_tier'] = 'default'
    preflight = usage_cost(probes) + usage_cost(json.loads((s.PUBLIC/'gpt54-flex-preflight.json').read_text()))
    output = {'complete':True,'verified_at':s.utc(),'registration_sha256':digest(s.REG),
              'analysis_source_sha256':digest(__file__),'benchmarks':verified,
              'amendment_sha256':amendments,
              'settled_observed_usd':(settled+preflight)/1e9,
              'unresolved_reserved_upper_usd':outstanding/1e9,
              'preflight_usd':preflight/1e9,'ledger_sha256':digest(s.PRIVATE/'spending.json')}
    write_new(s.PUBLIC/'gpt54-comparison-v1-verification.json',output)
    lines = ['# PRME default GPT-5.4 benchmark comparison', '',
             f'Completed and authenticated: {output["verified_at"]}.', '',
             '| Benchmark | PRME | Zep published reference | Raw difference |',
             '|---|---:|---:|---:|']
    for b,label,ref in [('longmemeval','LongMemEval-S',.902),('locomo','LoCoMo',1459/1540)]:
        r = results[b]
        lines.append(f'| {label} | **{r["correct"]}/{r["total"]} ({r["accuracy"]*100:.2f}%)** | {ref*100:.2f}% | {(r["accuracy"]-ref)*100:+.2f} points |')
    lines += ['', 'Zep values are [vendor-reported](https://www.getzep.com/research/). Both PRME arms use '
              '`gpt-5.4-2026-03-05` with medium reasoning as reader and judge. This matches the disclosed '
              'model family/reasoning and benchmark scope, but is not an exact reproduction or a live '
              'paired comparison. Zep does not provide its exact prompts, dataset checksum and current '
              'execution artifacts. Its published LoCoMo category counts do not reconcile with its headline.',
              '', 'All experimental flags remained off. No production defaults were changed. LongMemEval '
              'reuses all 500 authenticated production-control contexts from September 22; this is a '
              'new GPT reader/judge evaluation of those unchanged contexts. LoCoMo freshly materializes '
              'all ten conversations with public default APIs, including short turns and supplied image '
              'captions, without dataset observations/summaries or answer labels. It evaluates all '
              '1,540 non-adversarial questions. These examined cohorts are development evidence. '
              'The descriptive bootstrap groups LoCoMo by its ten conversations and LongMemEval '
              'by exact whole-history hash; partially overlapping histories are not merged into one '
              'cluster. Zep reports median contexts of 4,408 tokens for LongMemEval and 5,760 for '
              'LoCoMo, so its context usage is also different from this fixed PRME budget.', '',
              '## Complete-arm measurements', '']
    for b in ['longmemeval','locomo']:
        r,u = results[b],verified[b]['usage']
        ci = r['ci95_questions']
        cluster = r['ci95_source_clusters']
        lines += [f'### {b}', '', f'- Correct: {r["correct"]}/{r["total"]}; question-bootstrap 95% interval '
                  f'[{100*ci[0]:.2f}, {100*ci[1]:.2f}]%; source-cluster interval '
                  f'[{100*cluster[0]:.2f}, {100*cluster[1]:.2f}]%.',
                  f'- Context: mean {r["context_tokens"]["mean"]:.1f}, median {r["context_tokens"]["p50"]:.0f} tokens; '
                  '3,996 effective-token ceiling, including all rendered product context.',
                  f'- Retrieval p50/p95: {r["retrieval_seconds"]["p50"]:.3f}/{r["retrieval_seconds"]["p95"]:.3f} seconds. '
                  + r['retrieval_seconds']['note'],
                  f'- Successful reader/judge calls: {u["successful_calls"]}; HTTP attempts: {u["http_attempts"]}. '
                  f'Reported input/output tokens: {u["input_tokens"]:,}/{u["output_tokens"]:,}, including '
                  f'{u["reasoning_tokens"]:,} output reasoning tokens. Observed successful-call cost: '
                  f'${u["observed_nanodollars"]/1e9:.4f}.', '',
                  '| Category | Correct | Accuracy |','|---|---:|---:|']
        for cat,v in r['categories'].items():
            lines.append(f'| {cat} | {v["correct"]}/{v["total"]} | {100*v["correct"]/v["total"]:.2f}% |')
        if b=='locomo':
            lines += ['', f'Fresh source build: {u["stored_turns"]:,} stored turns; summed worker ingestion time '
                       f'{u["ingestion_seconds_sum"]:.1f} seconds. Parallel worker time is not wall time.']
        lines.append('')
    lines += ['## Costs, failures and validation', '',
              f'Total settled reported usage, including authored controls and funded access probes: '
              f'**${output["settled_observed_usd"]:.4f}**. Unresolved conservative reservations: '
              f'${output["unresolved_reserved_upper_usd"]:.4f}. This is usage-rate accounting, not a billing invoice.',
              '', 'The pre-funding project-credit failure remains recorded. The first authored calibration '
              'failed on an unrelated missing CLI dependency after one successful authored answer; zero '
              'dataset answers had started. An explicitly registered loader amendment retained the exact '
              'official prompt function and ran all authored controls anew. The failed attempt and its cost '
              'remain. LoCoMo source preparation used a registered ownership handoff and independent '
              'conversation workers; no source case was interrupted or omitted. All full-arm request, '
              'response, context and result hashes were independently reauthenticated before this report.',
              '', 'The existing DeepSeek 437/500 (87.4%) run remains separate. Changing both reader and '
              'judge cannot establish a memory-code improvement. No feature promotion or default change '
              'is recommended from this comparison alone.', '',
              'Artifacts: [registration](gpt54-comparison-v1-registration.json), '
              '[verification](gpt54-comparison-v1-verification.json), '
              '[LongMemEval](gpt54-longmemeval-v1-result.json), '
              '[LoCoMo](gpt54-locomo-v1-result.json). All per-question answers and captures remain in '
              'the private run directory; public results bind their checksums.', '']
    path=s.PUBLIC/'GPT54-DEFAULT-BENCHMARK-COMPARISON.md'
    with path.open('x') as handle:
        handle.write('\n'.join(lines))
    print(json.dumps(output,indent=2))


if __name__=='__main__':
    main()
