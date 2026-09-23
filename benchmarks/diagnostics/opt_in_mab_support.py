"""Pinned MemoryAgentBench provider and official-scoring support for this study."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time

from benchmarks.diagnostics import opt_in_successor as study
from benchmarks.diagnostics.register_opt_in_interactions import sha, write_new
from benchmarks.integrations import memoryagentbench as adapter
from benchmarks.integrations import register_memoryagentbench as registrar


UPSTREAM = study.PRIVATE / 'MemoryAgentBench'
PREPARED = study.PRIVATE / 'mab-prepared-v3'


def official_functions():
    with registrar._upstream_imports(UPSTREAM):
        from utils.eval_data_utils import format_chat
        from utils.eval_other_utils import post_process
    return format_chat, post_process


def reader_messages(prepared, context, query):
    format_chat, _ = official_functions()
    return format_chat(message=adapter.reader_message(context + '\n' + query,
        sub_dataset=prepared['dataset_config']['sub_dataset'],
        contract=prepared['agent_config']['reader_output_contract']),
        system_message=prepared['system_message'])


def score(prepared, answer, references):
    _, post_process = official_functions()
    metrics, details = post_process({'output': answer}, references, prepared['dataset_config'])
    # Preserve official parsing, max-over-references and task-specific metrics.
    return {'correct': bool(metrics[prepared['metric']]), 'metrics': metrics, 'details': details}


def request_body(messages, limit):
    return {'model': study.old.MODEL, 'messages': messages,
            'options': {'temperature': 0, 'seed': 42, 'num_ctx': 65536, 'num_predict': limit},
            'think': False, 'stream': False}


async def chat(client, semaphore, messages, limit, path):
    body = request_body(messages, limit)
    record = {'request': body, 'request_sha256': sha(body), 'attempts': []}
    start = time.perf_counter()
    async with semaphore:
        try:
            await study.old.model_digest(client)
            for attempt in range(1, 5):
                stamp = time.perf_counter()
                response = await client.post('/api/chat', json=body)
                record['attempts'].append({'attempt': attempt, 'http_status': response.status_code,
                    'elapsed_seconds': time.perf_counter() - stamp,
                    'response_sha256': hashlib.sha256(response.content).hexdigest()})
                if response.status_code in {429, 500, 502, 503, 504, 529} and attempt < 4:
                    await asyncio.sleep(min(8, .5 * 2 ** (attempt - 1)))
                    continue
                response.raise_for_status()
                value = response.json()
                record['response'] = value
                if (value.get('model') not in {study.old.MODEL, study.old.MODEL.removesuffix(':cloud')}
                    or value.get('done') is not True or value.get('done_reason') != 'stop'
                    or not value.get('message', {}).get('content', '').strip()
                    or type(value.get('prompt_eval_count')) is not int or type(value.get('eval_count')) is not int):
                    raise study.old.ResearchFailure('Provider completion invalid or truncated')
                await study.old.model_digest(client)
                record.update(status='complete', elapsed_seconds=time.perf_counter() - start)
                write_new(path, record)
                return value['message']['content'].strip(), record
            raise study.old.ResearchFailure('Provider attempts exhausted')
        except Exception as exc:
            record.update(status='failed', exception_type=type(exc).__name__, elapsed_seconds=time.perf_counter() - start)
            write_new(path, record)
            raise


def load_task(name):
    path = PREPARED / f'{name}.json'
    return json.loads(path.read_text())


def authored_cases():
    return [
        ('banking', 'Question: I forgot my PIN.\nlabel: 4\nQuestion: My replacement card arrived.\nlabel: 8',
         'Question: The new replacement card has arrived.\n\nlabel:', ['8']),
        ('banking', 'Question: I forgot my PIN.\nlabel: 4\nQuestion: My replacement card arrived.\nlabel: 8',
         'Question: I cannot remember my PIN.\n\nlabel:', ['4']),
        ('eventqa', 'On Monday, Lena drove to York. On Tuesday, Lena drove to Bath.',
         'Now Answer the Question: Where did Lena drive on Tuesday?', ['Bath']),
        ('eventqa', 'On Monday, Lena drove to York. On Tuesday, Lena drove to Bath.',
         'Now Answer the Question: Where did Lena drive on Monday?', ['York']),
        ('conflict', "Mara's favorite color is red. Update: Mara's favorite color is now blue.",
         "What is Mara's current favorite color?", ['blue']),
        ('conflict', 'Rowan lives in Lima. Update: Rowan now lives in Quito.',
         'Where does Rowan currently live?', ['Quito']),
        ('detective', 'Only Rowan had the key. The thief used the key.',
         'Who was the thief?\nA. Iris\nB. Rowan\nC. Lena', ['B. Rowan']),
        ('detective', 'Only Iris knew the safe code. The thief used the code.',
         'Who was the thief?\nA. Iris\nB. Rowan\nC. Lena', ['A. Iris']),
    ]
