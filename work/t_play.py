import requests, json
BASE="https://api.shortswave.com"
H={"app":"com.shorts.wave.drama","os":"1","os_ver":"13","mobile-brand":"samsung","model":"SM-G991B",
 "device-uuid":"aa11bb22cc33dd44ee55ff6677889900","version":"2.68.00","lang":"en","region":"US",
 "width":"1080","height":"2400","contype":"1","carrier":"","sign-code":"","package-id":"6",
 "advertising-id":"","attribution_id":"","attribution_type":"","afid":"","lip":"",
 "User-Agent":"ShortsWave/2.68.00 (Android;13;samsung;SM-G991B;)",
 "Content-Type":"application/json; charset=utf-8","rule-ab":""}
s=requests.Session()
tok=s.post(BASE+"/login/anonymous",json={"device_type":"android","source_channel":"googleplay","attribution_id":"","attribution_type":"","campaign":"","afid":""},headers=H,timeout=25).json()["data"]["session_token"]
H["X-SESSION-THOKEN"]=tok; H["X-SESSION-TOKEN"]=tok
all_=s.get(BASE+"/v3/all?page=1&page_size=1",headers=H,timeout=25).json()["data"][0]
did=all_["drama_id"]; cid=all_["chapter_id"]
print("drama",did,"chapter",cid,"chapters",all_["chapters"],"charge",all_["charge_chapters"])
print("=== keys of drama item"); print(list(all_.keys()))
json.dump(all_,open("probe_drama_item.json","w"),indent=2)
print("=== chapter list")
rl=s.get(BASE+f"/v1/drama/chapter/list?drama_id={did}",headers=H,timeout=25)
print(rl.status_code, rl.text[:800])
json.dump(rl.json(),open("probe_chapter_list.json","w"),indent=2)
print("=== chapter detail ep1")
rd=s.get(BASE+f"/v1/drama/chapter/detail?drama_id={did}&chapter_id={cid}&auto_pay=0",headers=H,timeout=25)
print(rd.status_code, rd.text[:1500])
json.dump(rd.json(),open("probe_chapter_detail_1.json","w"),indent=2)
