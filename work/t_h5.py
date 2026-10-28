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
j=s.get(BASE+"/v1/ad/fallback/config",headers=H,timeout=25).json()
json.dump(j,open("probe_ad_fallback.json","w"),indent=2)
d=j["data"]; print("keys:",list(d.keys()))
for t in d.get("ad_h5_tasks",[]):
    print({k:v for k,v in t.items() if k!="taskConfigList"}, "cfgs:",len(t.get("taskConfigList") or []))
for k,v in d.items():
    if k!="ad_h5_tasks": print(k,"=",json.dumps(v)[:200])
tid=d["ad_h5_tasks"][0]["taskId"]
for body in [{"task_id":tid},{"task_id":tid,"task_type":"ads_task"},{"task_id":tid,"task_type":1}]:
    print("query",body,"->",s.post(BASE+"/task/v2/query",json=body,headers=H,timeout=25).text[:300])
for tt in ["ads_task","watch_ads","h5_ad","ad_h5_task","ads"]:
    print("start",tt,"->",s.post(BASE+"/task/v2/start",json={"task_id":tid,"task_type":tt},headers=H,timeout=25).text[:200])
print("submit",s.post(BASE+"/task/v2/submit",json={"task_id":tid},headers=H,timeout=25).text[:300])
print("bonus now:",s.get(BASE+"/user/user_info",headers=H,timeout=25).json()["data"]["bonus"])
