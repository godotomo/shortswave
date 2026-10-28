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
for tid in ["1001","1003","1004_1742536988","010101010","1004","999888","202202098","2001","2002","2003","3003"]:
    s.post(BASE+"/task/v2/submit",json={"task_id":tid},headers=H,timeout=25)
did="6adb02c255756f63a270005d"; cid="6adb02cf55756f63a2700065"
r=s.get(f"{BASE}/v1/unlock/drama/chapter?drama_id={did}&chapter_id={cid}&unlock_type=coin",headers=H,timeout=25).json()
d=r["data"]
for k in ["bonus","coin","chapter_is_free","play_url","price","app_price","unlock_type","is_ad_unlock_user","max_ad_unlock_count","current_ad_unlock_count","watch_ad_remain_count","ad_rule","user_unlock_status","unlock_status","can_unlock","is_unlock"]:
    if k in d: print(f"{k} = {json.dumps(d[k])[:300]}")
print("---- all keys (unlock resp):"); print(sorted(d.keys()))
json.dump(d,open("probe_unlock_resp.json","w"),indent=2)
