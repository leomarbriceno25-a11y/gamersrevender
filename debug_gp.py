import jwt, time, requests, json

SK = '4d642026a78b03d66710'
PI = '8e444f41-3c46-4226-b90d-2fb8db46fbed'
URL = 'https://api.gamepointclub.net'

# Paso 1: Obtener token
t = int(time.time())
tok_jwt = jwt.encode({"timestamp": t}, SK, algorithm="HS256")
print(f"JWT type: {type(tok_jwt)}, value: {tok_jwt[:50]}...")

r = requests.post(f"{URL}/merchant/token",
    json={"payload": tok_jwt},
    headers={"Content-Type": "application/json", "Accept": "text/plain", "partnerid": PI},
    timeout=15)
print(f"Token response: {r.text}")
data = r.json()
raw_token = data.get("token", "")
print(f"Raw token: [{raw_token}]")
print(f"Raw token repr: {repr(raw_token)}")
clean_token = raw_token.strip('"')
print(f"Clean token: [{clean_token}]")

# Paso 2: Usar token para listar productos
t2 = int(time.time())
payload2 = {"timestamp": t2, "token": clean_token}
tok_jwt2 = jwt.encode(payload2, SK, algorithm="HS256")
print(f"\nProduct list payload: {json.dumps(payload2)}")

r2 = requests.post(f"{URL}/product/list",
    json={"payload": tok_jwt2},
    headers={"Content-Type": "application/json", "Accept": "text/plain", "partnerid": PI},
    timeout=15)
print(f"Product list response: {r2.text[:300]}")
