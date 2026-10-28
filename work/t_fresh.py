import requests, json, uuid, time
BASE="https://api.shortswave.com"
def mk(dev):
    H={"app":"com.shorts.wave.drama","os":"1","os_ver":"13","mobile-brand":"samsung","model":"SM-G991B",
     "device-uuid":dev,"version":"2.68.00","lang":"en","region":"US",
     "width":"1080","height":"2400","contype":"1","carrier":"","sign-code":"","package-id":"6",
     "advertising-id":"","attribution_id":"","attribution_type":"","afid":"","lip":"",
     "User-Agent":"ShortsWave/2.68.00 (Android;13;samsung;SM-G991B;)",
     "Content-Type":"application/json; charset=utf-8","rule-ab":""}
    s=requests.Session()
    ui=s.post(BASE+"/login/anonymous",json={"device_type":"android","source_channel":"googleplay"},headers=H,timeout=25).json()["data"]
    H["X-SESSION-TOKEN"]=ui["session_token"]
    return s,H,ui
did="6adb02c255756f63a270005d"
chs=json.load(open("probe_chapters.json"))
paid=[c for c in chs if c.get("is_free")==0]
for i in range(3):
    dev=uuid.uuid4().hex
    s,H,ui=mk(dev)
    print(f"--- device {dev} user={ui['user_id']} bonus={ui['bonus']} ad_user={ui.get('is_ad_unlock_user')}")
    for idx in [0,1,2,3]:
        c=paid[idx]
        j=s.get(BASE+f"/v1/unlock/drama/chapter?drama_id={did}&chapter_id={c['chapter_id']}&auto_pay=0&unlock_type=ad",headers=H,timeout=25).json()
        d=j.get("data") or {}
        print(f"   ep{c['chapter_index']} code={j.get('code')} msg={j.get('msg')} play_url={(d.get('play_url') or '')[:50]!r} bonus={d.get('bonus')}")
    time.sleep(1)
