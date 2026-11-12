import json
from fastapi.testclient import TestClient
from valorant_lab.app import create_app


def test_api_security_empty_and_unknown(config):
    app=create_app(config,run_worker=False)
    with TestClient(app) as client:
        assert client.get('/').status_code==200
        assert client.get('/api/sessions').json()==[]
        assert client.get('/api/sessions/999').status_code==404
        assert client.post('/api/sessions/1/retry').status_code==403
        assert client.get('/api/status',headers={'Origin':'https://evil.example'}).status_code==403
        assert client.get('/api/status',headers={'Host':'evil.example'}).status_code==400
        assert client.get('/').headers['content-security-policy']


def test_full_api_review_and_range(config):
    app=create_app(config,run_worker=False)
    p=app.state.pipeline;p.scan();p.scan();p.process_next()
    sid=p.store.list()[0]['id']
    with TestClient(app) as client:
        token=client.get('/api/status').json()['token']
        headers={'X-Lab-Token':token}
        row=client.get(f'/api/sessions/{sid}').json()
        assert row['state']=='complete'
        assert 'path' not in row
        media=client.get(f'/api/sessions/{sid}/video',headers={'Range':'bytes=0-99'})
        assert media.status_code==206 and len(media.content)==100
        invalid=client.put(f'/api/sessions/{sid}/evidence',headers=headers,json={'events':[{'time':99,'kind':'kill'}]})
        assert invalid.status_code==422
        body={'schema_version':1,'events':[{'time':1,'kind':'kill'}],'observations':[{'time':1,'head_x':.5,'head_y':.4,'target_id':'one','crosshair_source':'manual'}]}
        assert client.put(f'/api/sessions/{sid}/evidence',headers=headers,json=body).status_code==200
        assert client.put(f'/api/sessions/{sid}/evidence',headers=headers,json=body).status_code==409
        p.process_next()
        row=client.get(f'/api/sessions/{sid}').json()
        assert row['report']['metrics']['alignment_error']['value']>0
        assert len(client.get('/api/trends').json()['points'])==1
        assert client.post(f'/api/sessions/{sid}/clips',headers=headers,json={'start':0,'end':1000}).status_code==422
        assert client.post(f'/api/sessions/{sid}/retry',headers=headers).status_code==200
        assert client.post(f'/api/sessions/{sid}/retry',headers=headers).status_code==409
