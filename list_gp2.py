from gamepoint_api import obtener_token, listar_productos, _token_cache

# Forzar token nuevo
_token_cache["token"] = None
_token_cache["timestamp"] = 0

print("Obteniendo token...")
token = obtener_token()
print(f"Token: {token}")

print("\nListando productos...")
r = listar_productos()
if r.get("ok"):
    for p in r["productos"]:
        print(f"ID {p['id']}: {p['name']}")
else:
    print("Error:", r.get("error"))
