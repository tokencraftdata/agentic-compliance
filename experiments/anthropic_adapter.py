"""Optional network adapter, never used by the bundled offline evidence.

Contract: one request JSON on stdin, one response JSON on stdout. Credentials
stay in the local environment; request/response logs never contain HTTP headers.
API reference: https://platform.claude.com/docs/en/api/http/messages/create
"""
import json
import os
import sys
import urllib.request

API_VERSION='2026-10-21'


def payload(request):
    sampling=request.get('sampling',{})
    if set(sampling)-{'temperature','top_p'}:raise ValueError('unsupported sampling parameter')
    return {'model':request['model'],'system':request['system'],
            'max_tokens':request['max_output_tokens'],**sampling,
            'messages':[{'role':'user','content':json.dumps({k:v for k,v in request.items()
                if k not in ('model','system','max_output_tokens','sampling')},sort_keys=True)}]}


def main():
    key=os.environ.get('ANTHROPIC_API_KEY')
    if not key:raise ValueError('provider key is not configured')
    request=json.load(sys.stdin)
    req=urllib.request.Request('https://api.anthropic.com/v1/messages',
        data=json.dumps(payload(request)).encode(),method='POST',
        headers={'Content-Type':'application/json','x-api-key':key,'anthropic-version':API_VERSION})
    with urllib.request.urlopen(req,timeout=50) as response:body=json.load(response)
    if body.get('stop_reason')!='end_turn':raise ValueError('incomplete model response')
    text=''.join(part['text'] for part in body.get('content',[]) if part.get('type')=='text')
    print(json.dumps({'model':body['model'],'output_text':text,'usage':body.get('usage',{}),
                      'provider_response_id':body.get('id'),'api_version':API_VERSION}))


if __name__=='__main__':
    try:main()
    except Exception as error:
        # Do not echo exception bodies, request headers or credentials.
        print(type(error).__name__,file=sys.stderr);raise SystemExit(1)
