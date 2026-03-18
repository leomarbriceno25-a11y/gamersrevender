import jwt, time, requests, json

SK = '4d642026a78b03d66710'
PI = '8e444f41-3c46-4226-b90d-2fb8db46fbed'
URL = 'https://api.gamepointclub.net'

# Paso 1: Obtener token NUEVO (sin cache)
t = int(time.time())
print(f"Timestamp: {t}")
tok_jwt = jwt.encode({"timestamp": t}, SK, algorithm="HS256")

r = requests.post(f"{URL}/merchant/token",
    json={"payload": tok_jwt},
    headers={"Content-Type": "application/json", "Accept": "text/plain", "partnerid": PI},
    timeout=15)
print(f"Token response raw: {r.text}")
data = r.json()
token = data.get("token", "").strip('"')
print(f"Token: {token}")

# Paso 2: Esperar 1 segundo y usar token para balance (mas simple)
time.sleep(1)
t2 = int(time.time())
payload2 = {"timestamp": t2, "token": token}
print(f"\nBalance request timestamp: {t2}")
print(f"Balance payload: {json.dumps(payload2)}")
tok_jwt2 = jwt.encode(payload2, SK, algorithm="HS256")
print(f"JWT encoded: {tok_jwt2[:60]}...")

r2 = requests.post(f"{URL}/merchant/balance",
    json={"payload": tok_jwt2},
    headers={"Content-Type": "application/json", "Accept": "text/plain", "partnerid": PI},
    timeout=15)
print(f"Balance response: {r2.text}")

# Paso 3: Probar con comillas en el token (tal vez la API espera las comillas)
t3 = int(time.time())
payload3 = {"timestamp": t3, "token": f'"{token}"'}
tok_jwt3 = jwt.encode(payload3, SK, algorithm="HS256")
r3 = requests.post(f"{URL}/merchant/balance",
    json={"payload": tok_jwt3},
    headers={"Content-Type": "application/json", "Accept": "text/plain", "partnerid": PI},
    timeout=15)
print(f"\nWith quotes response: {r3.text}")
