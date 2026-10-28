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
tok=ui["session_token"]; H["X-SESSION-TOKEN"]=tok
print("bonus",ui["bonus"],"coin",ui["coin"],"is_ad_unlock_user",ui.get("is_ad_unlock_user"))
did="6adb02c255756f63a270005d"
chs=json.load(open("probe_chapters.json"))
paid=[c for c in chs if c.get("is_free")==0]
c=paid[0]
for ut in ["coin","ad","free","ad_unlock","video"]:
    r=s.get(BASE+f"/v1/unlock/drama/chapter?drama_id={did}&chapter_id={c['chapter_id']}&auto_pay=0&unlock_type={ut}",headers=H,timeout=25)
    j=r.json(); d=j.get("data") or {}
    print(f"unlock_type={ut:9s} -> code={j.get('code')} msg={j.get('msg')} play_url={d.get('play_url')!r} bonus={d.get('bonus')} coin={d.get('coin')}")
