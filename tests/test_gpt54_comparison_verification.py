import json

import pytest

from benchmarks.integrations.analyze_gpt54_comparison import verify_call
from benchmarks.integrations.gpt54_budget import MODEL, sha


def test_verification_rejects_request_or_response_drift(tmp_path):
    path = tmp_path/'reader.json'
    request = {'model':MODEL,'input':'fixed prompt','reasoning':{'effort':'medium'},
               'service_tier':'flex','max_output_tokens':8192,'store':False}
    response = {'model':MODEL,'status':'completed','service_tier':'flex',
                'output':[{'content':[{'type':'output_text','text':'fixed answer'}]}]}
    value = {'response':response,'text':'fixed answer','request_sha256':sha(request),'attempts':1}
    path.write_text(json.dumps(value))
    path.with_suffix('.request.json').write_text(json.dumps(request))
    path.with_suffix('.attempt-1.json').write_text(json.dumps(
        {'http_status':200,'response':response,'request_sha256':sha(request)}))
    assert verify_call(path,'fixed prompt',8192)['text']=='fixed answer'
    with pytest.raises(ValueError):
        verify_call(path,'changed prompt',8192)
    value['text']='replacement answer'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        verify_call(path,'fixed prompt',8192)
