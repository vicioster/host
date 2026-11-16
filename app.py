from flask import Flask, request, jsonify
import time
import re

app = Flask(__name__)

salas_activas = {}

CODIGO_REGEX = re.compile(r"U[A-Z0-9]{7}")

def es_codigo_valido(codigo: str) -> bool:
    if not codigo:
        return False
    codigo = codigo.strip().upper()
    return bool(re.fullmatch(r"U[A-Z0-9]{7}", codigo) or re.fullmatch(r"U[A-Z0-9]{6,10}", codigo))

def limpiar_expiradas(max_age=600):
    ahora = time.time()
    for k in list(salas_activas.keys()):
        v = salas_activas.get(k)
        if not v:
            continue
        if ahora - v.get("time", ahora) > max_age:
            del salas_activas[k]
        elif not es_codigo_valido(k):
            del salas_activas[k]

def _extraer_codigo(codigo_url=None):
    """Codigo desde URL, ?codigo=, JSON, form o body. Nunca levanta 400 por JSON vacio."""
    if codigo_url:
        return codigo_url.strip().upper()
    q = (request.args.get("codigo") or request.args.get("code") or "").strip().upper()
    if q:
        return q
    data = request.get_json(silent=True) or {}
    if isinstance(data, dict):
        for k in ("codigo", "code", "host_code"):
            if data.get(k):
                return str(data[k]).strip().upper()
    if request.form:
        for k in ("codigo", "code"):
            if request.form.get(k):
                return str(request.form.get(k)).strip().upper()
    return ""

def _borrar(codigo: str) -> bool:
    """Borra si existe. Devuelve True si quedo borrada / ya no existe."""
    codigo = (codigo or "").strip().upper()
    if not codigo:
        return False
    if codigo in salas_activas:
        del salas_activas[codigo]
        print(f"[BORRAR] {codigo} borrado")
        return True
    return False

@app.route('/crear', methods=['POST'])
def crear_sala():
    data = request.get_json(silent=True) or {}
    codigo = (data.get("codigo") or "").strip().upper()
    ip = data.get("ip")
    puerto = data.get("puerto")
    juego = data.get("juego", "Juego desconocido")
    host = data.get("host", "Player")
    if not codigo or not ip or not puerto or not es_codigo_valido(codigo):
        return jsonify({"error": "Código inválido o prohibido"}), 400
    limpiar_expiradas()
    for k in list(salas_activas.keys()):
        v = salas_activas[k]
        if v.get("ip") == ip:
            del salas_activas[k]
    salas_activas[codigo] = {"ip": ip, "puerto": puerto, "juego": juego, "host": host, "time": time.time()}
    print(f"[CREAR] {codigo} -> {ip}:{puerto} ({juego}) por {host}")
    return jsonify({"status": "ok", "codigo": codigo})

@app.route('/salas', methods=['GET'])
def listar_salas():
    limpiar_expiradas(max_age=600)
    activas = {k: v for k, v in salas_activas.items() if es_codigo_valido(k)}
    return jsonify(activas)

@app.route('/unirse/<codigo>', methods=['GET'])
def unirse_sala(codigo):
    codigo = codigo.upper().strip()
    limpiar_expiradas()
    if codigo in salas_activas:
        return jsonify(salas_activas[codigo])
    return jsonify({"error": "Sala no encontrada"}), 404

# --- BORRADO: un solo contrato, varias formas de llamarlo ---
# El manager (o.py) llama en este orden:
#   1) DELETE /borrar/<codigo>   (canonico)
#   2) POST  /eliminar {"codigo": codigo}
#   3) GET   /borrar/<codigo>   (fallback)
# Todas devuelven 200 aunque la sala ya no exista (idempotente),
# para que el manager no reintente eternamente un 404.
@app.route('/borrar/<codigo>', methods=['DELETE', 'GET', 'POST'])
def borrar_sala(codigo):
    codigo = _extraer_codigo(codigo)
    _borrar(codigo)
    return jsonify({"status": "borrado", "codigo": codigo})

@app.route('/borrar', methods=['POST', 'DELETE', 'GET'])
def borrar_sala_sin_codigo():
    codigo = _extraer_codigo()
    if not codigo:
        return jsonify({"error": "falta codigo"}), 400
    _borrar(codigo)
    return jsonify({"status": "borrado", "codigo": codigo})

@app.route('/eliminar', methods=['POST', 'DELETE', 'GET'])
@app.route('/eliminar/<codigo>', methods=['POST', 'DELETE', 'GET'])
def eliminar_sala_post(codigo=None):
    codigo = _extraer_codigo(codigo)
    if not codigo:
        return jsonify({"error": "falta codigo"}), 400
    _borrar(codigo)
    return jsonify({"status": "borrado", "codigo": codigo})

@app.route('/salas/<codigo>', methods=['DELETE', 'GET'])
def borrar_por_salas(codigo):
    codigo = _extraer_codigo(codigo)
    if request.method == 'GET':
        if codigo in salas_activas:
            return jsonify(salas_activas[codigo])
        return jsonify({"error": "Sala no encontrada"}), 404
    _borrar(codigo)
    return jsonify({"status": "borrado", "codigo": codigo})

@app.route('/', methods=['GET'])
def index():
    return jsonify({"status": "ok", "salas_activas": len(salas_activas),
                    "endpoints": ["/crear POST", "/salas GET", "/unirse/<codigo> GET",
                                  "/borrar/<codigo> DELETE/GET/POST", "/eliminar POST/DELETE/GET",
                                  "/salas/<codigo> DELETE"]})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
