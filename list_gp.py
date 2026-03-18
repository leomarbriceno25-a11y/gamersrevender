from gamepoint_api import listar_productos
r = listar_productos()
if r.get("ok"):
    for p in r["productos"]:
        print(f"ID {p['id']}: {p['name']}")
else:
    print("Error:", r.get("error"))
