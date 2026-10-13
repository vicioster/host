from flask import Flask, request, jsonify
import time
import re

app = Flask(__name__)

salas_activas = {}

# Regex centralizado igual que en x.py
CODIGO_REGEX = re.compile(r"U[A-Z0-9]{7}")  # 8 chars total: U + 7, variable UCAC7D1E, UD04AB08, U0D4D853

def es_codigo_valido(codigo: str) -> bool:
    if not codigo:
        return False
    codigo = codigo.strip().upper()
    # 8 chars totales: U + 7 alfanum, variable, ej UCAC7D1E, UD04AB08, U0D4D853
    return bool(re.fullmatch(r"U[A-Z0-9]{7}", codigo) or re.fullmatch(r"U[A-Z0-9]{6,10}", codigo))

def limpiar_expiradas(max_age=600):
    """Borra salas con mas de 10 min (antes tenias 7200 = 2hs)"""
    ahora = time.time()
    for k in list(salas_activas.keys()):
        v = salas_activas.get(k)
        if not v:
            continue
        if ahora - v.get("time", ahora) > max_age:
            del salas_activas[k]
        elif not es_codigo_valido(k):
            del salas_activas[k]

@app.route('/crear', methods=['POST'])
def crear_sala():
    data = request.json or {}
    codigo = data.get("codigo", "").strip().upper()
    ip = data.get("ip")
    puerto = data.get("puerto")
    juego = data.get("juego", "Juego desconocido")
    host = data.get("host", "Player")
    
    # Validar formato
    if not codigo or not ip or not puerto or not es_codigo_valido(codigo):
        return jsonify({"error": "Código inválido o prohibido"}), 400
    
    limpiar_expiradas()

    # FIX CLAVE: Si el mismo IP/host crea otra sala, borrar las viejas
    # Esto evita que te quede UF791143 y U0D4D853 del mismo usuario
    for k in list(salas_activas.keys()):
        v = salas_activas[k]
        # Misma IP publica = mismo usuario hosteando de nuevo
        if v.get("ip") == ip:
            # Borrar codigo viejo del mismo IP
            del salas_activas[k]
        # Opcional: mismo nombre de host tambien
        # elif v.get("host") == host and v.get("juego") == juego:
        #     del salas_activas[k]

    salas_activas[codigo] = {
        "ip": ip,
        "puerto": puerto,
        "juego": juego,
        "host": host,
        "time": time.time()
    }
    print(f"[CREAR] {codigo} -> {ip}:{puerto} ({juego}) por {host}")
    return jsonify({"status": "ok", "codigo": codigo})

@app.route('/salas', methods=['GET'])
def listar_salas():
    limpiar_expiradas(max_age=600)  # 10 min, antes 7200
    # Devolver solo activas validas
    activas = {}
    for k, v in salas_activas.items():
        if es_codigo_valido(k):
            activas[k] = v
    return jsonify(activas)

@app.route('/unirse/<codigo>', methods=['GET'])
def unirse_sala(codigo):
    codigo = codigo.upper().strip()
    limpiar_expiradas()
    if codigo in salas_activas:
        return jsonify(salas_activas[codigo])
    return jsonify({"error": "Sala no encontrada"}), 404

# FIX: Endpoints para borrar codigo viejo que tu x.py ya intenta llamar
@app.route('/borrar/<codigo>', methods=['DELETE', 'GET'])
def borrar_sala(codigo):
    codigo = codigo.upper().strip()
    if codigo in salas_activas:
        del salas_activas[codigo]
        print(f"[BORRAR] {codigo} borrado")
        return jsonify({"status": "borrado", "codigo": codigo})
    return jsonify({"error": "no encontrado", "codigo": codigo}), 404

@app.route('/eliminar', methods=['POST'])
def eliminar_sala_post():
    data = request.json or {}
    codigo = data.get("codigo", "").upper().strip()
    if codigo in salas_activas:
        del salas_activas[codigo]
        return jsonify({"status": "borrado", "codigo": codigo})
    return jsonify({"error": "no encontrado"}), 404

@app.route('/', methods=['GET'])
def index():
    return jsonify({"status": "ok", "salas_activas": len(salas_activas), "endpoints": ["/crear POST", "/salas GET", "/unirse/<codigo> GET", "/borrar/<codigo> DELETE"]})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
