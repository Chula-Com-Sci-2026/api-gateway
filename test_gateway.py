import grpc
import pytest
from uuid import uuid4
from conftest import auth
import app as module
import buddy_discovery_pb2 as pb

def test_next_run_is_the_soonest_joined_run_that_has_not_ended():
    now=module.datetime(2026,11,15,3,0,tzinfo=module.timezone.utc)
    run=lambda id,ends,joined=True,status='scheduled':{'id':id,'endsAt':ends,'joined':joined,'status':status}
    runs=[run('ended-yesterday','2026-11-14T12:00:00+00:00'),run('not-joined','2026-11-15T12:00:00Z',joined=False),
          run('completed','2026-11-15T13:00:00+00:00',status='completed'),run('next','2026-11-16T00:00:00+00:00'),run('later','2026-11-18T00:00:00+00:00')]
    assert module.next_run(runs,now)['id']=='next'
    assert module.next_run(runs[:3],now) is None

def test_protected_routes_require_token(client):
    assert client.get('/api/v1/runs').status_code==401
    assert client.get('/api/v1/buddy-requests').status_code==401

@pytest.fixture
def no_links(monkeypatch):
    """Discovery reports no buddies and no requests, so a new request is allowed through."""
    monkeypatch.setattr(module.discovery,'ListBuddies',lambda body,**kwargs:pb.ListBuddiesResponse())
    monkeypatch.setattr(module.discovery,'ListBuddyRequests',lambda body,**kwargs:pb.ListBuddyRequestsResponse())

def test_gateway_derives_sender_from_token(client,subject,monkeypatch,no_links):
    seen={}
    def send(body,**kwargs):
        seen['sender']=body.sender_id
        return pb.BuddyRequest(id=str(uuid4()),sender_id=body.sender_id,recipient_id=body.recipient_id,status='pending')
    monkeypatch.setattr(module.discovery,'SendBuddyRequest',send)
    recipient=str(uuid4())
    result=client.post('/api/v1/buddy-requests',json={'runnerId':recipient,'senderId':str(uuid4())},headers=auth(subject))
    assert result.status_code==201
    assert seen['sender']==subject

def test_gateway_forwards_the_note_and_returns_it(client,subject,monkeypatch,no_links):
    def send(body,**kwargs):
        return pb.BuddyRequest(id=str(uuid4()),sender_id=body.sender_id,recipient_id=body.recipient_id,status='pending',note=body.note)
    monkeypatch.setattr(module.discovery,'SendBuddyRequest',send)
    result=client.post('/api/v1/buddy-requests',json={'runnerId':str(uuid4()),'note':'Evening 5K this week?'},headers=auth(subject))
    assert result.status_code==201
    assert result.json()['note']=='Evening 5K this week?'
    assert client.post('/api/v1/buddy-requests',json={'runnerId':str(uuid4()),'note':42},headers=auth(subject)).status_code==422

def refuse_send(body,**kwargs):
    raise AssertionError('Discovery must not be asked to send a duplicate request')

def test_gateway_refuses_a_request_to_an_existing_buddy(client,subject,monkeypatch):
    buddy=str(uuid4())
    monkeypatch.setattr(module.discovery,'ListBuddies',lambda body,**kwargs:pb.ListBuddiesResponse(runner_ids=[buddy]))
    monkeypatch.setattr(module.discovery,'SendBuddyRequest',refuse_send)
    result=client.post('/api/v1/buddy-requests',json={'runnerId':buddy},headers=auth(subject))
    assert result.status_code==409 and result.json()['message']=='You are already running buddies'

@pytest.mark.parametrize('incoming',[False,True])
def test_gateway_refuses_while_a_request_either_way_is_pending(client,subject,monkeypatch,incoming):
    other=str(uuid4())
    sender,recipient=(other,subject) if incoming else (subject,other)
    monkeypatch.setattr(module.discovery,'ListBuddies',lambda body,**kwargs:pb.ListBuddiesResponse())
    monkeypatch.setattr(module.discovery,'ListBuddyRequests',lambda body,**kwargs:pb.ListBuddyRequestsResponse(
        buddy_requests=[pb.BuddyRequest(id=str(uuid4()),sender_id=sender,recipient_id=recipient,status='pending')]))
    monkeypatch.setattr(module.discovery,'SendBuddyRequest',refuse_send)
    assert client.post('/api/v1/buddy-requests',json={'runnerId':other},headers=auth(subject)).status_code==409

def test_gateway_allows_a_new_request_after_a_decline(client,subject,monkeypatch):
    other=str(uuid4())
    monkeypatch.setattr(module.discovery,'ListBuddies',lambda body,**kwargs:pb.ListBuddiesResponse())
    monkeypatch.setattr(module.discovery,'ListBuddyRequests',lambda body,**kwargs:pb.ListBuddyRequestsResponse(
        buddy_requests=[pb.BuddyRequest(id=str(uuid4()),sender_id=subject,recipient_id=other,status='rejected')]))
    monkeypatch.setattr(module.discovery,'SendBuddyRequest',lambda body,**kwargs:pb.BuddyRequest(id=str(uuid4()),sender_id=subject,recipient_id=other,status='pending'))
    assert client.post('/api/v1/buddy-requests',json={'runnerId':other},headers=auth(subject)).status_code==201

def test_gateway_refuses_a_request_to_yourself(client,subject):
    assert client.post('/api/v1/buddy-requests',json={'runnerId':subject},headers=auth(subject)).status_code==422

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

@pytest.mark.parametrize('path',['runners/'+str(uuid4())+'/rating-summary','runners/'+str(uuid4())+'/running-summary','me/history'])
def test_reputation_reads_reach_their_service(client,subject,monkeypatch,path):
    seen={}
    async def proxy(service,target,request):
        seen.update(service=service,target=target)
        return module.Response(status_code=200)
    monkeypatch.setattr(module,'proxy',proxy)
    assert client.get('/api/v1/'+path,headers=auth(subject)).status_code==200
    assert seen=={'service':'REPUTATION','target':'/api/v1/'+path}

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

class FakeRequest:
    headers={'authorization':'Bearer token'}

def test_suggested_runners_are_pace_matches_without_existing_buddies(monkeypatch):
    seen={}
    def search(body,**kwargs):
        seen['pace']=body.pace
        return pb.SearchRunnersResponse(runners_json='[{"id":"buddy"},{"id":"a"},{"id":"b"},{"id":"c"},{"id":"d"}]')
    monkeypatch.setattr(module.discovery,'SearchRunners',search)
    monkeypatch.setattr(module.discovery,'ListBuddies',lambda body,**kwargs:pb.ListBuddiesResponse(runner_ids=['buddy']))
    assert [runner['id'] for runner in module.suggested_runners(FakeRequest())]==['a','b','c']
    assert seen['pace']=='match'

def test_suggested_runners_are_empty_when_discovery_fails(monkeypatch):
    class Unavailable(grpc.RpcError):
        def code(self): return grpc.StatusCode.UNAVAILABLE
        def details(self): return 'Profile search unavailable'
    def search(body,**kwargs): raise Unavailable()
    monkeypatch.setattr(module.discovery,'SearchRunners',search)
    assert module.suggested_runners(FakeRequest())==[]
