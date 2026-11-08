import grpc
import pytest
from uuid import uuid4
from conftest import auth
import app as module
import buddy_discovery_pb2 as pb

def test_protected_routes_require_token(client):
    assert client.get('/api/v1/runs').status_code==401
    assert client.get('/api/v1/buddy-requests').status_code==401

def test_gateway_derives_sender_from_token(client,subject,monkeypatch):
    seen={}
    def send(body,**kwargs):
        seen['sender']=body.sender_id
        return pb.BuddyRequest(id=str(uuid4()),sender_id=body.sender_id,recipient_id=body.recipient_id,status='pending')
    monkeypatch.setattr(module.discovery,'SendBuddyRequest',send)
    recipient=str(uuid4())
    result=client.post('/api/v1/buddy-requests',json={'runnerId':recipient,'senderId':str(uuid4())},headers=auth(subject))
    assert result.status_code==201
    assert seen['sender']==subject

def test_gateway_invalid_recipient(client,subject):
    assert client.post('/api/v1/buddy-requests',json={'runnerId':'invalid'},headers=auth(subject)).status_code==422

@pytest.mark.parametrize('method,path',[('get','verification/'+str(uuid4())),('post','reports/runners'),('post','reports/reviews'),
  ('get','moderation/queue'),('get','moderation/audit-log'),('post','moderation/suspensions')])
def test_trust_safety_routes_reach_their_service(client,subject,monkeypatch,method,path):
    seen={}
    async def proxy(service,target,request):
        seen.update(service=service,target=target)
        return module.Response(status_code=200)
    monkeypatch.setattr(module,'proxy',proxy)
    assert getattr(client,method)('/api/v1/'+path,headers=auth(subject)).status_code==200
    assert seen=={'service':'TRUST_SAFETY','target':'/api/v1/'+path}

@pytest.mark.parametrize('method,path',[('get','me/profile'),('put','me/profile/visibility'),('post','me/profile/photo-uploads'),
  ('put','me/profile/photo'),('delete','me/profile/photo'),('get','runners/'+str(uuid4()))])
def test_profile_routes_reach_their_service(client,subject,monkeypatch,method,path):
    seen={}
    async def proxy(service,target,request):
        seen.update(service=service,target=target)
        return module.Response(status_code=200)
    monkeypatch.setattr(module,'proxy',proxy)
    assert getattr(client,method)('/api/v1/'+path,headers=auth(subject)).status_code==200
    assert seen=={'service':'PROFILE','target':'/api/v1/'+path}

def test_trust_safety_routes_require_token(client):
    assert client.get('/api/v1/moderation/queue').status_code==401
    assert client.post('/api/v1/reports/runners',json={}).status_code==401

def test_trust_safety_outage_is_a_readable_503(client,subject,monkeypatch):
    monkeypatch.setenv('TRUST_SAFETY_URL','http://127.0.0.1:9')
    result=client.get('/api/v1/moderation/queue',headers=auth(subject))
    assert result.status_code==503 and result.json()['message']=='Trust Safety service unavailable'
