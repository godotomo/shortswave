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
print("login keys:",sorted(ui.keys()))
print("bonus",ui.get("bonus"),"coin",ui.get("coin"))
u=s.get(BASE+"/user/user_info",headers=H,timeout=25).json()["data"]
print("all user_info keys:")
for k,v in sorted(u.items()):
    if isinstance(v,(int,str,bool,float)) and k in ("bonus","coin","vip","svip","sub_expire","is_vip","free_coin","gift_coin","charge_coin","pay_coin","total_coin","subscribe_status","vip_status","ad_unlock_count"): print(" ",k,"=",v)
print("numeric-ish keys:",[k for k in sorted(u) if any(t in k for t in ("coin","vip","sub","bonus","unlock","free"))])
json.dump(u,open("probe_user_info.json","w"),indent=2)
for path in ["/task/subcoin/sign/info","/activity/wheel/tasks","/activity/wheel/coupons"]:
    r=s.get(BASE+path,headers=H,timeout=25); print(path,r.text[:400])
