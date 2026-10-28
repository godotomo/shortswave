import requests, json
BASE="https://api.shortswave.com"
H={"app":"com.shorts.wave.drama","os":"1","os_ver":"13","mobile-brand":"samsung","model":"SM-G991B",
 "device-uuid":"aa11bb22cc33dd44ee55ff6677889900","version":"2.68.00","lang":"en","region":"US",
 "width":"1080","height":"2400","contype":"1","carrier":"","sign-code":"","package-id":"6",
 "advertising-id":"","attribution_id":"","attribution_type":"","afid":"","lip":"",
 "User-Agent":"ShortsWave/2.68.00 (Android;13;samsung;SM-G991B;)",
 "Content-Type":"application/json; charset=utf-8","rule-ab":""}
s=requests.Session()
ui=s.post(BASE+"/login/anonymous",json={"device_type":"android","source_channel":"googleplay"},headers=H,timeout=25).json()["data"]
H["X-SESSION-TOKEN"]=ui["session_token"]
print("user",ui["user_id"],"bonus",ui["bonus"],"coin",ui["coin"])
r=s.get(BASE+"/task/v2/type_list",headers=H,timeout=25); print("type_list",r.text[:300])
r=s.get(BASE+"/task/v2/list",headers=H,timeout=25); j=r.json()
print("task list code",j.get("code"),j.get("msg"))
d=j.get("data")
print(json.dumps(d,indent=2)[:2500] if d else "no data")
json.dump(j,open("probe_tasks.json","w"),indent=2)
