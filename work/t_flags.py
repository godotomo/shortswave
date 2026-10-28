import requests, json, collections
BASE="https://api.shortswave.com"
H={"app":"com.shorts.wave.drama","os":"1","os_ver":"13","mobile-brand":"samsung","model":"SM-G991B",
 "device-uuid":"aa11bb22cc33dd44ee55ff6677889900","version":"2.68.00","lang":"en","region":"US",
 "width":"1080","height":"2400","contype":"1","carrier":"","sign-code":"","package-id":"6",
 "advertising-id":"","attribution_id":"","attribution_type":"","afid":"","lip":"",
 "User-Agent":"ShortsWave/2.68.00 (Android;13;samsung;SM-G991B;)",
 "Content-Type":"application/json; charset=utf-8","rule-ab":""}
s=requests.Session()
tok=s.post(BASE+"/login/anonymous",json={"device_type":"android","source_channel":"googleplay"},headers=H,timeout=25).json()["data"]["session_token"]
H["X-SESSION-TOKEN"]=tok
allkeys=collections.Counter(); items=[]
for p in range(1,6):
    r=s.get(BASE+f"/v3/all?page={p}&page_size=20",headers=H,timeout=25).json()
    d=r.get("data") or []
    if not d: break
    items+=d
    for it in d:
        for k in it: allkeys[k]+=1
print("total items",len(items))
print("ALL KEYS:", dict(allkeys))
print("=== unlock_type values:", collections.Counter(str(i.get("unlock_type")) for i in items))
json.dump(items,open("probe_all_items.json","w"),indent=2)
print("=== sample item"); print(json.dumps(items[0],indent=2)[:2500])
