import requests

uid = "13644572012"
servers = ['us','sg','la','br','id','ind','tw','vn','th','me','pk','bd','eg','ru','cis','na','sa','eu']

for s in servers:
    try:
        url = f"https://freefire-api-six.vercel.app/get_player_personal_show?server={s}&uid={uid}"
        r = requests.get(url, timeout=15)
        print(f"{s}: {r.status_code} - {r.text[:300]}")
    except Exception as e:
        print(f"{s}: ERROR - {e}")
