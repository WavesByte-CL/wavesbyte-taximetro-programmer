from flask import Flask, render_template, request, jsonify, redirect
from flask_socketio import SocketIO, send # send no se usa, se podría quitar
import os
import sys
from esptool import main as esptool_main # Usado para programar ESP32
import serial.tools.list_ports
from google.cloud import storage
import threading # Usado por socketio.start_background_task
import time
import subprocess # Usado en run_cloud_run_job_with_env (aunque run_job_with_rest_api es preferible)
from datetime import datetime
from dotenv import load_dotenv
from serial_reader import restart_and_get_value # Asumo que este archivo existe en tu proyecto
# from eventlet.hubs import epolls, kqueue, selects # No parecen estar en uso directo
# import eventlet # No parece estar en uso directo
from resetcibtron import resetcibtron # Asumo que este archivo existe
import json
import base64
from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleAuthRequest # Renombrado para evitar colisión con flask.Request
import requests
import shutil # Para borrar directorios temporales de manera robusta
import firebase_admin # <--- AÑADIR ESTA LÍNEA
from firebase_admin import credentials, firestore, initialize_app, auth


# --- Configuración Inicial y Paths ---
def get_base_dir():
    if getattr(sys, "frozen", False): # PyInstaller
        return sys._MEIPASS
    else: # Ejecución normal
        return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_dir()
CIBTRON_CRED_FILE = os.path.join(BASE_DIR, "cibtron.txt") # Renombrado para claridad
DOTENV_PATH = os.path.join(BASE_DIR, ".env")
FIRMWARE_RESET_PATH = os.path.join(BASE_DIR, "leer_serial_memoria.ino.bin") # Para la función de reset

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

# Inicializar Firebase Admin
try:
    # Verificar si ya está inicializada para evitar error
    if not firebase_admin._apps: # Acceso no oficial, pero común. Mejor sería un flag global.
        cred = credentials.Certificate(DECODED_SERVICE_ACCOUNT_CREDS)
        initialize_app(cred)
        print("Firebase Admin SDK inicializado.")
    else:
        print("Firebase Admin SDK ya estaba inicializado.")
except Exception as e:
    print(f"Error al inicializar Firebase Admin SDK: {e}")
    # No salir si ya estaba inicializado, pero sí si es un error nuevo.
    if "already initialized" not in str(e).lower():
        sys.exit(1)

db = firestore.client() # Cliente Firestore
storage_client_gcs = storage.Client.from_service_account_info(DECODED_SERVICE_ACCOUNT_CREDS) # Cliente GCS

# --- Configuración de Flask y SocketIO ---
app = Flask(__name__)
# app.secret_key = os.urandom(24) # Necesario si usas flask.session, no parece ser el caso aquí
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True, async_mode='threading')


# --- Estado Global de la Aplicación ---
# Para el job original de programar parámetros
current_params_job_status = "Listo"
params_monitor_thread = None
is_params_programming = False
current_params_job_numero_serial = None # Para saber qué serial se está procesando

# NUEVO: Para el job de programar/setear el serial
current_set_serial_job_status = "Listo"
set_serial_monitor_thread = None
is_set_serial_programming = False

# Diccionario para manejar los listeners de Firestore y poder desuscribirlos
firestore_listeners = {}

# --- Funciones de Reseteo de Estado ---
def reset_params_job_state():
    global current_params_job_status, is_params_programming, params_monitor_thread, current_params_job_numero_serial
    current_params_job_status = "Listo"
    is_params_programming = False
    current_params_job_numero_serial = None
    # La desuscripción del listener se maneja en `listen_to_job_status` al inicio
    params_monitor_thread = None # Solo resetea la referencia al hilo
    print("Estado del job de programación de parámetros reseteado.")
    socketio.emit("job_status_update", {"status": current_params_job_status})

def reset_set_serial_job_state():
    global current_set_serial_job_status, is_set_serial_programming, set_serial_monitor_thread
    current_set_serial_job_status = "Listo"
    is_set_serial_programming = False
    # La desuscripción del listener se maneja en `listen_to_set_serial_job_status` al inicio
    set_serial_monitor_thread = None
    print("Estado del job de programación de nuevo serial reseteado.")
    socketio.emit("set_serial_job_status_update", {"status": current_set_serial_job_status})


# --- Funciones de Autenticación y Tokens ---
def generate_gcp_access_token():
    try:
        creds = service_account.Credentials.from_service_account_info(DECODED_SERVICE_ACCOUNT_CREDS)
        scoped_creds = creds.with_scopes(["https://www.googleapis.com/auth/cloud-platform"])
        auth_req = GoogleAuthRequest() # Usar el nombre completo para evitar colisión
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

# --- Funciones para Interactuar con Cloud Run Jobs ---
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
        response.raise_for_status() # Lanza HTTPError para respuestas 4xx/5xx
        print(f"Respuesta de ejecución de Job {job_name}: {response.status_code}")
        return response.json()
    except requests.exceptions.RequestException as e:
        error_detail = e.response.text if e.response is not None else str(e)
        print(f"Error en la petición HTTP al ejecutar Cloud Run Job {job_name}: {error_detail}")
        raise Exception(f"Error HTTP ejecutando job: {error_detail}")
    except Exception as e:
         print(f"Error genérico al ejecutar Cloud Run Job {job_name} con API REST: {e}")
         raise


# --- Funciones de Manejo de Archivos y Programación ESP32 ---
def download_gcs_binary(gcs_path, target_local_dir, uuid_for_file):
    """Descarga un binario de GCS a una ruta local única."""
    if not gcs_path.startswith("gs://"):
        raise ValueError("La ruta GCS no es válida (debe empezar con gs://).")

    path_parts = gcs_path[5:].split("/")
    bucket_name = path_parts[0]
    blob_name = "/".join(path_parts[1:])
    
    # Crear nombre de archivo local único para evitar colisiones
    local_file_name = f"firmware_{uuid_for_file}.bin"
    target_local_path = os.path.join(target_local_dir, local_file_name)
    os.makedirs(target_local_dir, exist_ok=True) # Asegurar que el directorio exista

    try:
        bucket = storage_client_gcs.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        print(f"Descargando {gcs_path} a {target_local_path}...")
        blob.download_to_filename(target_local_path)
        print(f"Binario descargado exitosamente a {target_local_path}.")
        return target_local_path
    except Exception as e:
        print(f"Error descargando binario {gcs_path} desde GCS: {e}")
        raise

def program_esp32_device(serial_port, firmware_path, baud_rate="115200"):
    """Programa el ESP32 usando esptool.py."""
    if not os.path.isfile(firmware_path):
        raise FileNotFoundError(f"El archivo de firmware no se encuentra en: {firmware_path}")

    # Comando para esptool.py
    # Nota: flash_size puede variar según el ESP32. '4MB' es común.
    # Las direcciones de memoria (0x10000 para firmware principal) son estándar para ESP32.
    command = [
        "--chip", "esp32", # Especificar chip puede ayudar
        "--port", serial_port,
        "--baud", baud_rate,
        "write_flash",
        "--flash_mode", "dio",
        "--flash_freq", "40m", # Frecuencia común, puede ser '80m'
        "--flash_size", "4MB", # O "detect" si esptool lo soporta bien
        "0x1000", os.path.join(BASE_DIR, "bootloader.bin"), # Ejemplo: si necesitas bootloader
        "0x8000", os.path.join(BASE_DIR, "partitions.bin"), # Ejemplo: si necesitas tabla de particiones
        "0x10000", firmware_path # Firmware principal
    ]
    # Simplificado si no necesitas bootloader/partitions específicos (tu esptool_main original no los usaba)
    command_simple = [
        "--port", serial_port, "--baud", baud_rate,
        "write_flash", 
        "--flash_mode", "dio", # Mantenido de tu código original
        "--flash_size", "4MB", # Mantenido de tu código original
        "0x10000", firmware_path
    ]
    
    print(f"Ejecutando esptool con: {command_simple}")
    try:
        # Guardar y restaurar sys.argv es una forma de usar esptool.py como librería
        old_argv = sys.argv
        sys.argv = ["esptool.py"] + command_simple 
        esptool_main() # Llama a la función main de esptool
        sys.argv = old_argv # Restaurar
        print(f"Programación en puerto {serial_port} con {firmware_path} completada.")
        # Opcional: Eliminar el archivo de firmware descargado después de la programación
        # os.remove(firmware_path)
        # print(f"Archivo de firmware temporal {firmware_path} eliminado.")
        return "Dispositivo programado exitosamente."
    except SystemExit as e: # esptool.py puede hacer sys.exit()
        if e.code == 0:
            print(f"Programación (SystemExit 0) en puerto {serial_port} con {firmware_path} completada.")
            return "Dispositivo programado exitosamente."
        else:
            print(f"esptool.py falló con SystemExit código {e.code} durante la programación.")
            raise Exception(f"Fallo de esptool.py (código {e.code})")
    except Exception as e:
        print(f"Error durante la ejecución de esptool.py: {e}")
        raise

# --- Listeners de Firestore para Estado de Jobs ---

def listen_to_params_job_status(user_for_job, uuid_for_job, serial_for_job, port_for_device):
    global current_params_job_status, is_params_programming, firestore_listeners

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
        global current_params_job_status, is_params_programming
        with app.app_context():
            for doc in doc_snapshot:
                if doc.exists:
                    log_data = doc.to_dict()
                    job_status = log_data.get("status", "unknown")
                    current_params_job_status = job_status
                    
                    message_to_emit = log_data.get("message", f"Estado del job de parámetros: {job_status}")
                    path_to_emit = log_data.get("path", None)
                    
                    socketio.emit("job_status_update", {"status": job_status, "message": message_to_emit, "path": path_to_emit, "log_data": log_data})
                    print(f"Job Parámetros (Firestore) - User: {user_for_job}, UUID: {uuid_for_job}, Status: {job_status}")

                    if job_status == "completed": # Job de compilación de firmware de parámetros terminó
                        print("Job de compilación de parámetros completado. Iniciando programación física.")
                        socketio.emit("job_status_update", {"status": "Programando Dispositivo", "message": "Firmware de parámetros compilado, iniciando flasheo..."})
                        try:
                            binary_gcs_path = log_data.get("path")
                            if not binary_gcs_path:
                                raise ValueError("No se encontró la ruta del binario en los logs del job de parámetros.")
                            
                            temp_firmware_dir = os.path.join(BASE_DIR, "temp_firmware_files")
                            downloaded_firmware_path = download_gcs_binary(binary_gcs_path, temp_firmware_dir, uuid_for_job)
                            
                            program_esp32_device(port_for_device, downloaded_firmware_path)
                            
                            final_status_msg = f"Programación de parámetros para serial {serial_for_job} completada."
                            socketio.emit("job_status_update", {"status": "Finalizado", "message": final_status_msg})
                            current_params_job_status = "Finalizado"
                            if os.path.exists(downloaded_firmware_path): os.remove(downloaded_firmware_path) # Limpiar
                            reset_params_job_state() # Resetea el estado global para este job
                            
                        except Exception as e_prog:
                            error_msg_prog = f"Error durante programación física (parámetros): {str(e_prog)}"
                            print(error_msg_prog)
                            socketio.emit("job_status_update", {"status": "Error Programación Física", "message": error_msg_prog})
                            current_params_job_status = "Error Programación Física"
                            reset_params_job_state()

                    elif job_status in ["failed", "auth_failed", "blocked", "error"]:
                        print(f"Job de compilación de parámetros falló o fue bloqueado: {message_to_emit}")
                        reset_params_job_state()
                        # El listener se desuscribe automáticamente al resetear o al inicio de la próxima escucha
                else:
                    print(f"Documento {document_path} (parámetros) ya no existe.")
    
    try:
        listener = db.document(document_path).on_snapshot(on_snapshot_params)
        firestore_listeners[listener_key] = listener
    except Exception as e:
        print(f"Error Crítico: No se pudo crear listener de Firestore para {document_path}: {e}")
        socketio.emit("job_status_update", {"status": "Error Interno", "message": "Fallo al monitorear logs."})
        reset_params_job_state()


# NUEVA función de escucha para el job de SETEAR SERIAL
def listen_to_set_serial_job_status(user_for_job, uuid_for_job, serial_to_program, port_for_device):
    global current_set_serial_job_status, is_set_serial_programming, firestore_listeners

    # Ruta en Firestore para los logs del job de setear serial
    document_path = f"logs_set_serial/{serial_to_program}/{user_for_job}/{uuid_for_job}"
    listener_key = f"set_serial_{document_path}" # Clave única para este tipo de listener
    print(f"Intentando escuchar logs para Job de Seteo de Serial en: {document_path}")

    if listener_key in firestore_listeners and firestore_listeners[listener_key]:
        print(f"Desuscribiendo listener previo para {listener_key}")
        try:
            firestore_listeners[listener_key].unsubscribe()
        except Exception as e:
            print(f"Error al desuscribir listener para {listener_key}: {e}")
        del firestore_listeners[listener_key]

    def on_snapshot_set_serial(doc_snapshot, changes, read_time):
        global current_set_serial_job_status, is_set_serial_programming
        with app.app_context():
            for doc in doc_snapshot:
                if doc.exists:
                    log_data = doc.to_dict()
                    job_status = log_data.get("status", "unknown")
                    current_set_serial_job_status = job_status # Actualiza estado global específico

                    message_to_emit = log_data.get("message", f"Estado del job de seteo de serial: {job_status}")
                    path_to_emit = log_data.get("binary_path", None)

                    # Emitir al canal de SocketIO específico
                    socketio.emit("set_serial_job_status_update", {
                        "status": job_status, 
                        "message": message_to_emit, 
                        "path": path_to_emit, 
                        "log_data": log_data # Para que el cliente pueda usar numero_serial_a_programar
                    })
                    print(f"Job Seteo Serial (Firestore) - User: {user_for_job}, UUID: {uuid_for_job}, Status: {job_status}")

                    if job_status == "completed": # Job de compilación de firmware para setear serial terminó
                        print(f"Firmware para programar serial {serial_to_program} está listo. Iniciando programación física.")
                        socketio.emit("set_serial_job_status_update", {"status": "Programando Dispositivo", "message": f"Firmware para serial {serial_to_program} compilado, iniciando flasheo..."})
                        try:
                            binary_gcs_path = log_data.get("binary_path") # `binary_path` es la key en logs_set_serial
                            if not binary_gcs_path:
                                raise ValueError("No se encontró la ruta del binario en los logs del job de seteo de serial.")
                            
                            temp_firmware_dir = os.path.join(BASE_DIR, "temp_set_serial_firmware_files")
                            downloaded_firmware_path = download_gcs_binary(binary_gcs_path, temp_firmware_dir, uuid_for_job)
                            
                            program_esp32_device(port_for_device, downloaded_firmware_path)
                            
                            final_status_msg = f"Programación del nuevo serial {serial_to_program} en el dispositivo completada."
                            socketio.emit("set_serial_job_status_update", {"status": "Programación de Serial Completa", "message": final_status_msg})
                            current_set_serial_job_status = "Programación de Serial Completa"
                            if os.path.exists(downloaded_firmware_path): os.remove(downloaded_firmware_path) # Limpiar
                            reset_set_serial_job_state()

                        except Exception as e_prog:
                            error_msg_prog = f"Error durante programación física (seteo serial): {str(e_prog)}"
                            print(error_msg_prog)
                            socketio.emit("set_serial_job_status_update", {"status": "Error Programación Física", "message": error_msg_prog})
                            current_set_serial_job_status = "Error Programación Física"
                            reset_set_serial_job_state()

                    elif job_status in ["failed", "auth_failed", "error"]: # Asumiendo 'error' es un estado de fallo
                        print(f"Job de compilación para setear serial falló: {message_to_emit}")
                        reset_set_serial_job_state()
                else:
                    print(f"Documento {document_path} (seteo serial) ya no existe.")

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
        # No es necesario pasar email y uid al template si se obtienen vía JS con /get_user_data
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
        # httponly=True, samesite='Lax' son buenas prácticas para cookies de sesión
        response.set_cookie("idToken", id_token, httponly=True, samesite='Lax', secure=request.is_secure)
        return response
    else:
        return jsonify({"status": "error", "message": "Token inválido"}), 401

@app.route("/logout", methods=["POST"])
def logout():
    response = jsonify({"status": "success", "message": "Sesión cerrada"})
    response.set_cookie("idToken", "", httponly=True, expires=0, samesite='Lax', secure=request.is_secure)
    return response

@app.route("/execute_and_program", methods=["POST"]) # Job de Parámetros
def execute_and_program_route():
    global current_params_job_status, params_monitor_thread, is_params_programming, current_params_job_numero_serial
    
    # Verificar token de Firebase para asegurar que el usuario está autenticado
    id_token = request.cookies.get("idToken")
    verified_user = verify_firebase_id_token(id_token)
    if not verified_user:
        return jsonify({"status": "error", "message": "Usuario no autenticado o sesión inválida."}), 403

    if is_params_programming:
        return jsonify({"status": "error", "message": "Ya hay un trabajo de programación de parámetros en curso."})

    try:
        is_params_programming = True
        current_params_job_status = "Iniciando"
        
        # Parámetros del formulario
        # Es buena práctica validar y sanitizar estos datos
        params_from_form = request.form.to_dict()
        port_for_device = params_from_form.get("port")
        current_params_job_numero_serial = params_from_form.get("NUMERO_SERIAL") # Guardar para el listener
        user_for_job = params_from_form.get("USER") # Programador
        uuid_for_job = params_from_form.get("UUID")

        if not all([port_for_device, current_params_job_numero_serial, user_for_job, uuid_for_job]):
            reset_params_job_state()
            return jsonify({"status": "error", "message": "Faltan parámetros requeridos del formulario."}), 400
        
        project_id = "wavesbyte-taximetro"
        region = "us-central1"
        job_name_params = "esp32-compiler" # Job para compilar firmware de parámetros
        # args_params_job = ["/workspace/compile_and_upload.py"] # Si CMD en Dockerfile no es suficiente

        socketio.emit("job_status_update", {"status": "Ejecutando Job Remoto", "message": "Compilando firmware de parámetros..."})
        current_params_job_status = "Ejecutando Job Remoto"

        run_cloud_run_job_via_rest(project_id, region, job_name_params, params_from_form) # `params_from_form` contiene todas las variables de entorno

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
        return jsonify({"status": "error", "message": error_message})


# NUEVA RUTA PARA EJECUTAR EL JOB DE SETEAR SERIAL
@app.route("/execute_set_serial_job", methods=["POST"])
def execute_set_serial_job_route():
    global current_set_serial_job_status, set_serial_monitor_thread, is_set_serial_programming

    id_token = request.cookies.get("idToken")
    verified_user = verify_firebase_id_token(id_token)
    if not verified_user:
        return jsonify({"status": "error", "message": "Usuario no autenticado o sesión inválida."}), 403

    if is_set_serial_programming:
        return jsonify({"status": "error", "message": "Ya hay un trabajo de programación de nuevo serial en curso."})

    try:
        is_set_serial_programming = True
        current_set_serial_job_status = "Iniciando"

        numero_serial_a_programar = request.form.get("NUMERO_SERIAL_A_PROGRAMAR")
        clave_acceso = request.form.get("CLAVE_ACCESO")
        user_for_job = request.form.get("USER") # El programador (del campo USER del form, o del token)
        uuid_for_job = request.form.get("UUID") # UUID generado en el cliente
        port_for_device = request.form.get("port") # Puerto para flashear

        if not all([numero_serial_a_programar, clave_acceso, user_for_job, uuid_for_job, port_for_device]):
            reset_set_serial_job_state()
            return jsonify({"status": "error", "message": "Faltan parámetros para programar el nuevo serial."}), 400
        
        # Validar formato de clave_acceso si es necesario (ej. longitud)
        if len(clave_acceso) != 10:
            reset_set_serial_job_state()
            return jsonify({"status": "error", "message": "La clave de acceso debe tener 10 caracteres."}), 400


        project_id = "wavesbyte-taximetro"
        region = "us-central1"
        job_name_set_serial = "esp32-set-serial-compiler" # Nombre del NUEVO Cloud Run Job

        env_params_for_set_serial_job = {
            "NUMERO_SERIAL_A_PROGRAMAR": numero_serial_a_programar,
            "CLAVE_ACCESO": clave_acceso,
            "USER": user_for_job, # Usuario que ejecuta la acción
            "UUID": uuid_for_job, # UUID de esta ejecución particular
            # Variables como CODE_BUCKET_SETSERIAL, etc., se configuran en el Cloud Run Job mismo.
        }
        
        socketio.emit("set_serial_job_status_update", {"status": "Ejecutando Job Remoto", "message": "Compilando firmware para programar nuevo serial..."})
        current_set_serial_job_status = "Ejecutando Job Remoto"

        run_cloud_run_job_via_rest(project_id, region, job_name_set_serial, env_params_for_set_serial_job)

        set_serial_monitor_thread = socketio.start_background_task(
            listen_to_set_serial_job_status,
            user_for_job,
            uuid_for_job,
            numero_serial_a_programar, # Este es el serial que se está programando
            port_for_device            # Puerto para el flasheo físico
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
        return jsonify({"status": "error", "message": error_message})


# --- Rutas de Información y Auxiliares ---
@app.route("/search_serial", methods=["GET"]) # Para rellenar formulario de parámetros
def search_serial_route():
    serial_number = request.args.get("serial_number")
    if not serial_number:
        return jsonify({"status": "error", "message": "Número serial no proporcionado."}), 400
    try:
        # Asumiendo que lector_firestore.py está en el mismo directorio o en PYTHONPATH
        from lector_firestore import get_most_recent_document_by_serial 
        result = get_most_recent_document_by_serial(serial_number)
        if result:
            return jsonify({"status": "success", "data": result})
        else:
            return jsonify({"status": "error", "message": "No se encontró información para este número serial."}), 404
    except ModuleNotFoundError:
        return jsonify({"status": "error", "message": "Módulo 'lector_firestore' no encontrado."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error interno: {str(e)}"}), 500

@app.route("/search_certificates", methods=["GET"]) # Para listar programaciones anteriores
def search_certificates_route():
    serial_number = request.args.get("serial_number")
    if not serial_number:
        return jsonify({"status": "error", "message": "Número serial no proporcionado."}), 400
    try:
        from lector_firestore2 import get_all_documents_by_serial
        results = get_all_documents_by_serial(serial_number) # Devuelve una lista
        return jsonify({"status": "success", "data": results if results else []})
    except ModuleNotFoundError:
        return jsonify({"status": "error", "message": "Módulo 'lector_firestore2' no encontrado."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error interno: {str(e)}"}), 500

@app.route("/get_ports", methods=["GET"])
def get_ports_route():
    try:
        ports_raw = serial.tools.list_ports.comports()
        # Filtrar por VID:PID conocidos para CH340, CP210x, FTDI
        ids_conocidos = ["1A86:7523", "10C4:EA60", "0403:6001", "067B:2303"] # Añadido Prolific
        filtered_ports = [
            {"device": p.device, "description": p.description, "hwid": p.hwid}
            for p in ports_raw
            if any(id_conocido in p.hwid.upper() for id_conocido in ids_conocidos)
        ]
        return jsonify({"status": "success", "ports": filtered_ports})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/get_serial_number", methods=["POST"]) # Desde el dispositivo conectado
def get_serial_number_from_device_route():
    data = request.json
    port = data.get("port")
    if not port:
        return jsonify({"status": "error", "message": "Puerto no proporcionado."}), 400
    try:
        # Valores por defecto si no se especifican
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

@app.route("/get_job_status", methods=["GET"]) # Para el job de parámetros
def get_params_job_status_route():
    global current_params_job_status
    return jsonify({"status": current_params_job_status})

# NUEVA RUTA para el estado del job de setear serial
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
    # firmware_path_param = request.form.get("firmware_path") # Podría ser un parámetro, pero usamos el fijo.
    if not port:
        return jsonify({"status": "error", "message": "Puerto no proporcionado"}), 400
    if not os.path.exists(FIRMWARE_RESET_PATH):
        return jsonify({"status": "error", "message": f"Firmware de reseteo no encontrado en {FIRMWARE_RESET_PATH}"}), 500
    try:
        resetcibtron(port, FIRMWARE_RESET_PATH) # resetcibtron debe manejar la programación
        return jsonify({"status": "success", "message": "Reset de firmware completado con éxito"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error al realizar el reset: {str(e)}"}), 500

# --- Ejecución de la Aplicación ---
if __name__ == "__main__":
    print(f"Iniciando Flask app en modo: {'Producción (gunicorn/waitress)' if os.environ.get('WERKZEUG_RUN_MAIN') else 'Desarrollo (Flask dev server)'}")
    print(f"Sirviendo desde BASE_DIR: {BASE_DIR}")
    print(f"Usando archivo de credenciales: {CIBTRON_CRED_FILE}")
    print(f"Usando archivo de firmware de reset: {FIRMWARE_RESET_PATH}")
    # Para desarrollo: host='0.0.0.0' para acceder desde otros dispositivos en la red local
    # Para producción, esto se maneja por Gunicorn/Waitress.
    # allow_unsafe_werkzeug=True solo es para el servidor de desarrollo de Flask con reloaders.
    socketio.run(app, host="127.0.0.1", port=5001, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)