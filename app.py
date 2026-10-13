from flask import Flask, request, jsonify
import time
import re

app = Flask(__name__)

salas_activas = {}

@app.route('/crear', methods=['POST'])
def crear_sala():
    data = request.json
    codigo = data.get("codigo", "").strip().upper()
    ip = data.get("ip")
    puerto = data.get("puerto")
    juego = data.get("juego", "Juego desconocido")
    host = data.get("host", "Player")
    
    # Validar que el código tenga formato correcto y OBLIGATORIAMENTE al menos un número
    if not codigo or not ip or not puerto or not any(c.isdigit() for c in codigo) or not re.fullmatch(r"U[A-Z0-9]{6,10}", codigo):
        return jsonify({"error": "Código inválido o prohibido"}), 400
        
    salas_activas[codigo] = {
        "ip": ip,
        "puerto": puerto,
        "juego": juego,
        "host": host,
        "time": time.time()
    }
    return jsonify({"status": "ok", "codigo": codigo})

@app.route('/salas', methods=['GET'])
def listar_salas():
    ahora = time.time()
    # Limpiar y filtrar solo códigos válidos con números y antigüedad menor a 2 horas
    activas = {}
    for k, v in salas_activas.items():
        if ahora - v.get("time", ahora) < 7200 and any(c.isdigit() for c in k) and re.fullmatch(r"U[A-Z0-9]{6,10}", k):
            activas[k] = v
    return jsonify(activas)

@app.route('/unirse/<codigo>', methods=['GET'])
def unirse_sala(codigo):
    codigo = codigo.upper()
    if codigo in salas_activas:
        return jsonify(salas_activas[codigo])
    return jsonify({"error": "Sala no encontrada"}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
