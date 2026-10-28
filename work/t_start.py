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
print("ad/fallback/config:",s.get(BASE+"/v1/ad/fallback/config",headers=H,timeout=25).text[:300])
for tid,tt in [("2001","watch_task"),("3003","ads_task"),("tb_cd1_X9aLp7ZqT3vNd","ads_task"),("tb_ad1_Wm4NqE7AzL9Tp","ads_task")]:
    r=s.post(BASE+"/task/v2/start",json={"task_id":tid,"task_type":tt},headers=H,timeout=25)
    print("start",tid,"->",r.text[:250])
r=s.post(BASE+"/v1/ad/task/start",json={"task_id":"tb_cd1_X9aLp7ZqT3vNd","task_type":"ads_task"},headers=H,timeout=25)
print("ad/task/start ->",r.text[:300])
r=s.post(BASE+"/task/v2/independent/list",json={},headers=H,timeout=25); print("indep/list ->",r.text[:300])
r=s.get(BASE+"/task/v2/query",headers=H,timeout=25); print("query GET ->",r.text[:200])
r=s.post(BASE+"/task/v2/query",json={},headers=H,timeout=25); print("query POST ->",r.text[:400])
