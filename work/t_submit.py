import requests, json, uuid
BASE="https://api.shortswave.com"
def mk(seed=None):
    H={"app":"com.shorts.wave.drama","os":"1","os_ver":"13","mobile-brand":"samsung","model":"SM-G991B",
     "device-uuid":seed or uuid.uuid4().hex,"version":"2.68.00","lang":"en","region":"US",
     "width":"1080","height":"2400","contype":"1","carrier":"","sign-code":"","package-id":"6",
     "advertising-id":"","attribution_id":"","attribution_type":"","afid":"","lip":"",
     "User-Agent":"ShortsWave/2.68.00 (Android;13;samsung;SM-G991B;)",
     "Content-Type":"application/json; charset=utf-8","rule-ab":""}
    return H
s=requests.Session(); H=mk()
ui=s.post(BASE+"/login/anonymous",json={"device_type":"android","source_channel":"googleplay"},headers=H,timeout=25).json()["data"]
H["X-SESSION-TOKEN"]=ui["session_token"]
print("start bonus",ui["bonus"])
for body in [{"task_id":"1001"},{"task_id":"010101010"},{"task_id":"2001"},{"task_id":"3003"},
             {"task_id":"1001","task_type":"new_user","task_content":"open_notify"}]:
    r=s.post(BASE+"/task/v2/submit",json=body,headers=H,timeout=25)
    print(body,"->",r.text[:300])
print("user after:",s.get(BASE+"/user/info",headers=H,timeout=25).text[:300])
