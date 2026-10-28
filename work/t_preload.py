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
chs=json.load(open("probe_chapters.json"))
paid=[c for c in chs if c.get("is_free")==0]
print("paid count",len(paid))
# baseline
c=paid[0]
r=s.get(BASE+f"/v1/drama/chapter/detail?drama_id={did}&chapter_id={c['chapter_id']}&auto_pay=0",headers=H,timeout=25).json()
print("baseline ep",c["chapter_index"],"play_url=",repr((r.get("data") or {}).get("play_url")))
# try preload=1
for pv in ["1","true","0"]:
    r=s.get(BASE+f"/v1/drama/chapter/detail?drama_id={did}&chapter_id={c['chapter_id']}&auto_pay=0&preload={pv}",headers=H,timeout=25).json()
    d=r.get("data") or {}
    print(f"preload={pv} code={r.get('code')} play_url={d.get('play_url')!r} preload_field={d.get('preload')!r} price={d.get('price')}")
# ad rule info
r=s.get(BASE+f"/v1/drama/chapter/detail?drama_id={did}&chapter_id={c['chapter_id']}&auto_pay=0",headers=H,timeout=25).json()
d=r.get("data") or {}
print("ad_rule=",json.dumps(d.get("ad_rule"))[:400])
print("is_ad_unlock_user=",d.get("is_ad_unlock_user"),"max=",d.get("max_ad_unlock_count"),"cur=",d.get("current_ad_unlock_count"))
