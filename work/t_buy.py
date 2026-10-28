import requests, json, uuid
BASE="https://api.shortswave.com"
def mk(): return {"app":"com.shorts.wave.drama","os":"1","os_ver":"13","mobile-brand":"samsung","model":"SM-G991B",
 "device-uuid":uuid.uuid4().hex,"version":"2.68.00","lang":"en","region":"US","width":"1080","height":"2400",
 "contype":"1","carrier":"","sign-code":"","package-id":"6","advertising-id":"","attribution_id":"",
 "attribution_type":"","afid":"","lip":"","User-Agent":"ShortsWave/2.68.00 (Android;13;samsung;SM-G991B;)",
 "Content-Type":"application/json; charset=utf-8","rule-ab":""}
s=requests.Session(); H=mk()
ui=s.post(BASE+"/login/anonymous",json={"device_type":"android","source_channel":"googleplay"},headers=H,timeout=25).json()["data"]
H["X-SESSION-TOKEN"]=ui["session_token"]
# farm all tasks
tot=0
for tid in ["1001","1003","1004_1742536988","010101010","1004","999888","202202098","2001","2002","2003","3003"]:
    j=s.post(BASE+"/task/v2/submit",json={"task_id":tid},headers=H,timeout=25).json()
    if j.get("code")==0: tot+=j["data"]["task_bonus"]
print("farmed bonus:",tot)
print("user_info:",json.dumps(s.get(BASE+"/user/user_info",headers=H,timeout=25).json())[:400])
# find a paid chapter
lst=s.get(BASE+"/v3/all?page=1&page_size=5",headers=H,timeout=25).json()["data"]
items=lst if isinstance(lst,list) else (lst.get("list") or [])
did=items[0]["drama_id"]; print("drama",did,items[0].get("drama_title"))
ch=s.get(f"{BASE}/v1/drama/chapter/list?drama_id={did}&page=1&page_size=100",headers=H,timeout=25).json()["data"]
chs=ch if isinstance(ch,list) else (ch.get("list") or [])
paid=[c for c in chs if c.get("is_free")==0][:3]
for c in paid: print("paid",c["chapter_id"],c.get("chapter_name"),c.get("chapter_price"))
c=paid[0]
r=s.get(f"{BASE}/v1/drama/chapter/detail?drama_id={did}&chapter_id={c['chapter_id']}",headers=H,timeout=25).json()
print("pre play_url:",repr(r["data"].get("play_url"))[:120])
r=s.get(f"{BASE}/v1/unlock/drama/chapter?drama_id={did}&chapter_id={c['chapter_id']}&unlock_type=coin",headers=H,timeout=25).json()
print("unlock:",json.dumps(r)[:300])
r=s.get(f"{BASE}/v1/drama/chapter/detail?drama_id={did}&chapter_id={c['chapter_id']}",headers=H,timeout=25).json()
print("post play_url:",repr(r["data"].get("play_url"))[:200])
json.dump({"drama":did,"chapter":c["id"],"url":r["data"].get("play_url")},open("probe_unlocked.json","w"),indent=2)
