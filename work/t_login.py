import json, requests, uuid
BASE="https://api.shortswave.com"
H={
 "User-Agent":"okhttp/3.12.13",
 "Content-Type":"application/json; charset=utf-8",
 "Accept":"application/json",
}
body={"device_type":"android","source_channel":"googleplay","attribution_id":"","attribution_type":"","campaign":"","afid":""}
r=requests.post(BASE+"/login/anonymous",json=body,headers=H,timeout=25)
print("STATUS",r.status_code)
print(r.text[:2000])
