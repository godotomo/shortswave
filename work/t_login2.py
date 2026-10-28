import requests, json
BASE="https://api.shortswave.com"
H={
 "app":"com.shorts.wave.drama","os":"1","os_ver":"13","mobile-brand":"samsung","model":"SM-G991B",
 "device-uuid":"aa11bb22cc33dd44ee55ff6677889900","version":"2.68.00","lang":"en","region":"US",
 "width":"1080","height":"2400","contype":"1","carrier":"","sign-code":"","package-id":"6",
 "advertising-id":"","attribution_id":"","attribution_type":"","afid":"","lip":"",
 "User-Agent":"ShortsWave/2.68.00 (Android;13;samsung;SM-G991B;)",
 "Content-Type":"application/json; charset=utf-8",
 "rule-ab":"",
}
body={"device_type":"android","source_channel":"googleplay","attribution_id":"","attribution_type":"","campaign":"","afid":""}
r=requests.post(BASE+"/login/anonymous",json=body,headers=H,timeout=25)
print("STATUS",r.status_code)
print(json.dumps(r.json(),indent=2)[:3000])
