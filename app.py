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
from eventlet.hubs import epolls, kqueue, selects
from dns import dnssec, e164, namedict, tsigkeyring, update, version, zone

# Obtener el directorio base dinámico según el entorno
def get_base_dir():
    if getattr(sys, 'frozen', False):
        # Si está empaquetado con PyInstaller
        return sys._MEIPASS
    else:
        # Entorno de desarrollo
        return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_dir()

# Rutas ajustadas
SERVICE_ACCOUNT_PATH = os.path.join(BASE_DIR, "credentials.json")
TEMP_BIN_PATH = os.path.join(BASE_DIR, "firmware.bin")
DOTENV_PATH = os.path.join(BASE_DIR, ".env")

# Cargar variables de entorno desde el archivo .env
load_dotenv(DOTENV_PATH)

# Obtener clave de Firebase
FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY")

numero_serial = None

# Inicializar Firebase Admin
try:
    if not os.path.isfile(SERVICE_ACCOUNT_PATH):
        raise FileNotFoundError(f"Archivo de credenciales no encontrado: {SERVICE_ACCOUNT_PATH}")

    print("Inicializando Firebase Admin...")
    cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
    initialize_app(cred)
    print("Firebase Admin inicializado correctamente.")
except ValueError as e:
    print("Firebase Admin ya estaba inicializado:", e)
except Exception as e:
    print(f"Error al inicializar Firebase Admin: {e}")
    sys.exit(1)

# Obtener cliente Firestore
try:
    db = firestore.client()
    print("Cliente Firestore obtenido correctamente.")
except Exception as e:
    print(f"Error al obtener cliente Firestore: {e}")
    sys.exit(1)

# Inicializar Flask y Socket.IO
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True)


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
@app.route('/listen_to_job_status', methods=['POST'])
def listen_to_job_status(user, uuid, port):
    global numero_serial
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
@app.route('/program_esp32', methods=['POST'])
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

        socketio.emit('log_update', {"status": "Compilando WavesByte Cibtron WB-001..."})
        run_cloud_run_job_with_env(project_id, region, job_name, parameters, args)
        listen_to_job_status(user, uuid_val, port)
        socketio.emit('log_update', {"status": "Programando WavesByte Cibtron WB-001..."})
        return jsonify({"status": "success", "message": "Cloud Run Job iniciado y monitoreando Firebase."})
    except Exception as e:
        error_message = f"Error al ejecutar el trabajo: {str(e)}"
        print(error_message)
        return jsonify({"status": "error", "message": error_message})

@app.route('/search_serial', methods=['GET'])
def search_serial():
    global numero_serial
    serial_number = request.args.get('serial_number')
    if not serial_number:
        return jsonify({"status": "error", "message": "Número serial no proporcionado."}), 400

    try:
        # Reutilizamos la función `get_most_recent_document_by_serial`
        from lector_firestore import get_most_recent_document_by_serial
        result = get_most_recent_document_by_serial(serial_number)
        numero_serial = serial_number
        if result:
            return jsonify({"status": "success", "data": result})
        else:
            return jsonify({"status": "error", "message": "No se encontró información para este número serial."}), 404
    except Exception as e:
        print(f"Error al buscar número serial: {e}")
        return jsonify({"status": "error", "message": f"Error interno: {str(e)}"}), 500


@app.route('/search_certificates', methods=['GET'])
def search_certificates():
    serial_number = request.args.get('serial_number')
    if not serial_number:
        return jsonify({"status": "error", "message": "Número serial no proporcionado."}), 400

    try:
        from lector_firestore2 import get_all_documents_by_serial
        print(f"Buscando certificados para el número serial: {serial_number}")
        
        result = get_all_documents_by_serial(serial_number)
        print(f"Resultados obtenidos: {result}")

        if result:
            return jsonify({"status": "success", "data": result})
        else:
            return jsonify({"status": "success", "data": []})  # Devolver un array vacío si no hay documentos
    except Exception as e:
        print(f"Error al buscar certificados: {e}")
        return jsonify({"status": "error", "message": f"Error interno: {str(e)}"}), 500


@app.route('/get_ports', methods=['GET'])
def get_ports():
    try:
        ports = list_serial_ports()
        ids_conocidos = ["1A86:7523", "10C4:EA60", "0403:6001"]
        current_ports = [
            {"device": port["device"], "description": port["description"], "hwid": port["hwid"]}
            for port in ports
            if any(id_conocido in port["hwid"] for id_conocido in ids_conocidos)
        ]
        return jsonify({"status": "success", "ports": current_ports})
    except Exception as e:
        print(f"Error al obtener los puertos: {e}")
        return jsonify({"status": "error", "message": str(e)})


@app.route('/get_serial_number', methods=['POST'])
def get_serial_number():
    data = request.json
    port = data.get("port")
    
    if not port:
        return jsonify({"status": "error", "message": "Puerto no proporcionado."}), 400

    try:
        # Configuración de la comunicación serial
        BAUDRATE = 115200
        TIMEOUT = 5  # Aumenta el tiempo de espera si es necesario

        # Llamar a la función para obtener el número de serie
        serial_number = restart_and_get_value(port, BAUDRATE, TIMEOUT)

        if serial_number:
            return jsonify({"status": "success", "serial_number": serial_number})
        else:
            return jsonify({"status": "error", "message": "No se pudo obtener el número de serie."}), 404
    except Exception as e:
        print(f"Error al obtener el número de serie: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/check_port_status', methods=['POST'])
def check_port_status():
    data = request.json
    port = data.get("port")
    
    if not port:
        return jsonify({"status": "error", "message": "Puerto no proporcionado."}), 400

    try:
        ports = list_serial_ports()
        port_devices = [p["device"] for p in ports]

        if port in port_devices:
            return jsonify({"status": "success", "connected": True})
        else:
            return jsonify({"status": "success", "connected": False})
    except Exception as e:
        print(f"Error al verificar el puerto: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == '__main__':
    socketio.run(app, host="127.0.0.1", port=5000, debug=False, allow_unsafe_werkzeug=True)


