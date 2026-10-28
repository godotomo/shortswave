import requests, itertools
BASE="https://api.shortswave.com"
body={"device_type":"android","source_channel":"googleplay","attribution_id":"","attribution_type":"","campaign":"","afid":""}
cands = [
 {"App-Id":"shorts"},{"app_id":"shorts"},{"appid":"shorts"},{"X-App-Id":"shorts"},
 {"App-Id":"shortswave"},{"app_id":"shortswave"},{"X-App-Id":"shortswave"},
 {"App-Id":"com.shorts.wave.drama"},{"app_id":"com.shorts.wave.drama"},{"X-App-Id":"com.shorts.wave.drama"},
 {"Package-Name":"com.shorts.wave.drama"},{"pkg":"com.shorts.wave.drama"},
 {"deviceType":"android"},{"device_type":"android"},
]
for c in cands:
    H={"User-Agent":"okhttp/3.12.13","Content-Type":"application/json; charset=utf-8"}
    H.update(c)
    try:
        r=requests.post(BASE+"/login/anonymous",json=body,headers=H,timeout=20)
        print(c, "->", r.status_code, r.text[:150])
    except Exception as e:
        print(c,"ERR",e)
