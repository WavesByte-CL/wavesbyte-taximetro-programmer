from fillpdf import fillpdfs

# Ruta al archivo PDF de entrada y salida
input_pdf_path = "templatewb.pdf"
output_pdf_path = "filled_form.pdf"

# Datos a rellenar en el formulario
data_dict = {
    "divisor": "4",
    "resolucion": "1000.0",
    "tarifa1": "450/200",
    "tarifa2": "450/200",
    "tarifa3": "450/200",
    "fecha1": "2023-10-01",
    "fecha2": "2023-10-01",
    "fecha3": "2023-10-01",
    "fecha4": "2023-10-01",
    "marca1": "Marca1",
    "marca2": "Marca2",
    "marca3": "Marca3",
    "patente1": "ABC123",
    "patente2": "DEF456",
    "patente3": "GHI789",
    "n_sello1": "Sello1",
    "n_sello2": "Sello2",
    "n_sello3": "Sello3",
    "n_serie1": "Serie1",
    "n_serie2": "Serie2",
    "n_serie3": "Serie3",
    "observacion1": "Observaciones1",
    "observacion2": "Observaciones2",
    "nombre_propietario": "Propietario1"
}

# Obtener los campos del formulario para verificar nombres de campos
fields = fillpdfs.get_form_fields(input_pdf_path)
print("Campos disponibles en el formulario:")
print(fields)

# Rellenar el formulario con los datos proporcionados
fillpdfs.write_fillable_pdf(input_pdf_path, output_pdf_path, data_dict)

# (Opcional) Hacer que el PDF no sea editable
fillpdfs.write_fillable_pdf(input_pdf_path, output_pdf_path, data_dict, flatten=True)

print(f"Formulario rellenado guardado en: {output_pdf_path}")
