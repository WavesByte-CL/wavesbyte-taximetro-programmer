import os
import sys
from google.cloud import firestore
from google.oauth2.service_account import Credentials

def initialize_firestore_with_service_account():
    """
    Inicializa Firestore utilizando las credenciales de una cuenta de servicio desde el archivo credentials.json.
    Este método es compatible con PyInstaller.
    """
    try:
        # Asegúrate de que las credenciales estén en el mismo directorio que el script
        base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        credentials_path = os.path.join(base_dir, "credentials.json")

        if not os.path.exists(credentials_path):
            raise FileNotFoundError(f"El archivo de credenciales no se encontró en {credentials_path}")

        # Cargar credenciales y proyecto desde el archivo credentials.json
        credentials = Credentials.from_service_account_file(credentials_path)
        project = credentials.project_id

        db = firestore.Client(credentials=credentials, project=project)
        print("Firestore inicializado correctamente con Service Account.")
        return db
    except Exception as e:
        print(f"Error inicializando Firestore con Service Account: {e}")
        return None

def get_all_documents_by_serial(serial_number):
    """
    Obtiene todos los documentos de todas las subcolecciones dinámicas bajo un documento principal identificado por su serial_number.
    Devuelve un array con todos los documentos ordenados por el campo 'timestamp'.
    """
    try:
        print(f"Obteniendo todos los documentos para el número serial {serial_number}...")
        db = initialize_firestore_with_service_account()
        if not db:
            raise Exception("No se pudo inicializar Firestore.")
        
        document_path = f"logs/{serial_number}"
        document_ref = db.document(document_path)
        subcollections = document_ref.collections()
        all_documents = []

        for subcollection in subcollections:
            print(f"Procesando subcolección: {subcollection.id}")
            docs = subcollection.stream()
            for doc in docs:
                doc_data = {
                    "subcollection_name": subcollection.id,
                    "document_id": doc.id,
                    "document_data": doc.to_dict()
                }
                all_documents.append(doc_data)

        # Ordenar los documentos por el campo 'timestamp'
        sorted_documents = sorted(
            all_documents,
            key=lambda x: x["document_data"].get("timestamp"),
            reverse=True  # Orden descendente, el más reciente primero
        )
        return sorted_documents
    except Exception as e:
        print(f"Error al obtener todos los documentos: {e}")
        return []

# Ejemplo de uso
if __name__ == "__main__":
    serial_number = "000006"  # Este es el parámetro que irá variando
    result = get_all_documents_by_serial(serial_number)
    if result:
        print("Todos los documentos encontrados (ordenados por timestamp):")
        for doc in result:
            print(doc)
    else:
        print("No se encontró ningún documento.")
