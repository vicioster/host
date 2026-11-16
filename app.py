from flask import Flask, request, jsonify
import time, re, threading, json, os
from pathlib import Path
try:
    import requests as _req
except Exception:
    _req = None

app = Flask(__name__)
salas_activas = {}
_SALAS_FILE = Path(os.environ.get("SALAS_FILE", "/tmp/salas_activas.json"))
_SALAS_LOCK = threading.Lock()

def _load_salas():
    try:
        with _SALAS_LOCK:
            if _SALAS_FILE.exists():
                data = json.loads(_SALAS_FILE.read_text(encoding="utf-8") or "{}")
                if isinstance(data, dict):
                    salas_activas.clear()
                    salas_activas.update(data)
    except Exception as e:
        print(f"[SALAS] load error: {e}")

def _save_salas():
    try:
        with _SALAS_LOCK:
            tmp = _SALAS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(salas_activas), encoding="utf-8")
            tmp.replace(_SALAS_FILE)
    except Exception as e:
        print(f"[SALAS] save error: {e}")

_country_cache = {}
_country_lock = threading.Lock()

def _flag_for_code(code: str) -> str:
    code = (code or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        return "🌐"
    return chr(127397 + ord(code[0])) + chr(127397 + ord(code[1]))

def _get_country(ip: str):
    ip = (ip or "").strip()
    if not ip or ip in ("127.0.0.1", "::1", ""):
        return ("", "", "🌐")
    with _country_lock:
        hit = _country_cache.get(ip)
        if hit and time.time() - hit[3] < 86400:
            return hit[:3]
    code, name, flag = ("", "", "🌐")
    try:
        if _req is not None:
            r = _req.get(f"http://ip-api.com/json/{ip}?fields=status,country,countryCode", timeout=2)
            if r.status_code == 200:
                d = r.json() or {}
                if d.get("status") == "success":
                    code = (d.get("countryCode") or "").upper()
                    name = d.get("country") or ""
                    flag = _flag_for_code(code)
    except Exception:
        pass
    with _country_lock:
        _country_cache[ip] = (code, name, flag, time.time())
    return (code, name, flag)

def _pais_desde_payload(data: dict, ip: str):
    code = (data.get("country_code") or data.get("host_country_code") or data.get("player2_country_code") or "").strip().upper()
    name = (data.get("pais") or data.get("host_pais") or data.get("player2_pais") or data.get("country") or "").strip()
    flag = (data.get("flag") or data.get("host_flag") or data.get("player2_flag") or "").strip()
    if code and not flag:
        flag = _flag_for_code(code)
    if not code and ip:
        code, name, flag = _get_country(ip)
    if not flag:
        flag = _flag_for_code(code)
    return (code, name, flag)

def es_codigo_valido(codigo: str) -> bool:
    if not codigo: return False
    codigo = codigo.strip().upper()
    return bool(re.fullmatch(r"U[A-Z0-9]{7}", codigo) or re.fullmatch(r"U[A-Z0-9]{6,10}", codigo))

def limpiar_expiradas(max_age=600):
    _load_salas()
    ahora = time.time()
    _changed = False
    for k in list(salas_activas.keys()):
        v = salas_activas.get(k)
        if not v: continue
        if ahora - v.get("time", ahora) > max_age:
            del salas_activas[k]
            _changed = True
        elif not es_codigo_valido(k):
            del salas_activas[k]
            _changed = True
        else:
            p2t = v.get("player2_time")
            if v.get("player2") and p2t and ahora - p2t > 120:
                v["player2"] = ""; v["player2_time"] = 0
                v["player2_country_code"] = ""; v["player2_pais"] = ""; v["player2_flag"] = "🌐"
                _changed = True
    if _changed:
        _save_salas()

def _extraer_codigo(codigo_url=None):
    if codigo_url: return codigo_url.strip().upper()
    q = (request.args.get("codigo") or request.args.get("code") or "").strip().upper()
    if q: return q
    data = request.get_json(silent=True) or {}
    if isinstance(data, dict):
        for k in ("codigo","code","host_code"):
            if data.get(k): return str(data[k]).strip().upper()
    if request.form:
        for k in ("codigo","code"):
            if request.form.get(k): return str(request.form.get(k)).strip().upper()
    return ""

def _extraer_jugador():
    data = request.get_json(silent=True) or {}
    if isinstance(data, dict):
        for k in ("jugador","player2","jugador2","p2","name","host"):
            if data.get(k): return str(data[k]).strip()
    if request.form:
        for k in ("jugador","player2","name"):
            if request.form.get(k): return str(request.form.get(k)).strip()
    return (request.args.get("jugador") or request.args.get("name") or "").strip()

def _borrar(codigo: str) -> bool:
    codigo = (codigo or "").strip().upper()
    if not codigo: return False
    _load_salas()
    if codigo in salas_activas:
        del salas_activas[codigo]
        _save_salas()
        print(f"[BORRAR] {codigo} borrado y persistido")
        return True
    print(f"[BORRAR] {codigo} ya no estaba")
    return True

@app.route('/crear', methods=['POST'])
def crear_sala():
    data = request.get_json(silent=True) or {}
    codigo = (data.get("codigo") or "").strip().upper()
    ip = data.get("ip"); puerto = data.get("puerto")
    juego = data.get("juego", "Juego desconocido"); host = data.get("host", "Player")
    if not codigo or not ip or not puerto or not es_codigo_valido(codigo):
        return jsonify({"error": "Código inválido o prohibido"}), 400
    limpiar_expiradas()
    for k in list(salas_activas.keys()):
        if salas_activas[k].get("ip") == ip: del salas_activas[k]
    code, name, flag = _pais_desde_payload(data, ip)
    salas_activas[codigo] = {"ip": ip, "puerto": puerto, "juego": juego,
                             "host": host, "time": time.time(),
                             "player2": "", "player2_time": 0,
                             "player2_country_code": "", "player2_pais": "", "player2_flag": "🌐",
                             "espectadores": 0,
                             "host_country_code": code, "host_pais": name, "host_flag": flag,
                             "country_code": code, "pais": name, "flag": flag}
    _save_salas()
    print(f"[CREAR] {codigo} -> {ip}:{puerto} ({juego}) por {host} {flag} {code}")
    return jsonify({"status": "ok", "codigo": codigo})

@app.route('/salas', methods=['GET'])
def listar_salas():
    limpiar_expiradas(max_age=600)
    _load_salas()
    return jsonify({k: v for k, v in salas_activas.items() if es_codigo_valido(k)})

@app.route('/unirse/<codigo>', methods=['GET', 'POST'])
def unirse_sala(codigo):
    codigo = codigo.upper().strip()
    limpiar_expiradas()
    _load_salas()
    if codigo not in salas_activas:
        return jsonify({"error": "Sala no encontrada"}), 404
    sala = salas_activas[codigo]
    if request.method == 'GET':
        return jsonify(sala)
    data = request.get_json(silent=True) or {}
    rol = (data.get("rol") or "").strip().lower()
    jugador = _extraer_jugador()
    if rol in ("spectator", "espectador"):
        sala["espectadores"] = sala.get("espectadores", 0) + 1
        _save_salas()
        return jsonify({"status": "ok", "codigo": codigo, "rol": "spectator"})
    if not jugador:
        return jsonify({"error": "falta jugador"}), 400
    actual = (sala.get("player2") or "").strip()
    if actual and actual.lower() != jugador.lower():
        return jsonify({"error": "sala llena", "player2": actual, "host": sala.get("host")}), 409
    ip_p2 = (data.get("ip") or "").strip()
    if not ip_p2:
        fwd = request.headers.get("X-Forwarded-For", "")
        ip_p2 = fwd.split(",")[0].strip() if fwd else ""
    code, name, flag = _pais_desde_payload(data, ip_p2)
    sala["player2"] = jugador
    sala["player2_time"] = time.time()
    sala["player2_country_code"] = code
    sala["player2_pais"] = name
    sala["player2_flag"] = flag
    _save_salas()
    print(f"[UNIRSE] {jugador} {flag} {code} -> {codigo}")
    return jsonify({"status": "ok", "codigo": codigo, "host": sala.get("host"), "player2": jugador})

@app.route('/salir/<codigo>', methods=['POST', 'DELETE', 'GET'])
@app.route('/salir', methods=['POST', 'DELETE', 'GET'])
def salir_sala(codigo=None):
    codigo = _extraer_codigo(codigo)
    jugador = _extraer_jugador()
    _load_salas()
    if not codigo:
        return jsonify({"error": "falta codigo"}), 400
    if codigo in salas_activas:
        sala = salas_activas[codigo]
        actual = (sala.get("player2") or "").strip()
        if not jugador or not actual or actual.lower() == jugador.lower():
            sala["player2"] = ""; sala["player2_time"] = 0
            sala["player2_country_code"] = ""; sala["player2_pais"] = ""; sala["player2_flag"] = "🌐"
            _save_salas()
    return jsonify({"status": "ok", "codigo": codigo})

@app.route('/borrar/<codigo>', methods=['DELETE','GET','POST'])
@app.route('/borrar', methods=['POST','DELETE','GET'])
@app.route('/eliminar', methods=['POST','DELETE','GET'])
@app.route('/eliminar/<codigo>', methods=['POST','DELETE','GET'])
@app.route('/cerrar', methods=['POST','DELETE','GET'])
@app.route('/cerrar/<codigo>', methods=['POST','DELETE','GET'])
@app.route('/terminar', methods=['POST','DELETE','GET'])
@app.route('/terminar/<codigo>', methods=['POST','DELETE','GET'])
def eliminar_sala_generica(codigo=None):
    c = _extraer_codigo(codigo)
    if not c:
        return jsonify({"error": "falta codigo"}), 400
    _borrar(c)
    return jsonify({"status": "borrado", "codigo": c})

@app.route('/salas/<codigo>', methods=['DELETE','GET'])
def borrar_por_salas(codigo):
    c = _extraer_codigo(codigo)
    if request.method == 'GET':
        _load_salas()
        if c in salas_activas: return jsonify(salas_activas[c])
        return jsonify({"error": "Sala no encontrada"}), 404
    _borrar(c)
    return jsonify({"status": "borrado", "codigo": c})

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"status": "ok"})

@app.route('/', methods=['GET'])
def index():
    _load_salas()
    return jsonify({"status": "ok", "salas_activas": len(salas_activas)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
