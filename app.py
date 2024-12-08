from flask import Flask, render_template, request, jsonify, redirect
from flask_socketio import SocketIO, emit
import os
import sys
from esptool import main as esptool_main
import serial.tools.list_ports
from firebase_admin import credentials, firestore, initialize_app, auth
from google.cloud import storage
import threading
import time
import subprocess
from datetime import datetime 
from dotenv import load_dotenv
from serial_reader import restart_and_get_value

load_dotenv()

# Ruta del archivo de credenciales SA
SERVICE_ACCOUNT_PATH = "./credentials.json"
FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY")

# Definir ruta temporal para el binario
TEMP_BIN_PATH = "./firmware.bin"

numero_serial = None

# Inicializar Firebase Admin
try:
    print("Inicializando Firebase Admin...")
    cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
    initialize_app(cred)
    print("Firebase Admin inicializado correctamente.")
except ValueError as e:
    print("Firebase Admin ya estaba inicializado:", e)
except Exception as e:
    print(f"Error al inicializar Firebase Admin: {e}")
    exit(1)

# Obtener cliente Firestore
try:
    db = firestore.client()
    print("Cliente Firestore obtenido correctamente.")
except Exception as e:
    print(f"Error al obtener cliente Firestore: {e}")
    exit(1)

# Inicializar Flask y Socket.IO
app = Flask(__name__)
socketio = SocketIO(app)

# Detectar los puertos disponibles
def list_serial_ports():
    try:
        ids_conocidos = ["1A86:7523", "10C4:EA60", "0403:6001"]
        ports = serial.tools.list_ports.comports()
        filtered_ports = [
            {"device": port.device, "description": port.description, "hwid": port.hwid}
            for port in ports
            if any(id_conocido in port.hwid for id_conocido in ids_conocidos)
        ]
        return filtered_ports
    except Exception as e:
        print(f"Error al listar puertos: {e}")
        return []

# Emitir periódicamente los puertos disponibles al cliente
def emit_ports_periodically():
    global numero_serial
    detected_ports = {}  # Diccionario para rastrear puertos y su información
    retry_interval = 5   # Intervalo para reintentar obtener serial en segundos

    while True:
        try:
            # Escanear los puertos disponibles
            ports = list_serial_ports()
            current_ports = {port['device']: port for port in ports}

            # Emitir la lista de puertos al cliente
            socketio.emit('update_ports', ports)

            # Agregar nuevos puertos al diccionario
            for port in current_ports.keys():
                if port not in detected_ports:
                    print(f"Nuevo puerto detectado: {port}")
                    # Guardamos la última vez que intentamos leer el serial (0 indica que aún no)
                    detected_ports[port] = {
                        "last_attempt": 0,
                        "serial": None
                    }

            # Intentar leer el número serial para cada puerto que aún no lo tenga
            # y que haya pasado el intervalo de reintento.
            current_time = time.time()
            for port, info in list(detected_ports.items()):
                if info["serial"] is None:
                    # Verificar si podemos intentar ahora (pasó el intervalo desde el último intento)
                    if current_time - info["last_attempt"] >= retry_interval:
                        try:
                            print(f"Intentando leer número serial en el puerto {port}...")
                            numero_serial = restart_and_get_value(port, 115200, 2, keyword="NUMERO_SERIAL")
                            detected_ports[port]["last_attempt"] = time.time()

                            if numero_serial:
                                print(f"Número serial detectado en {port}: {numero_serial}")
                                detected_ports[port]["serial"] = numero_serial
                                # Emitir el número serial al cliente
                                socketio.emit('serial_detected', {"port": port, "serial": numero_serial})
                                # Una vez detectado, ya no necesitamos reintentar
                                # Podrías decidir si eliminar el puerto del diccionario o mantenerlo con el serial detectado
                                # En este ejemplo, lo mantenemos con su serial para saber que ya fue encontrado
                            else:
                                print(f"No se encontró número serial en el puerto {port}. Reintentaremos en {retry_interval} segundos.")
                        except Exception as e:
                            print(f"Error al leer el número serial en el puerto {port}: {e}")
                            # Actualizamos el último intento para reintentar más tarde
                            detected_ports[port]["last_attempt"] = time.time()

            # Limpiar puertos desconectados
            disconnected_ports = set(detected_ports.keys()) - set(current_ports.keys())
            for disconnected_port in disconnected_ports:
                print(f"Puerto desconectado: {disconnected_port}")
                del detected_ports[disconnected_port]

        except Exception as e:
            print(f"Error al emitir puertos: {e}")

        time.sleep(2)  # Esperar antes de volver a escanear


# Descargar el binario desde Google Cloud Storage
def download_binary(gcs_path):
    try:
        if not gcs_path.startswith("gs://"):
            raise ValueError("La ruta no es válida para GCS.")

        gcs_path = gcs_path[5:]
        bucket_name, *file_path_parts = gcs_path.split('/')
        file_path = "/".join(file_path_parts)

        storage_client = storage.Client.from_service_account_json(SERVICE_ACCOUNT_PATH)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(file_path)
        blob.download_to_filename(TEMP_BIN_PATH)
        print(f"Binario descargado correctamente a {TEMP_BIN_PATH}")
        return TEMP_BIN_PATH
    except Exception as e:
        print(f"Error descargando binario desde GCS: {e}")
        raise

# Ejecutar un Cloud Run Job con parámetros
def run_cloud_run_job_with_env(project_id, region, job_name, parameters, args=None):
    try:
        # Forzar autenticación de gcloud con la SA
        subprocess.run(
            [
                "gcloud", "auth", "activate-service-account",
                f"--key-file={SERVICE_ACCOUNT_PATH}"
            ],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        # Formatear las variables de entorno para el Cloud Run Job
        env_vars = ",".join([f"{key}={value}" for key, value in parameters.items()])
        command = [
            "gcloud", "run", "jobs", "execute", job_name,
            f"--project={project_id}",
            f"--region={region}",
            f"--update-env-vars={env_vars}"
        ]
        if args:
            command.append(f"--args={','.join(args)}")

        print("Ejecutando comando:", " ".join(command))

        # Ejecutar el comando para el Cloud Run Job
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            print(f"Error ejecutando el comando: {result.stderr}")
            raise Exception(result.stderr)
        return result.stdout
    except Exception as e:
        print(f"Error al ejecutar el Cloud Run Job: {e}")
        raise

# Middleware para verificar el token de Firebase
def verify_token(token):
    try:
        decoded_token = auth.verify_id_token(token)
        return decoded_token
    except Exception as e:
        print(f"Error verificando el token: {e}")
        return None

# Escuchar cambios en Firestore
def listen_to_job_status(user, uuid, port):
    fecha = datetime.utcnow().strftime("%Y-%m-%d")  # Obtener la fecha actual en formato YYYY-MM-DD
    document_path = f"logs/{numero_serial}/{user}/{uuid}"
    print(f"Escuchando logs en: {document_path}")

    def on_snapshot(doc_snapshot, changes, read_time):
        for doc in doc_snapshot:
            if doc.exists:
                log_data = doc.to_dict()
                job_status = log_data.get("status", "unknown")
                print(f"Estado actualizado del job: {job_status}")
                socketio.emit('log_update', {"status": job_status})

                if job_status == "success":
                    try:
                        binary_path = log_data.get("path")
                        if binary_path:
                            socketio.emit('log_update', {"status": "Descargando binario..."})
                            download_binary(binary_path)

                        socketio.emit('log_update', {"status": "Programando WavesByte Cibtron WB-001..."})
                        program_status = program_esp32(port)
                        socketio.emit('log_update', {"status": program_status})
                    except Exception as e:
                        error_message = f"Error en la programación del taxímetro WavesByte Cibtron WB-001: {str(e)}"
                        socketio.emit('log_update', {"status": error_message})
                elif job_status == "completed":
                    print("El proceso se completó con éxito.")
            else:
                print("El documento ya no existe.")

    doc_ref = db.document(document_path)
    doc_ref.on_snapshot(on_snapshot)

# Programar el ESP32 usando esptool
def program_esp32(port, baud_rate="115200"):
    try:
        if not os.path.isfile(TEMP_BIN_PATH):
            raise FileNotFoundError(f"El archivo firmware.bin no se encuentra en la ruta especificada: {TEMP_BIN_PATH}")

        command = [
            "--port", port,
            "--baud", baud_rate,
            "write_flash",
            "--flash_mode", "dio",
            "--flash_size", "4MB",
            "0x10000", TEMP_BIN_PATH
        ]
        print("Ejecutando esptool con los siguientes argumentos:", command)
        sys.argv = ["esptool.py"] + command
        esptool_main()

        print("El Taxímetro WavesByte Cibtron WB-001 ha sido programado exitosamente.")

        # Eliminar el archivo firmware.bin después de la programación
        try:
            os.remove(TEMP_BIN_PATH)
            print(f"Archivo {TEMP_BIN_PATH} eliminado exitosamente.")
        except Exception as e:
            print(f"Error al eliminar el archivo {TEMP_BIN_PATH}: {e}")

        return "WavesByte Cibtron WB-001 programado exitosamente."
    except Exception as e:
        print(f"Error durante la programación del taxímetro WavesByte Cibtron WB-001: {e}")
        raise


# Ruta de login
@app.route('/login', methods=['GET'])
def login_page():
    return render_template('login.html', api_key=FIREBASE_API_KEY)

# Ruta principal
@app.route('/')
def index():
    id_token = request.cookies.get('idToken')  # Leer token desde la cookie
    if not id_token:
        return redirect('/login')  # Redirige al login si no hay token

    user = verify_token(id_token)
    if user:
        print(f"Usuario autenticado: {user['email']}")
        # Pasar el email del usuario al template
        return render_template('index.html', email=user['email'], uid=user['uid'])
    else:
        return redirect('/login')  # Redirige si el token no es válido

# Establecer token en cookie
@app.route('/set_token', methods=['POST'])
def set_token():
    data = request.json
    id_token = data.get('idToken')
    if not id_token:
        return jsonify({"status": "error", "message": "Falta el token"}), 400

    try:
        decoded_token = verify_token(id_token)
        if decoded_token:
            response = jsonify({"status": "success", "message": "Token establecido"})
            response.set_cookie('idToken', id_token, httponly=True)  # Quitar secure=True para desarrollo
            return response
        else:
            return jsonify({"status": "error", "message": "Token inválido"}), 401
    except Exception as e:
        print(f"Error al establecer el token: {e}")
        return jsonify({"status": "error", "message": "Error al procesar el token"}), 500

# Ruta para cerrar sesión
@app.route('/logout', methods=['POST'])
def logout():
    response = jsonify({"status": "success", "message": "Sesión cerrada"})
    response.set_cookie('idToken', '', httponly=True, expires=0)  # Eliminar la cookie
    return response

@app.route('/execute_and_program', methods=['POST'])
def execute_and_program():
    try:
        parameters = request.form.to_dict()
        project_id = "wavesbyte-taximetro"
        region = "us-central1"
        job_name = "esp32-compiler"
        user = parameters.get("USER")
        uuid_val = parameters.get("UUID")
        args = ["/workspace/compile_and_upload.py"]

        port = parameters.get("port")
        if not port:
            return jsonify({"status": "error", "message": "Debe seleccionar un puerto antes de ejecutar el trabajo."})

        socketio.emit('log_update', {"status": "Ejecutando Cloud Run Job..."})
        run_cloud_run_job_with_env(project_id, region, job_name, parameters, args)

        listen_to_job_status(user, uuid_val, port)

        return jsonify({"status": "success", "message": "Cloud Run Job iniciado y monitoreando Firebase."})
    except Exception as e:
        error_message = f"Error al ejecutar el trabajo: {str(e)}"
        print(error_message)
        return jsonify({"status": "error", "message": error_message})

if __name__ == '__main__':
    threading.Thread(target=emit_ports_periodically, daemon=True).start()
    socketio.run(app, debug=True)
