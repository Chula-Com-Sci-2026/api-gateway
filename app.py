"""Browser BFF; addresses are deployment configuration, never browser-facing service URLs."""
import asyncio
import json
import os
from uuid import UUID
import grpc
import httpx
from fastapi import Depends,FastAPI,HTTPException,Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from google.protobuf.json_format import MessageToDict
import buddy_discovery_pb2 as pb
import buddy_discovery_pb2_grpc as rpc
from common import setup,user

app=FastAPI(title="Can't Run Alone Gateway",version="1.0.0")
setup(app)
app.add_middleware(CORSMiddleware,allow_origins=os.environ.get("FRONTEND_ORIGINS","http://localhost:5173,http://localhost:5174").split(","),
                   allow_credentials=True,allow_methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS"],allow_headers=["Authorization","Content-Type","X-Correlation-ID"])
# One HTTP/2 channel pins every call to one backend; DISCOVERY_LB_POLICY=round_robin with a
# dns:/// headless target spreads calls over all resolved Discovery pods. Unset keeps gRPC's default.
lb_policy=os.environ.get("DISCOVERY_LB_POLICY")
channel=grpc.insecure_channel(os.environ["DISCOVERY_TARGET"],options=[("grpc.lb_policy_name",lb_policy)] if lb_policy else None)
discovery=rpc.BuddyDiscoveryServiceStub(channel)

async def proxy(service,path,request):
    headers={name:request.headers[name] for name in ("authorization","cookie","content-type","x-correlation-id") if name in request.headers}
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            result=await client.request(request.method,os.environ[service+"_URL"]+path,params=request.query_params,content=await request.body(),headers=headers)
    except httpx.HTTPError:
        raise HTTPException(503,f"{service.replace('_',' ').title()} service unavailable")
    response=Response(result.content,status_code=result.status_code,media_type=result.headers.get("content-type"))
    for cookie in result.headers.get_list("set-cookie"):
        response.headers.append("set-cookie",cookie)
    return response

def grpc_call(method,body,request):
    try:
        result=method(body,metadata=[("authorization",request.headers.get("authorization",""))],timeout=10)
        return MessageToDict(result,preserving_proto_field_name=False)
    except grpc.RpcError as error:
        statuses={grpc.StatusCode.UNAUTHENTICATED:401,grpc.StatusCode.PERMISSION_DENIED:403,grpc.StatusCode.NOT_FOUND:404,
                  grpc.StatusCode.ALREADY_EXISTS:409,grpc.StatusCode.FAILED_PRECONDITION:409,grpc.StatusCode.INVALID_ARGUMENT:422}
        raise HTTPException(statuses.get(error.code(),503),error.details())

@app.api_route("/api/v1/auth/{operation}",methods=["POST"])
async def auth(operation:str,request:Request):
    if operation not in ("register","verify-otp","login","refresh","logout"):
        raise HTTPException(404,"Unknown authentication operation")
    return await proxy("IDENTITY",f"/api/v1/auth/{operation}",request)

@app.get("/api/v1/runners")
def runners(request:Request,claims=Depends(user)):
    try:
        radius=float(request.query_params.get("radiusKm",10))
    except ValueError:
        raise HTTPException(422,"Invalid radius")
    result=grpc_call(discovery.SearchRunners,pb.SearchRunnersRequest(radius_km=radius,experience=request.query_params.get("experience","All"),
      pace=request.query_params.get("pace",""),availability=request.query_params.get("availability","")),request)
    runners=json.loads(result.get("runnersJson","[]"))
    if request.query_params.get("verifiedOnly")=="true":
        return [runner for runner in runners if runner["verified"]]
    return runners

@app.get("/api/v1/buddy-requests")
def requests(request:Request,claims=Depends(user)):
    result=grpc_call(discovery.ListBuddyRequests,pb.ListBuddyRequestsRequest(runner_id=claims["sub"]),request)
    return result.get("buddyRequests",[])

@app.post("/api/v1/buddy-requests",status_code=201)
async def send(request:Request,claims=Depends(user)):
    body=await request.json()
    try:
        recipient=str(UUID(body.get("runnerId","")))
    except (ValueError,TypeError):
        raise HTTPException(422,"Valid runnerId required")
    return grpc_call(discovery.SendBuddyRequest,pb.SendBuddyRequestRequest(sender_id=claims["sub"],recipient_id=recipient),request)

@app.patch("/api/v1/buddy-requests/{request_id}")
async def respond(request_id:UUID,request:Request,claims=Depends(user)):
    body=await request.json()
    return grpc_call(discovery.RespondToBuddyRequest,pb.RespondToBuddyRequestRequest(id=str(request_id),recipient_id=claims["sub"],decision=body.get("decision","")),request)

@app.delete("/api/v1/buddy-requests/{request_id}",status_code=204)
def cancel(request_id:UUID,request:Request,claims=Depends(user)):
    grpc_call(discovery.CancelBuddyRequest,pb.CancelBuddyRequestRequest(id=str(request_id),sender_id=claims["sub"]),request)
    return Response(status_code=204)

@app.get("/api/v1/buddies")
def buddies(request:Request,claims=Depends(user)):
    return grpc_call(discovery.ListBuddies,pb.ListBuddiesRequest(),request).get("runnerIds",[])

def suggested_runners(request,limit=3):
    """Home's strongest matches: Discovery's search with the pace filter on (pace ranges that overlap
    the caller's own), nearest first, minus runners who are already buddies. Optional on Home, so a
    Discovery outage or a caller without a profile yields none instead of failing the dashboard."""
    try:
        found=json.loads(grpc_call(discovery.SearchRunners,pb.SearchRunnersRequest(radius_km=10,experience="All",pace="match"),request).get("runnersJson","[]"))
        buddies=set(grpc_call(discovery.ListBuddies,pb.ListBuddiesRequest(),request).get("runnerIds",[]))
    except HTTPException:
        return []
    return [runner for runner in found if runner["id"] not in buddies][:limit]

@app.get("/api/v1/dashboard")
async def dashboard(request:Request,claims=Depends(user)):
    headers={"Authorization":request.headers["authorization"]}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            profile=await client.get(os.environ["PROFILE_URL"]+"/api/v1/me/profile",headers=headers)
            runs=await client.get(os.environ["SCHEDULING_URL"]+"/api/v1/runs",headers=headers)
            history=await client.get(os.environ["REPUTATION_URL"]+"/api/v1/me/history",headers=headers)
            for result in (profile,runs,history):
                if result.status_code!=200:
                    raise HTTPException(result.status_code,"Complete your profile or check service availability")
        # gRPC calls block, so they run off the event loop.
        suggested=await asyncio.to_thread(suggested_runners,request)
        return {"greetingName":profile.json()["name"],"weeklyDistanceKm":history.json()["totalDistanceMeters"]/1000,
                "nextRun":next((run for run in runs.json() if run["status"]=="scheduled"),None),"suggestedRunners":suggested}
    except httpx.HTTPError:
        raise HTTPException(503,"Dashboard service unavailable")

@app.api_route("/api/v1/{path:path}",methods=["GET","POST","PUT","PATCH","DELETE"])
async def route(path:str,request:Request,claims=Depends(user)):
    # Profile also owns visibility and photo routes under me/profile/ (running-buddy-contracts profile.openapi.json).
    if path=="me/profile" or path.startswith("me/profile/") or path.startswith("runners/") and not path.endswith("rating-summary"):
        service="PROFILE"
    elif path=="conditions":
        service="CONDITIONS"
    elif path=="notifications" or path.startswith("notifications/"):
        service="NOTIFICATION"
    elif path=="running_records" or path.startswith("running_records/") or path=="me/history" or path.endswith(("/attendance","/ratings","/rating-summary")):
        service="REPUTATION"
    elif path=="runs" or path.startswith("runs/"):
        service="SCHEDULING"
    elif path.startswith(("verification/","reports/","moderation/")):
        # Moderator permission is decided by Trust & Safety, which owns that rule.
        service="TRUST_SAFETY"
    else:
        raise HTTPException(404,"Unknown API route")
    return await proxy(service,"/api/v1/"+path,request)
