import jwt, time, requests
SK = '4d642026a78b03d66710'
PI = '8e444f41-3c46-4226-b90d-2fb8db46fbed'
t = int(time.time())
tok = jwt.encode({'timestamp': t}, SK, algorithm='HS256')
r = requests.post('https://api.gamepointclub.net/merchant/token',
    json={'payload': tok},
    headers={'Content-Type': 'application/json', 'Accept': 'text/plain', 'partnerid': PI},
    timeout=15)
print('Status:', r.status_code)
print('Body:', r.text[:300])
