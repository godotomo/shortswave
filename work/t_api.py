import requests, json
BASE="https://api.shortswave.com"
H={
 "app":"com.shorts.wave.drama","os":"1","os_ver":"13","mobile-brand":"samsung","model":"SM-G991B",
 "device-uuid":"aa11bb22cc33dd44ee55ff6677889900","version":"2.68.00","lang":"en","region":"US",
 "width":"1080","height":"2400","contype":"1","carrier":"","sign-code":"","package-id":"6",
 "advertising-id":"","attribution_id":"","attribution_type":"","afid":"","lip":"",
 "User-Agent":"ShortsWave/2.68.00 (Android;13;samsung;SM-G991B;)",
 "Content-Type":"application/json; charset=utf-8","rule-ab":"",
}
s=requests.Session()
r=s.post(BASE+"/login/anonymous",json={"device_type":"android","source_channel":"googleplay","attribution_id":"","attribution_type":"","campaign":"","afid":""},headers=H,timeout=25)
tok=r.json()["data"]["session_token"]
print("token",tok)
H["X-SESSION-TOKEN"]=tok
for p in ["/v3/discover/channel","/v3/all?page=1&page_size=3","/v3/more?page=1&page_size=3","/v3/discover/display/channel"]:
    rr=s.get(BASE+p,headers=H,timeout=25)
    print("==",p,rr.status_code, rr.text[:500].replace("\n"," "))
