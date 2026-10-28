import requests, json
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
did="6adb02c255756f63a270005d"
# chapter list with paging
rl=s.get(BASE+f"/v1/drama/chapter/list?drama_id={did}&page=1&page_size=100",headers=H,timeout=25)
j=rl.json(); print("chapter list code",j.get("code"), j.get("msg"))
data=j.get("data") or []
if isinstance(data,dict): print("keys",list(data.keys())); data=data.get("chapter_list") or data.get("list") or []
print("n chapters",len(data))
if data:
    print(json.dumps(data[0],indent=2)[:800])
    json.dump(data,open("probe_chapters.json","w"),indent=2)
    for ch in data[:8]:
        print(ch.get("chapter_index"), ch.get("chapter_id"), "free=",ch.get("chapter_is_free"), "price=",ch.get("price"), "ut=",ch.get("unlock_type"), "locked=",ch.get("is_lock"), ch.get("play_url","")[:40])
