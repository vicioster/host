from flask import Flask, request, jsonify
import time

app = Flask(__name__)

# Memoria temporal para almacenar las salas activas
salas_activas = {}

@app.route('/crear', methods=['POST'])
def crear_sala():
    data = request.json
    codigo = data.get("codigo")
    ip = data.get("ip")
    puerto = data.get("puerto")
    juego = data.get("juego", "Juego desconocido")
    host = data.get("host", "Player")
    
    if not codigo or not ip or not puerto:
        return jsonify({"error": "Faltan datos"}), 400
        
    salas_activas[codigo.upper()] = {
        "ip": ip,
        "puerto": puerto,
        "juego": juego,
        "host": host,
        "time": time.time()
    }
    return jsonify({"status": "ok", "codigo": codigo})

@app.route('/salas', methods=['GET'])
def listar_salas():
    # Limpia automáticamente salas inactivas de más de 2 horas
    ahora = time.time()
    activas = {k: v for k, v in salas_activas.items() if ahora - v.get("time", ahora) < 7200}
    return jsonify(activas)

@app.route('/unirse/<codigo>', methods=['GET'])
def unirse_sala(codigo):
    codigo = codigo.upper()
    if codigo in salas_activas:
        return jsonify(salas_activas[codigo])
    return jsonify({"error": "Sala no encontrada"}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
