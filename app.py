from flask import Flask, request, jsonify

app = Flask(__name__)

# Memoria temporal para almacenar las salas activas (Código -> IP y Puerto)
salas_activas = {}

@app.route('/crear', methods=['POST'])
def crear_sala():
    data = request.json
    codigo = data.get("codigo")
    ip = data.get("ip")
    puerto = data.get("puerto")
    
    if not codigo or not ip or not puerto:
        return jsonify({"error": "Faltan datos"}), 400
        
    salas_activas[codigo.upper()] = {"ip": ip, "puerto": puerto}
    return jsonify({"status": "ok", "codigo": codigo})

@app.route('/unirse/<codigo>', methods=['GET'])
def unirse_sala(codigo):
    codigo = codigo.upper()
    if codigo in salas_activas:
        return jsonify(salas_activas[codigo])
    return jsonify({"error": "Sala no encontrada"}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
