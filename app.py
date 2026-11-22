from flask import Flask, request, jsonify
import time, re, threading, json, os
from pathlib import Path
try:
    import requests as _req
except Exception:
    _req = None
app = Flask(__name__)
salas_activas = {}
# Archivo compartido: si Render corre con varios workers, el dict en memoria
# de cada worker es distinto y un worker borra pero otro sigue listando la sala.
# Con archivo, todos leen/escriben el mismo estado.
_SALAS_FILE = Path(os.environ.get("SALAS_FILE", "/tmp/salas_activas.json"))
# RLock: _load/_save tambien lo toman, y las requests lo toman entero
# (load -> modificar -> save). Sin esto, un keepalive del host y un
# /unirse del P2 que llegaban juntos se pisaban y el P2 desaparecia.
_SALAS_LOCK = threading.RLock()

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
# ================= 1.9.0 CHAT =================
# Chat general del lobby (mockup): historial compartido por todos los que
# tienen la app abierta, no por sala. Mismo patron que las salas: lista en
# memoria + archivo en /tmp para que varios workers de Render vean lo mismo.
# Maximo 100 mensajes, los viejos se podan. No expira por tiempo: el historial
# queda hasta que Render reinicia (como las salas, que expiran por keepalive).
_mensajes_chat: list[dict] = []
_CHAT_FILE = Path(os.environ.get("CHAT_FILE", "/tmp/chat_mensajes.json"))
_CHAT_LOCK = threading.RLock()

def _load_chat():
    try:
        with _CHAT_LOCK:
            if _CHAT_FILE.exists():
                data = json.loads(_CHAT_FILE.read_text(encoding="utf-8") or "[]")
                if isinstance(data, list):
                    _mensajes_chat.clear()
                    _mensajes_chat.extend([m for m in data if isinstance(m, dict)])
    except Exception as e:
        print(f"[CHAT] load error: {e}")

def _save_chat():
    try:
        with _CHAT_LOCK:
            tmp = _CHAT_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(_mensajes_chat[-100:]), encoding="utf-8")
            tmp.replace(_CHAT_FILE)
    except Exception as e:
        print(f"[CHAT] save error: {e}")

_country_cache = {}
_country_lock = threading.Lock()

def _flag_for_code(code: str) -> str:
    code = (code or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        return "🌐"
    return chr(127397 + ord(code[0])) + chr(127397 + ord(code[1]))

def _get_country(ip: str):
    """IP -> (codigo, nombre, bandera). Cache 24h. Solo pais."""
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
    # Si flag llegó como código de 2 letras (ej. "AR"), convertirlo a emoji
    if flag and len(flag) == 2 and flag.isalpha():
        flag = _flag_for_code(flag.upper())
    if code and (not flag or flag == "🌐"):
        flag = _flag_for_code(code)
    if not code and ip:
        code, name, flag = _get_country(ip)
    if not flag or (len(flag) == 2 and flag.isalpha()):
        flag = _flag_for_code(code)
    return (code, name, flag)

def es_codigo_valido(codigo: str) -> bool:
    if not codigo: return False
    codigo = codigo.strip().upper()
    return bool(re.fullmatch(r"U[A-Z0-9]{7}", codigo) or re.fullmatch(r"U[A-Z0-9]{6,10}", codigo))

def limpiar_expiradas(max_age=90):
    """Expira salas sin actividad reciente.
    El cliente host envía keepalive cada ~25s, así 90s sin update = sala muerta.
    Antes era 600s y las salas quedaban colgadas al cerrar el emulador.
    """
    _load_salas()
    ahora = time.time()
    _changed = False
    for k in list(salas_activas.keys()):
        v = salas_activas.get(k)
        if not v: continue
        if ahora - v.get("time", ahora) > max_age:
            del salas_activas[k]
            _changed = True
            print(f"[EXPIRA] {k} (sin keepalive > {max_age}s)")
        elif not es_codigo_valido(k):
            del salas_activas[k]
            _changed = True
        else:
            # NO borrar player2 por timeout aca. ESTE ERA EL BUG DEL ESPECTADOR:
            # el host hace keepalive cada 25s y el P2 cada 30s, pero si un
            # heartbeat se demoraba >90s (Render lento / red), esto borraba
            # al P2 en mitad de la partida y el lobby volvia a mostrar
            # "Esperando rival...". Entonces el 3ro hacia doble click,
            # el cliente veia player2 vacio y lo mandaba a JUGAR en vez
            # de preguntar por espectador (o chocaba con 409 sala llena).
            # Ahora el P2 solo se borra con /salir explicito, o cuando
            # expira TODA la sala (sin keepalive del host >90s, arriba).
            if "status" not in v:
                v["status"] = "en_curso" if v.get("player2") else "esperando"
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

def _sala_estado(codigo: str, sala: dict) -> dict:
    """Unica fuente de verdad para 'hay 2 jugadores?'."""
    p2 = (sala.get("player2") or "").strip()
    host = (sala.get("host") or "").strip()
    status = "en_curso" if p2 else "esperando"
    return {"codigo": (codigo or "").strip().upper(), "host": host, "player2": p2,
            "status": status, "llena": bool(p2), "jugadores": 2 if p2 else 1,
            "espectadores": sala.get("espectadores", 0), "juego": sala.get("juego", "")}

def _releer_p2_desde_archivo(codigo: str) -> dict:
    """Lee el archivo SIN tocar salas_activas. Sirve para fusionar un P2
    que entro JUSTO entre el load y el save del keepalive del host."""
    try:
        if _SALAS_FILE.exists():
            data = json.loads(_SALAS_FILE.read_text(encoding="utf-8") or "{}")
            if isinstance(data, dict):
                v = data.get((codigo or "").strip().upper()) or {}
                if isinstance(v, dict):
                    return v
    except Exception:
        pass
    return {}

def _borrar(codigo: str) -> bool:
    codigo = (codigo or "").strip().upper()
    if not codigo: return False
    _load_salas()
    if codigo in salas_activas:
        del salas_activas[codigo]
        _save_salas()
        print(f"[BORRAR] {codigo} borrado (persistido)")
        return True
    print(f"[BORRAR] {codigo} ya no estaba (idempotente 200)")
    return True

@app.route('/crear', methods=['POST'])
def crear_sala():
    data = request.get_json(silent=True) or {}
    codigo = (data.get("codigo") or "").strip().upper()
    ip = data.get("ip"); puerto = data.get("puerto")
    juego = data.get("juego", "Juego desconocido"); host = data.get("host", "Player")
    if not codigo or not ip or not puerto or not es_codigo_valido(codigo):
        return jsonify({"error": "Código inválido o prohibido"}), 400
    with _SALAS_LOCK:
        return _crear_sala_locked(codigo, ip, puerto, juego, host, data)

def _crear_sala_locked(codigo, ip, puerto, juego, host, data):
    limpiar_expiradas()
    # Borrar otras salas del mismo IP (excepto esta misma, para permitir keepalive)
    for k in list(salas_activas.keys()):
        if k != codigo and salas_activas[k].get("ip") == ip:
            del salas_activas[k]
    code, name, flag = _pais_desde_payload(data, ip)
    # Si la sala ya existe (keepalive), preservar player2 y espectadores
    prev = salas_activas.get(codigo) or {}
    _p2 = prev.get("player2", "")
    salas_activas[codigo] = {
        "ip": ip, "puerto": puerto, "juego": juego,
        "host": host, "time": time.time(),
        "player2": _p2,
        # Host vivo = partida viva: el keepalive del host renueva tambien
        # al P2. Si el P2 salio, /salir ya lo borro y _p2 es "".
        "player2_time": time.time() if _p2 else prev.get("player2_time", 0),
        "status": "en_curso" if _p2 else "esperando",
        "player2_country_code": prev.get("player2_country_code", ""),
        "player2_pais": prev.get("player2_pais", ""),
        "player2_flag": prev.get("player2_flag", "🌐"),
        "espectadores": prev.get("espectadores", 0),
        "host_country_code": code, "host_pais": name, "host_flag": flag,
        "country_code": code, "pais": name, "flag": flag,
    }
    # Fusion: si el P2 entro justo entre el load y este save, el archivo
    # ya lo tiene y nuestro 'prev' no. Sin esto el keepalive lo borraba.
    _fresco = _releer_p2_desde_archivo(codigo)
    _p2_fresco = (_fresco.get("player2") or "").strip()
    if _p2_fresco and not salas_activas[codigo].get("player2"):
        salas_activas[codigo]["player2"] = _p2_fresco
        salas_activas[codigo]["player2_time"] = _fresco.get("player2_time", time.time())
        salas_activas[codigo]["player2_country_code"] = _fresco.get("player2_country_code", "")
        salas_activas[codigo]["player2_pais"] = _fresco.get("player2_pais", "")
        salas_activas[codigo]["player2_flag"] = _fresco.get("player2_flag", "🌐")
        salas_activas[codigo]["status"] = "en_curso"
        salas_activas[codigo]["espectadores"] = max(
            salas_activas[codigo].get("espectadores", 0), _fresco.get("espectadores", 0))
    _save_salas()
    print(f"[CREAR] {codigo} -> {ip}:{puerto} ({juego}) por {host} {flag} {code}")
    return jsonify({"status": "ok", "codigo": codigo})

@app.route('/salas', methods=['GET'])
def listar_salas():
    limpiar_expiradas(max_age=90)
    out = {}
    for k, v in salas_activas.items():
        if not es_codigo_valido(k):
            continue
        v = dict(v)
        # Status explicito para que el cliente no dependa solo del nombre del P2
        v["status"] = "en_curso" if v.get("player2") else "esperando"
        v["llena"] = bool(v.get("player2"))
        v["jugadores"] = 2 if v.get("player2") else 1
        out[k] = v
    return jsonify(out)

@app.route('/unirse/<codigo>', methods=['GET', 'POST'])
def unirse_sala(codigo):
    codigo = codigo.upper().strip()
    limpiar_expiradas()
    if codigo not in salas_activas:
        return jsonify({"error": "Sala no encontrada"}), 404
    sala = salas_activas[codigo]
    if request.method == 'GET':
        _s = dict(sala)
        _s["status"] = "en_curso" if _s.get("player2") else "esperando"
        _s["llena"] = bool(_s.get("player2"))
        _s["jugadores"] = 2 if _s.get("player2") else 1
        return jsonify(_s)
    data = request.get_json(silent=True) or {}
    rol = (data.get("rol") or "").strip().lower()
    jugador = _extraer_jugador()
    if rol in ("spectator", "espectador"):
        sala["espectadores"] = sala.get("espectadores", 0) + 1
        _save_salas()
        return jsonify({"status": "ok", "codigo": codigo, "rol": "spectator",
                        "player2": sala.get("player2", ""), "host": sala.get("host", ""),
                        "room_status": sala.get("status") or ("en_curso" if sala.get("player2") else "esperando")})
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
    sala["status"] = "en_curso"
    sala["player2_country_code"] = code
    sala["player2_pais"] = name
    sala["player2_flag"] = flag
    _save_salas()
    print(f"[UNIRSE] {jugador} {flag} {code} -> {codigo} ({sala.get('host')} vs {jugador})")
    return jsonify({"status": "ok", "codigo": codigo, "host": sala.get("host"), "player2": jugador})

@app.route('/salir/<codigo>', methods=['POST', 'DELETE', 'GET'])
@app.route('/salir', methods=['POST', 'DELETE', 'GET'])
def salir_sala(codigo=None):
    codigo = _extraer_codigo(codigo)
    jugador = _extraer_jugador()
    data = request.get_json(silent=True) or {}
    rol = (data.get("rol") or "").strip().lower() if isinstance(data, dict) else ""
    _load_salas()
    if not codigo:
        return jsonify({"error": "falta codigo"}), 400
    if codigo in salas_activas:
        sala = salas_activas[codigo]
        if rol in ("spectator", "espectador"):
            # El espectador solo baja el contador, NUNCA toca player2.
            # Antes el cliente ni siquiera avisaba que era espectador.
            sala["espectadores"] = max(0, sala.get("espectadores", 0) - 1)
            _save_salas()
            print(f"[SALIR] espectador {jugador} salio de {codigo}")
            return jsonify({"status": "ok", "codigo": codigo})
        actual = (sala.get("player2") or "").strip()
        if not jugador or not actual or actual.lower() == jugador.lower():
            sala["player2"] = ""; sala["player2_time"] = 0
            sala["player2_country_code"] = ""; sala["player2_pais"] = ""; sala["player2_flag"] = "🌐"
            sala["status"] = "esperando"
            _save_salas()
            print(f"[SALIR] {jugador} salio de {codigo}")
    return jsonify({"status": "ok", "codigo": codigo})

@app.route('/borrar/<codigo>', methods=['DELETE','GET','POST'])
def borrar_sala(codigo):
    codigo = _extraer_codigo(codigo); _borrar(codigo)
    return jsonify({"status": "borrado", "codigo": codigo})

@app.route('/borrar', methods=['POST','DELETE','GET'])
def borrar_sin():
    codigo = _extraer_codigo()
    if not codigo: return jsonify({"error": "falta codigo"}), 400
    _borrar(codigo); return jsonify({"status": "borrado", "codigo": codigo})

@app.route('/eliminar', methods=['POST','DELETE','GET'])
@app.route('/eliminar/<codigo>', methods=['POST','DELETE','GET'])
def eliminar_sala_post(codigo=None):
    codigo = _extraer_codigo(codigo)
    if not codigo: return jsonify({"error": "falta codigo"}), 400
    _borrar(codigo); return jsonify({"status": "borrado", "codigo": codigo})

@app.route('/cerrar', methods=['POST','DELETE','GET'])
@app.route('/cerrar/<codigo>', methods=['POST','DELETE','GET'])
@app.route('/terminar', methods=['POST','DELETE','GET'])
@app.route('/terminar/<codigo>', methods=['POST','DELETE','GET'])
def cerrar_sala(codigo=None):
    codigo = _extraer_codigo(codigo)
    if not codigo: return jsonify({"error": "falta codigo"}), 400
    _borrar(codigo); return jsonify({"status": "borrado", "codigo": codigo})

# ---- CHAT ----
@app.route('/chat', methods=['GET'])
@app.route('/mensajes', methods=['GET'])
def listar_chat():
    _load_chat()
    return jsonify(_mensajes_chat[-100:])

@app.route('/chat', methods=['POST'])
@app.route('/mensajes', methods=['POST'])
def enviar_chat():
    try:
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        nick = str(data.get("nick") or data.get("nickname") or data.get("nombre") or data.get("user") or "pc").strip() or "pc"
        texto = str(data.get("mensaje") or data.get("texto") or data.get("message") or data.get("text") or data.get("msg") or "").replace("\n", " ").replace("\r", " ").strip()[:300]
        if not texto:
            return jsonify({"ok": False, "error": "mensaje vacio"}), 400
        with _CHAT_LOCK:
            _load_chat()
            _mensajes_chat.append({"nick": nick[:30], "mensaje": texto, "texto": texto, "id": str(time.time()), "ts": time.time()})
            del _mensajes_chat[:-100]
            _save_chat()
        return jsonify({"ok": True, "mensajes": _mensajes_chat[-100:]})
    except Exception as e:
        print(f"[CHAT] post error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"status": "ok"})

@app.route('/estado/<codigo>', methods=['GET'])
@app.route('/estado', methods=['GET', 'POST'])
def estado_sala(codigo=None):
    # Endpoint dedicado SOLO para preguntar "hay 2 jugadores?".
    # El cliente lo consulta en el doble click, sin depender de /salas.
    codigo = _extraer_codigo(codigo)
    with _SALAS_LOCK:
        _load_salas()
        if not codigo or codigo not in salas_activas:
            return jsonify({"error": "Sala no encontrada", "llena": False, "jugadores": 0}), 404
        return jsonify(_sala_estado(codigo, salas_activas[codigo]))

@app.route('/salas/<codigo>', methods=['DELETE','GET'])
def borrar_por_salas(codigo):
    codigo = _extraer_codigo(codigo)
    _load_salas()
    if request.method == 'GET':
        if codigo in salas_activas:
            _s = dict(salas_activas[codigo])
            _s["status"] = "en_curso" if _s.get("player2") else "esperando"
            _s["llena"] = bool(_s.get("player2"))
            _s["jugadores"] = 2 if _s.get("player2") else 1
            return jsonify(_s)
        return jsonify({"error": "Sala no encontrada"}), 404
    _borrar(codigo); return jsonify({"status": "borrado", "codigo": codigo})

@app.route('/', methods=['GET'])
def index():
    _load_salas()
    _load_chat()
    return jsonify({"status": "ok", "salas_activas": len(salas_activas), "chat_mensajes": len(_mensajes_chat)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
