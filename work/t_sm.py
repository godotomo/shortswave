import requests, json, uuid
BASE="https://api.shortswave.com"
H={"app":"com.shorts.wave.drama","os":"1","os_ver":"13","mobile-brand":"samsung","model":"SM-G991B",
 "device-uuid":uuid.uuid4().hex,"version":"2.68.00","lang":"en","region":"US","width":"1080","height":"2400",
 "contype":"1","carrier":"","sign-code":"","package-id":"6","advertising-id":"","attribution_id":"",
 "attribution_type":"","afid":"","lip":"","User-Agent":"ShortsWave/2.68.00 (Android;13;samsung;SM-G991B;)",
 "Content-Type":"application/json; charset=utf-8","rule-ab":""}
s=requests.Session()
ui=s.post(BASE+"/login/anonymous",json={"device_type":"android","source_channel":"googleplay"},headers=H,timeout=25).json()["data"]
H["X-SESSION-TOKEN"]=ui["session_token"]
cfg=s.get(BASE+"/v1/ad/fallback/config",headers=H,timeout=25).json()["data"]
for t in cfg["ad_h5_tasks"]:
    tid=t["taskId"]
    r=s.post(BASE+"/task/v2/start",json={"task_id":tid,"task_type":"sm"},headers=H,timeout=25)
    print("start",tid,"->",r.text[:200])
    q=s.post(BASE+"/task/v2/query",json={"task_id":tid,"task_type":"sm"},headers=H,timeout=25)
    print("  query ->",q.text[:250])
    r2=s.post(BASE+"/v1/ad/task/start",json={"task_id":tid,"task_type":"sm"},headers=H,timeout=25)
    print("  ad/task/start ->",r2.text[:250])
print("bonus:",s.get(BASE+"/user/user_info",headers=H,timeout=25).json()["data"]["bonus"])
