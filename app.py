from flask import Flask, render_template, request, jsonify, redirect
from flask_socketio import SocketIO # send no se usa, se podría quitar
import os
import sys
from esptool import main as esptool_main # Usado para programar ESP32
import serial.tools.list_ports
from google.cloud import storage
import threading # Usado por socketio.start_background_task
import time
# import subprocess # No usado directamente si run_job_with_rest_api es el método
from datetime import datetime # Asegurarse que está importado
from dotenv import load_dotenv
from serial_reader import restart_and_get_value
from resetcibtron import resetcibtron
import json
import base64
from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleAuthRequest
import requests
import shutil
import firebase_admin
from firebase_admin import credentials, firestore, initialize_app, auth


# --- NUEVA FUNCIÓN DE AYUDA ---
def convert_firestore_timestamps(data):
    """
    Convierte recursivamente los objetos datetime de Firestore (o cualquier datetime)
    en un diccionario/lista a cadenas ISO 8601 para serialización JSON.
    """
    if isinstance(data, dict):
        return {key: convert_firestore_timestamps(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [convert_firestore_timestamps(element) for element in data]
    elif isinstance(data, datetime): # Esto debería capturar DatetimeWithNanoseconds también
        return data.isoformat() # Convertir a string ISO 8601
    return data

# --- Configuración Inicial y Paths ---
def get_base_dir():
    if getattr(sys, "frozen", False): # PyInstaller
        return sys._MEIPASS
    else: # Ejecución normal
        return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_dir()
CIBTRON_CRED_FILE = os.path.join(BASE_DIR, "cibtron.txt")
DOTENV_PATH = os.path.join(BASE_DIR, ".env")
FIRMWARE_RESET_PATH = os.path.join(BASE_DIR, "leer_serial_memoria.ino.bin")

load_dotenv(DOTENV_PATH)
CIBTRON_API_KEY_B64 = os.getenv("CIBTRON_API")
if not CIBTRON_API_KEY_B64:
    print("ERROR: La variable de entorno CIBTRON_API no está configurada.")
    sys.exit(1)
CIBTRON_FIREBASE_API_KEY = base64.b64decode(CIBTRON_API_KEY_B64).decode("utf-8")


# --- Credenciales y Clientes de Google Cloud ---
def get_decoded_service_account_credentials():
    try:
        with open(CIBTRON_CRED_FILE, "r") as f:
            base64_credentials = f.read()
        return json.loads(base64.b64decode(base64_credentials))
    except Exception as e:
        print(f"Error crítico: No se pudo cargar o decodificar las credenciales de servicio desde {CIBTRON_CRED_FILE}: {e}")
        sys.exit(1)

DECODED_SERVICE_ACCOUNT_CREDS = get_decoded_service_account_credentials()

try:
    if not firebase_admin._apps:
        cred = credentials.Certificate(DECODED_SERVICE_ACCOUNT_CREDS)
        initialize_app(cred)
        print("Firebase Admin SDK inicializado.")
    else:
        print("Firebase Admin SDK ya estaba inicializado.")
except Exception as e:
    print(f"Error al inicializar Firebase Admin SDK: {e}")
    if "already initialized" not in str(e).lower():
        sys.exit(1)

db = firestore.client()
storage_client_gcs = storage.Client.from_service_account_info(DECODED_SERVICE_ACCOUNT_CREDS)

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True, async_mode='threading')

current_params_job_status = "Listo"
params_monitor_thread = None
is_params_programming = False
current_params_job_numero_serial = None

current_set_serial_job_status = "Listo"
set_serial_monitor_thread = None
is_set_serial_programming = False

firestore_listeners = {}

def reset_params_job_state():
    global current_params_job_status, is_params_programming, params_monitor_thread, current_params_job_numero_serial
    current_params_job_status = "Listo"
    is_params_programming = False
    current_params_job_numero_serial = None
    params_monitor_thread = None
    print("Estado del job de programación de parámetros reseteado.")
    socketio.emit("job_status_update", {"status": current_params_job_status})

def reset_set_serial_job_state():
    global current_set_serial_job_status, is_set_serial_programming, set_serial_monitor_thread
    current_set_serial_job_status = "Listo"
    is_set_serial_programming = False
    set_serial_monitor_thread = None
    print("Estado del job de programación de nuevo serial reseteado.")
    socketio.emit("set_serial_job_status_update", {"status": current_set_serial_job_status})

def generate_gcp_access_token():
    try:
        creds = service_account.Credentials.from_service_account_info(DECODED_SERVICE_ACCOUNT_CREDS)
        scoped_creds = creds.with_scopes(["https://www.googleapis.com/auth/cloud-platform"])
        auth_req = GoogleAuthRequest()
        scoped_creds.refresh(auth_req)
        return scoped_creds.token
    except Exception as e:
        print(f"Error al generar token de acceso GCP: {e}")
        return None

def verify_firebase_id_token(id_token):
    try:
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token
    except Exception as e:
        print(f"Error verificando Firebase ID token: {e}")
        return None

def run_cloud_run_job_via_rest(project_id, region, job_name, env_parameters, job_args=None):
    access_token = generate_gcp_access_token()
    if not access_token:
        raise Exception("No se pudo obtener el token de acceso GCP para ejecutar el job.")

    url = f"https://{region}-run.googleapis.com/v2/projects/{project_id}/locations/{region}/jobs/{job_name}:run"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    container_overrides = [{"env": [{"name": key, "value": str(value)} for key, value in env_parameters.items()]}]
    if job_args:
        container_overrides[0]["args"] = job_args
        
    data = {"overrides": {"containerOverrides": container_overrides}}

    try:
        print(f"Ejecutando Job {job_name} con parámetros: {env_parameters} y args: {job_args}")
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        print(f"Respuesta de ejecución de Job {job_name}: {response.status_code}")
        return response.json()
    except requests.exceptions.RequestException as e:
        error_detail = e.response.text if e.response is not None else str(e)
        print(f"Error en la petición HTTP al ejecutar Cloud Run Job {job_name}: {error_detail}")
        raise Exception(f"Error HTTP ejecutando job: {error_detail}")
    except Exception as e:
         print(f"Error genérico al ejecutar Cloud Run Job {job_name} con API REST: {e}")
         raise

def download_gcs_binary(gcs_path, target_local_dir, uuid_for_file):
    if not gcs_path.startswith("gs://"):
        raise ValueError("La ruta GCS no es válida (debe empezar con gs://).")

    path_parts = gcs_path[5:].split("/")
    bucket_name = path_parts[0]
    blob_name = "/".join(path_parts[1:])
    
    local_file_name = f"firmware_{uuid_for_file}.bin"
    target_local_path = os.path.join(target_local_dir, local_file_name)
    os.makedirs(target_local_dir, exist_ok=True)

    try:
        bucket = storage_client_gcs.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        print(f"Descargando {gcs_path} a {target_local_path}...")
        blob.download_to_filename(target_local_path)
        print(f"Binario descargado exitosamente a {target_local_path}.")
        return target_local_path
    except Exception as e:
        print(f"Error descargando binario {gcs_path} desde GCS: {e}")
        if os.path.exists(target_local_dir):
            shutil.rmtree(target_local_dir, ignore_errors=True)
        raise

def program_esp32_device(serial_port, firmware_path, baud_rate="115200"):
    if not os.path.isfile(firmware_path):
        raise FileNotFoundError(f"El archivo de firmware no se encuentra en: {firmware_path}")

    command_simple = [
        "--port", serial_port, "--baud", baud_rate,
        "write_flash", 
        "--flash_mode", "dio",
        "--flash_size", "4MB",
        "0x10000", firmware_path
    ]
    
    print(f"Ejecutando esptool con: {command_simple}")
    try:
        old_argv = sys.argv
        sys.argv = ["esptool.py"] + command_simple 
        esptool_main()
        sys.argv = old_argv
        print(f"Programación en puerto {serial_port} con {firmware_path} completada.")
        return "Dispositivo programado exitosamente."
    except SystemExit as e:
        if e.code == 0:
            print(f"Programación (SystemExit 0) en puerto {serial_port} con {firmware_path} completada.")
            return "Dispositivo programado exitosamente."
        else:
            print(f"esptool.py falló con SystemExit código {e.code} durante la programación.")
            raise Exception(f"Fallo de esptool.py (código {e.code})")
    except Exception as e:
        print(f"Error durante la ejecución de esptool.py: {e}")
        raise

def listen_to_params_job_status(user_for_job, uuid_for_job, serial_for_job, port_for_device):
    global current_params_job_status, firestore_listeners # is_params_programming no se modifica aquí

    document_path = f"logs/{serial_for_job}/{user_for_job}/{uuid_for_job}"
    listener_key = f"params_{document_path}"
    print(f"Intentando escuchar logs para Job de Parámetros en: {document_path}")

    if listener_key in firestore_listeners and firestore_listeners[listener_key]:
        print(f"Desuscribiendo listener previo para {listener_key}")
        try:
            firestore_listeners[listener_key].unsubscribe()
        except Exception as e:
            print(f"Error al desuscribir listener para {listener_key}: {e}")
        del firestore_listeners[listener_key]

    def on_snapshot_params(doc_snapshot, changes, read_time):
        global current_params_job_status
        with app.app_context():
            for doc in doc_snapshot:
                if doc.exists:
                    log_data_original = doc.to_dict()
                    serializable_log_data = convert_firestore_timestamps(log_data_original.copy()) # Copiar para no modificar original si se usa después

                    job_status = log_data_original.get("status", "unknown")
                    current_params_job_status = job_status
                    
                    message_to_emit = log_data_original.get("message", f"Estado del job de parámetros: {job_status}")
                    path_to_emit = log_data_original.get("path", None)
                    
                    socketio.emit("job_status_update", {
                        "status": job_status, 
                        "message": message_to_emit, 
                        "path": path_to_emit, 
                        "log_data": serializable_log_data
                    })
                    print(f"Job Parámetros (Firestore) - User: {user_for_job}, UUID: {uuid_for_job}, Status: {job_status}")

                    if job_status == "completed":
                        print("Job de compilación de parámetros completado. Iniciando programación física.")
                        socketio.emit("job_status_update", {"status": "Programando Dispositivo", "message": "Firmware de parámetros compilado, iniciando flasheo..."})
                        temp_firmware_dir = os.path.join(BASE_DIR, "temp_firmware_files")
                        downloaded_firmware_path = None
                        try:
                            binary_gcs_path = log_data_original.get("path")
                            if not binary_gcs_path:
                                raise ValueError("No se encontró la ruta del binario ('path') en los logs del job de parámetros.")
                            
                            downloaded_firmware_path = download_gcs_binary(binary_gcs_path, temp_firmware_dir, uuid_for_job)
                            program_esp32_device(port_for_device, downloaded_firmware_path)
                            
                            final_status_msg = f"Programación de parámetros para serial {serial_for_job} completada."
                            socketio.emit("job_status_update", {"status": "Finalizado", "message": final_status_msg})
                            current_params_job_status = "Finalizado" # Asegurar que se setea antes de resetear
                            reset_params_job_state() # Esto desuscribirá o permitirá una nueva suscripción
                            
                        except Exception as e_prog:
                            error_msg_prog = f"Error durante programación física (parámetros): {str(e_prog)}"
                            print(error_msg_prog)
                            socketio.emit("job_status_update", {"status": "Error Programación Física", "message": error_msg_prog})
                            current_params_job_status = "Error Programación Física" # Asegurar
                            reset_params_job_state()
                        finally:
                            if downloaded_firmware_path and os.path.exists(downloaded_firmware_path):
                                os.remove(downloaded_firmware_path)
                            if os.path.exists(temp_firmware_dir):
                                shutil.rmtree(temp_firmware_dir, ignore_errors=True)
                        return # Salir del callback una vez procesado 'completed' o error de programación

                    elif job_status in ["failed", "auth_failed", "blocked", "error"]:
                        print(f"Job de compilación de parámetros falló o fue bloqueado: {message_to_emit}")
                        reset_params_job_state()
                        return # Salir del callback
                else:
                    print(f"Documento {document_path} (parámetros) ya no existe o fue eliminado.")
                    # No resetear estado aquí, podría ser un evento intermedio o el doc se crea después.
                    # El reset de estado se maneja en los finales (completed, failed) o si el listener falla al crear.
    
    try:
        listener = db.document(document_path).on_snapshot(on_snapshot_params)
        firestore_listeners[listener_key] = listener
    except Exception as e:
        print(f"Error Crítico: No se pudo crear listener de Firestore para {document_path}: {e}")
        socketio.emit("job_status_update", {"status": "Error Interno", "message": "Fallo al monitorear logs."})
        reset_params_job_state()


def listen_to_set_serial_job_status(user_for_job, uuid_for_job, serial_to_program, port_for_device):
    global current_set_serial_job_status, firestore_listeners # is_set_serial_programming no se modifica aquí

    document_path = f"logs_set_serial/{serial_to_program}/{user_for_job}/{uuid_for_job}"
    listener_key = f"set_serial_{document_path}"
    print(f"Intentando escuchar logs para Job de Seteo de Serial en: {document_path}")

    if listener_key in firestore_listeners and firestore_listeners[listener_key]:
        print(f"Desuscribiendo listener previo para {listener_key}")
        try:
            firestore_listeners[listener_key].unsubscribe()
        except Exception as e:
            print(f"Error al desuscribir listener para {listener_key}: {e}")
        del firestore_listeners[listener_key]

    def on_snapshot_set_serial(doc_snapshot, changes, read_time):
        global current_set_serial_job_status
        with app.app_context():
            for doc in doc_snapshot:
                if doc.exists:
                    log_data_original = doc.to_dict()
                    serializable_log_data = convert_firestore_timestamps(log_data_original.copy())

                    job_status = log_data_original.get("status", "unknown")
                    current_set_serial_job_status = job_status

                    message_to_emit = log_data_original.get("message", f"Estado del job de seteo de serial: {job_status}")
                    binary_gcs_path_from_log = log_data_original.get("binary_path")

                    socketio.emit("set_serial_job_status_update", {
                        "status": job_status, 
                        "message": message_to_emit, 
                        "path": binary_gcs_path_from_log, 
                        "log_data": serializable_log_data
                    })
                    print(f"Job Seteo Serial (Firestore) - User: {user_for_job}, UUID: {uuid_for_job}, Status: {job_status}")

                    if job_status == "completed":
                        print(f"Firmware para programar serial {serial_to_program} está listo. Iniciando programación física.")
                        socketio.emit("set_serial_job_status_update", {"status": "Programando Dispositivo", "message": f"Firmware para serial {serial_to_program} compilado, iniciando flasheo..."})
                        
                        temp_firmware_dir = os.path.join(BASE_DIR, "temp_set_serial_firmware_files")
                        downloaded_firmware_path = None
                        try:
                            if not binary_gcs_path_from_log:
                                raise ValueError("No se encontró la ruta del binario ('binary_path') en los logs del job de seteo de serial.")
                            
                            downloaded_firmware_path = download_gcs_binary(binary_gcs_path_from_log, temp_firmware_dir, uuid_for_job)
                            program_esp32_device(port_for_device, downloaded_firmware_path)
                            
                            final_status_msg = f"Programación del nuevo serial {serial_to_program} en el dispositivo completada."
                            socketio.emit("set_serial_job_status_update", {"status": "Programación de Serial Completa", "message": final_status_msg})
                            current_set_serial_job_status = "Programación de Serial Completa" # Asegurar
                            reset_set_serial_job_state()

                        except Exception as e_prog:
                            error_msg_prog = f"Error durante programación física (seteo serial): {str(e_prog)}"
                            print(error_msg_prog)
                            socketio.emit("set_serial_job_status_update", {"status": "Error Programación Física", "message": error_msg_prog})
                            current_set_serial_job_status = "Error Programación Física" # Asegurar
                            reset_set_serial_job_state()
                        finally:
                            if downloaded_firmware_path and os.path.exists(downloaded_firmware_path):
                                os.remove(downloaded_firmware_path)
                            if os.path.exists(temp_firmware_dir):
                                shutil.rmtree(temp_firmware_dir, ignore_errors=True)
                        return # Salir del callback

                    elif job_status in ["failed", "auth_failed", "error"]:
                        print(f"Job de compilación para setear serial falló: {message_to_emit}")
                        reset_set_serial_job_state()
                        return # Salir del callback
                else:
                    print(f"Documento {document_path} (seteo serial) ya no existe o fue eliminado.")
    try:
        listener = db.document(document_path).on_snapshot(on_snapshot_set_serial)
        firestore_listeners[listener_key] = listener
    except Exception as e:
        print(f"Error Crítico: No se pudo crear listener de Firestore para {document_path} (seteo serial): {e}")
        socketio.emit("set_serial_job_status_update", {"status": "Error Interno", "message": "Fallo al monitorear logs."})
        reset_set_serial_job_state()

# --- Rutas Flask ---
@app.route("/login", methods=["GET"])
def login_page():
    return render_template("login.html", api_key=CIBTRON_FIREBASE_API_KEY)

@app.route("/")
def index():
    id_token = request.cookies.get("idToken")
    if not id_token:
        return redirect("/login")
    user = verify_firebase_id_token(id_token)
    if user:
        return render_template("index.html")
    else:
        return redirect("/login")

@app.route("/set_token", methods=["POST"])
def set_token():
    data = request.json
    id_token = data.get("idToken")
    if not id_token:
        return jsonify({"status": "error", "message": "Falta el token"}), 400
    decoded_token = verify_firebase_id_token(id_token)
    if decoded_token:
        response = jsonify({"status": "success", "message": "Token establecido"})
        response.set_cookie("idToken", id_token, httponly=True, samesite='Lax', secure=request.is_secure)
        return response
    else:
        return jsonify({"status": "error", "message": "Token inválido"}), 401

@app.route("/logout", methods=["POST"])
def logout_route(): # Renombrada para evitar conflicto con `logout` si fuera una variable/módulo importado
    response = jsonify({"status": "success", "message": "Sesión cerrada"})
    response.set_cookie("idToken", "", httponly=True, expires=0, samesite='Lax', secure=request.is_secure)
    return response

@app.route("/execute_and_program", methods=["POST"])
def execute_and_program_route():
    global current_params_job_status, params_monitor_thread, is_params_programming, current_params_job_numero_serial
    
    id_token = request.cookies.get("idToken")
    verified_user = verify_firebase_id_token(id_token)
    if not verified_user:
        return jsonify({"status": "error", "message": "Usuario no autenticado o sesión inválida."}), 403

    if is_params_programming:
        return jsonify({"status": "error", "message": "Ya hay un trabajo de programación de parámetros en curso."}), 400

    try:
        is_params_programming = True
        current_params_job_status = "Iniciando"
        
        params_from_form = request.form.to_dict()
        port_for_device = params_from_form.get("port")
        current_params_job_numero_serial = params_from_form.get("NUMERO_SERIAL")
        user_for_job = params_from_form.get("USER")
        uuid_for_job = params_from_form.get("UUID")

        if not all([port_for_device, current_params_job_numero_serial, user_for_job, uuid_for_job]):
            reset_params_job_state()
            return jsonify({"status": "error", "message": "Faltan parámetros requeridos del formulario."}), 400
        
        project_id = "wavesbyte-taximetro"
        region = "us-central1"
        job_name_params = "esp32-compiler"

        socketio.emit("job_status_update", {"status": "Ejecutando Job Remoto", "message": "Compilando firmware de parámetros..."})
        current_params_job_status = "Ejecutando Job Remoto"

        run_cloud_run_job_via_rest(project_id, region, job_name_params, params_from_form)

        params_monitor_thread = socketio.start_background_task(
            listen_to_params_job_status,
            user_for_job,
            uuid_for_job,
            current_params_job_numero_serial,
            port_for_device
        )
        return jsonify({
            "status": "success",
            "message": "Cloud Run Job para parámetros iniciado. Monitoreando Firebase.",
            "uuid": uuid_for_job
        })

    except Exception as e:
        error_message = f"Error al ejecutar job de parámetros: {str(e)}"
        print(error_message)
        reset_params_job_state()
        socketio.emit("job_status_update", {"status": "Error", "message": error_message})
        return jsonify({"status": "error", "message": error_message}), 500


@app.route("/execute_set_serial_job", methods=["POST"])
def execute_set_serial_job_route():
    global current_set_serial_job_status, set_serial_monitor_thread, is_set_serial_programming

    id_token = request.cookies.get("idToken")
    verified_user = verify_firebase_id_token(id_token)
    if not verified_user:
        return jsonify({"status": "error", "message": "Usuario no autenticado o sesión inválida."}), 403

    if is_set_serial_programming:
        return jsonify({"status": "error", "message": "Ya hay un trabajo de programación de nuevo serial en curso."}), 400

    try:
        is_set_serial_programming = True
        current_set_serial_job_status = "Iniciando"

        numero_serial_a_programar = request.form.get("NUMERO_SERIAL_A_PROGRAMAR")
        clave_acceso = request.form.get("CLAVE_ACCESO")
        user_for_job = request.form.get("USER")
        uuid_for_job = request.form.get("UUID")
        port_for_device = request.form.get("port")

        if not all([numero_serial_a_programar, clave_acceso, user_for_job, uuid_for_job, port_for_device]):
            reset_set_serial_job_state()
            return jsonify({"status": "error", "message": "Faltan parámetros para programar el nuevo serial."}), 400
        
        if len(clave_acceso) != 10:
            reset_set_serial_job_state()
            return jsonify({"status": "error", "message": "La clave de acceso debe tener 10 caracteres."}), 400

        project_id = "wavesbyte-taximetro"
        region = "us-central1"
        job_name_set_serial = "esp32-set-serial-compiler"

        env_params_for_set_serial_job = {
            "NUMERO_SERIAL_A_PROGRAMAR": numero_serial_a_programar,
            "CLAVE_ACCESO": clave_acceso,
            "USER": user_for_job,
            "UUID": uuid_for_job,
        }
        
        socketio.emit("set_serial_job_status_update", {"status": "Ejecutando Job Remoto", "message": "Compilando firmware para programar nuevo serial..."})
        current_set_serial_job_status = "Ejecutando Job Remoto"

        run_cloud_run_job_via_rest(project_id, region, job_name_set_serial, env_params_for_set_serial_job)

        set_serial_monitor_thread = socketio.start_background_task(
            listen_to_set_serial_job_status,
            user_for_job,
            uuid_for_job,
            numero_serial_a_programar,
            port_for_device
        )
        return jsonify({
            "status": "success",
            "message": f"Cloud Run Job '{job_name_set_serial}' para programar serial {numero_serial_a_programar} iniciado. UUID: {uuid_for_job}.",
            "uuid": uuid_for_job,
            "serial_programmed": numero_serial_a_programar
        })

    except Exception as e:
        error_message = f"Error al ejecutar job de seteo de serial: {str(e)}"
        print(error_message)
        reset_set_serial_job_state()
        socketio.emit("set_serial_job_status_update", {"status": "Error", "message": error_message})
        return jsonify({"status": "error", "message": error_message}), 500

@app.route("/search_serial", methods=["GET"])
def search_serial_route():
    serial_number = request.args.get("serial_number")
    if not serial_number:
        return jsonify({"status": "error", "message": "Número serial no proporcionado."}), 400
    try:
        from lector_firestore import get_most_recent_document_by_serial 
        result = get_most_recent_document_by_serial(serial_number)
        if result:
            # Convertir timestamps antes de enviar JSON
            serializable_result = convert_firestore_timestamps(result)
            return jsonify({"status": "success", "data": serializable_result})
        else:
            return jsonify({"status": "error", "message": "No se encontró información para este número serial."}), 404
    except ModuleNotFoundError:
        print("Error: Módulo 'lector_firestore' no encontrado.")
        return jsonify({"status": "error", "message": "Error interno del servidor (lector)."}), 500
    except Exception as e:
        print(f"Error en /search_serial: {str(e)}")
        return jsonify({"status": "error", "message": f"Error interno: {str(e)}"}), 500

@app.route("/search_certificates", methods=["GET"])
def search_certificates_route():
    serial_number = request.args.get("serial_number")
    if not serial_number:
        return jsonify({"status": "error", "message": "Número serial no proporcionado."}), 400
    try:
        from lector_firestore2 import get_all_documents_by_serial
        results = get_all_documents_by_serial(serial_number)
        # Convertir timestamps en cada documento
        serializable_results = [convert_firestore_timestamps(doc) for doc in results] if results else []
        return jsonify({"status": "success", "data": serializable_results})
    except ModuleNotFoundError:
        print("Error: Módulo 'lector_firestore2' no encontrado.")
        return jsonify({"status": "error", "message": "Error interno del servidor (lector2)."}), 500
    except Exception as e:
        print(f"Error en /search_certificates: {str(e)}")
        return jsonify({"status": "error", "message": f"Error interno: {str(e)}"}), 500

@app.route("/get_ports", methods=["GET"])
def get_ports_route():
    try:
        ports_raw = serial.tools.list_ports.comports()
        ids_conocidos = ["1A86:7523", "10C4:EA60", "0403:6001", "067B:2303"]
        filtered_ports = [
            {"device": p.device, "description": p.description, "hwid": p.hwid}
            for p in ports_raw
            if any(id_conocido in p.hwid.upper() for id_conocido in ids_conocidos)
        ]
        return jsonify({"status": "success", "ports": filtered_ports})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/get_serial_number", methods=["POST"])
def get_serial_number_from_device_route():
    data = request.json
    port = data.get("port")
    if not port:
        return jsonify({"status": "error", "message": "Puerto no proporcionado."}), 400
    try:
        baudrate = data.get("baudrate", 115200)
        timeout = data.get("timeout", 5)
        serial_number_device = restart_and_get_value(port, baudrate, timeout)
        if serial_number_device:
            return jsonify({"status": "success", "serial_number": serial_number_device.upper()})
        else:
            return jsonify({"status": "error", "message": "No se pudo obtener el número de serie del dispositivo."}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/check_port_status", methods=["POST"])
def check_port_status_route():
    data = request.json
    port_to_check = data.get("port")
    if not port_to_check:
        return jsonify({"status": "error", "message": "Puerto no proporcionado."}), 400
    try:
        available_ports = [p.device for p in serial.tools.list_ports.comports()]
        is_connected = port_to_check in available_ports
        return jsonify({"status": "success", "connected": is_connected})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/get_job_status", methods=["GET"])
def get_params_job_status_route():
    global current_params_job_status
    return jsonify({"status": current_params_job_status})

@app.route("/get_set_serial_job_status", methods=["GET"])
def get_set_serial_job_status_route():
    global current_set_serial_job_status
    return jsonify({"status": current_set_serial_job_status})


@app.route("/get_user_data", methods=["GET"])
def get_user_data_route():
    id_token = request.cookies.get("idToken")
    user = verify_firebase_id_token(id_token)
    if user and user.get("email"):
        return jsonify({"email": user["email"]}), 200
    return jsonify({"error": "Usuario no autenticado o email no disponible"}), 401

@app.route("/resetcibtron", methods=["POST"])
def reset_cibtron_firmware_route():
    port = request.form.get("port")
    if not port:
        return jsonify({"status": "error", "message": "Puerto no proporcionado"}), 400
    if not os.path.exists(FIRMWARE_RESET_PATH):
        return jsonify({"status": "error", "message": f"Firmware de reseteo no encontrado en {FIRMWARE_RESET_PATH}"}), 500
    try:
        resetcibtron(port, FIRMWARE_RESET_PATH)
        return jsonify({"status": "success", "message": "Reset de firmware completado con éxito"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error al realizar el reset: {str(e)}"}), 500

if __name__ == "__main__":
    print(f"Iniciando Flask app en modo: {'Producción (gunicorn/waitress)' if os.environ.get('WERKZEUG_RUN_MAIN') else 'Desarrollo (Flask dev server)'}")
    print(f"Sirviendo desde BASE_DIR: {BASE_DIR}")
    print(f"Usando archivo de credenciales: {CIBTRON_CRED_FILE}")
    print(f"Usando archivo de firmware de reset: {FIRMWARE_RESET_PATH}")
    
    temp_dirs_to_clean = [
        os.path.join(BASE_DIR, "temp_firmware_files"),
        os.path.join(BASE_DIR, "temp_set_serial_firmware_files")
    ]
    for temp_dir in temp_dirs_to_clean:
        if os.path.exists(temp_dir):
            print(f"Limpiando directorio temporal: {temp_dir}")
            shutil.rmtree(temp_dir, ignore_errors=True)
            
    socketio.run(app, host="127.0.0.1", port=5001, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)