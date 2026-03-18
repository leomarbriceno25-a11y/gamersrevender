from gamepoint_api import obtener_token, listar_productos, obtener_saldo, detalle_producto, _token_cache

# Forzar token nuevo
_token_cache["token"] = None
_token_cache["timestamp"] = 0

print("=== TEST GAMEPOINT API (VPS) ===\n")

print("[1] Token...")
token = obtener_token()
print(f"    Token: {token}\n")

print("[2] Saldo...")
s = obtener_saldo()
print(f"    {s}\n")

print("[3] Productos...")
r = listar_productos()
if r.get("ok"):
    for p in r["productos"]:
        print(f"    ID {p['id']}: {p['name']}")
else:
    print(f"    Error: {r.get('error')}")
