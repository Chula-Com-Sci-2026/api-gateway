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
