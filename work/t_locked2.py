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
for ch in [chs[0], chs[1], chs[5], chs[20], chs[49]]:
    cid=ch["chapter_id"]; idx=ch["chapter_index"]
    r=s.get(BASE+f"/v1/drama/chapter/detail?drama_id={did}&chapter_id={cid}&auto_pay=0",headers=H,timeout=25)
    j=r.json()
    d=j.get("data") or {}
    print(f"--- ep{idx} code={j.get('code')} msg={j.get('msg')} list_price={ch.get('chapter_price')} is_free={ch.get('is_free')}")
    print("   chapter_is_free=",d.get("chapter_is_free"),"price=",d.get("price"),"unlock_type=",d.get("unlock_type"))
    print("   play_url      =",repr(d.get("play_url")))
    print("   play_url_720p =",repr(d.get("play_url_720p")))
