// Intenta obtener el socket globalmente si aún no está definido.
// Esto es útil si partes del script se cargan condicionalmente o en diferente orden.
const socket = typeof io !== "undefined" ? io() : null;

let isProgramFinished = false;
let isReloading = false;
let lastStatus = null; // Para rastrear el último estado del job de parámetros
let lastSetSerialStatus = null; // Para rastrear el último estado del job de seteo de serial
let selectedPort = null; // Puerto seleccionado en el formulario principal

// --- Inicialización y Funciones de Utilidad ---
async function initializeForm() {
  try {
    const response = await fetch("/get_user_data");
    if (response.ok) {
      const data = await response.json();
      const userEmail = data.email;
      document.getElementById("USER").value = userEmail.split("@")[0];
      document.getElementById("UUID").value = generateUUID(); // Para el job de programar parámetros
      document.getElementById("MARCA_TAXIMETRO").value = "CIBTRON";
      document.getElementById("MODELO_TAXIMETRO").value = "WB-001";
      document.getElementById("executeButton").disabled = true; // El de programar parámetros
      if (document.getElementById("userEmailDisplay")) {
        document.getElementById("userEmailDisplay").textContent = userEmail;
      }
    } else {
      console.error("No se pudo obtener el usuario, redirigiendo a login.");
      // window.location.href = "/login"; // Descomentar si quieres forzar redirección
    }
  } catch (error) {
    console.error("Error inicializando formulario:", error);
  }
}

function generateUUID() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
    const r = (Math.random() * 16) | 0,
      v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function addLogMessage(message, jobType = "params") {
  const logContainer = document.getElementById("jobLogs");
  if (!logContainer) {
    console.warn("Contenedor de logs 'jobLogs' no encontrado.");
    return;
  }

  // Si es el primer mensaje y el placeholder está presente, quitarlo.
  const placeholder = logContainer.querySelector("p.text-muted");
  if (placeholder) {
    placeholder.remove();
  }

  const logEntry = document.createElement("p");
  const serialNumberInput = document.getElementById("NUMERO_SERIAL");
  const serialNumber = serialNumberInput ? serialNumberInput.value : "N/A";

  const now = new Date();
  const timestamp = now.toLocaleString();

  let prefix = jobType === "set_serial" ? "[Prog. Serial]" : "[Prog. Params]";

  logEntry.innerHTML = `${timestamp} - ${prefix} ${serialNumber}: ${message}`;
  logContainer.appendChild(logEntry);
  logContainer.scrollTop = logContainer.scrollHeight; // Auto-scroll
}

function clearLogs() {
  const logContainer = document.getElementById("jobLogs");
  if (logContainer) {
    logContainer.innerHTML =
      '<p class="text-muted text-center">Esperando logs...</p>'; // Restaurar placeholder
  }
}

function clearMainForm() {
  const form = document.getElementById("jobForm");
  if (!form) return;

  const userField = document.getElementById("USER");
  const uuidField = document.getElementById("UUID");
  const marcaField = document.getElementById("MARCA_TAXIMETRO");
  const modeloField = document.getElementById("MODELO_TAXIMETRO");

  // Guardar valores que no deben resetearse
  const userValue = userField ? userField.value : "";
  const uuidValue = uuidField ? uuidField.value : ""; // Se regenerará uno nuevo
  const marcaValue = marcaField ? marcaField.value : "CIBTRON";
  const modeloValue = modeloField ? modeloField.value : "WB-001";

  form.reset();

  // Restaurar valores
  if (userField) userField.value = userValue;
  if (uuidField) uuidField.value = generateUUID(); // Generar nuevo UUID para la próxima operación de params
  if (marcaField) marcaField.value = marcaValue;
  if (modeloField) modeloField.value = modeloValue;

  const portSelect = document.getElementById("port");
  if (portSelect) {
    portSelect.innerHTML =
      '<option value="" disabled selected>Esperando detección...</option>';
  }
  checkFormValidity(); // Re-evaluar validez
}

function checkFormValidity() {
  const form = document.getElementById("jobForm");
  const executeBtn = document.getElementById("executeButton");
  const portVal = document.getElementById("port")?.value;

  if (form && executeBtn) {
    if (form.checkValidity() && portVal && portVal !== "") {
      executeBtn.disabled = false;
    } else {
      executeBtn.disabled = true;
    }
  }
}

function updatePortStatus(connected, portName = "") {
  const statusElement = document.getElementById("port-status");
  if (statusElement) {
    if (connected) {
      statusElement.textContent = `Conectado (${portName})`;
      statusElement.style.color = "green";
    } else {
      statusElement.textContent = "Desconectado";
      statusElement.style.color = "red";
    }
  }
}

function updateGlobalJobStatus(status, jobType = "params") {
  // Esta función es genérica. Se podría usar para actualizar un display global
  // si tuvieras uno, o simplemente para loguear. El manejo específico del estado
  // (botones, alertas) se hace en los handlers de Socket.IO.
  console.log(`Estado global del Job [${jobType}]: ${status}`);
  // addLogMessage(`Nuevo estado global: ${status}`, jobType); // Opcional, podría ser ruidoso
}

// --- Manejo de Puertos ---
async function fetchAndPopulatePorts(selectElementId, autoSelectIfOne = false) {
  const portSelect = document.getElementById(selectElementId);
  if (!portSelect) return;

  // Guardar el valor seleccionado actualmente si existe, para intentar restaurarlo
  const previouslySelectedPort = portSelect.value;

  portSelect.innerHTML =
    '<option value="" disabled selected>Detectando puertos...</option>';

  try {
    const response = await fetch("/get_ports");
    const data = await response.json();
    portSelect.innerHTML = ""; // Limpiar antes de añadir nuevas opciones

    if (data.status === "success" && data.ports.length > 0) {
      let foundPreviouslySelected = false;
      data.ports.forEach((port) => {
        const option = document.createElement("option");
        option.value = port.device;
        option.textContent = `${port.device} (${port.description || "N/A"})`;
        if (port.device === previouslySelectedPort) {
          option.selected = true;
          foundPreviouslySelected = true;
        }
        portSelect.appendChild(option);
      });

      if (!foundPreviouslySelected && data.ports.length > 0) {
        portSelect.insertBefore(
          new Option("Selecciona un puerto...", "", true, true),
          portSelect.firstChild
        );
        portSelect.value = ""; // Forzar selección si el anterior no está
      } else if (!foundPreviouslySelected && previouslySelectedPort) {
        // Si había uno seleccionado y ya no está, resetear.
        portSelect.insertBefore(
          new Option("Selecciona un puerto...", "", true, true),
          portSelect.firstChild
        );
        portSelect.value = "";
      }

      if (
        autoSelectIfOne &&
        data.ports.length === 1 &&
        !foundPreviouslySelected
      ) {
        portSelect.value = data.ports[0].device;
        selectedPort = portSelect.value; // Actualizar variable global
        updatePortStatus(true, selectedPort);
        await fetchAndSetSerialNumber(selectedPort); // Intentar obtener serial si solo hay un puerto
      } else if (!foundPreviouslySelected) {
        portSelect.value = ""; // Dejar sin selección si hay múltiples o ninguno
      }
    } else {
      portSelect.appendChild(
        new Option("No se detectaron taxímetros", "", true, true)
      );
      portSelect.value = "";
      updatePortStatus(false);
    }
  } catch (error) {
    console.error("Error al obtener puertos:", error);
    portSelect.innerHTML =
      '<option value="" disabled selected>Error al cargar puertos</option>';
    updatePortStatus(false);
  }
  checkFormValidity(); // Después de poblar puertos, verificar validez del form principal
}

async function fetchAndSetSerialNumber(portDevice) {
  const serialInput = document.getElementById("NUMERO_SERIAL");
  if (!portDevice || !serialInput) return;

  serialInput.value = "Detectando serial...";
  try {
    const response = await fetch("/get_serial_number", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port: portDevice }),
    });
    const data = await response.json();
    if (data.status === "success") {
      serialInput.value = data.serial_number.toUpperCase();
    } else {
      serialInput.value = "Error al detectar";
      // alert(`No se pudo obtener el número serial del puerto ${portDevice}: ${data.message}`);
      console.warn(
        `No se pudo obtener el número serial del puerto ${portDevice}: ${data.message}`
      );
    }
  } catch (error) {
    serialInput.value = "Error de conexión";
    console.error("Error al obtener número de serie:", error);
  }
  checkFormValidity();
}

// --- Event Listeners para Elementos del DOM ---
document.addEventListener("DOMContentLoaded", () => {
  initializeForm();
  fetchAndPopulatePorts("port", true); // Llenar puertos del form principal al cargar

  const mainForm = document.getElementById("jobForm");
  if (mainForm) {
    mainForm.addEventListener("input", checkFormValidity);
    mainForm.addEventListener("change", checkFormValidity); // Para selects
  }

  const updatePortsBtn = document.getElementById("updatePortsBtn");
  if (updatePortsBtn) {
    updatePortsBtn.addEventListener("click", () => {
      fetchAndPopulatePorts("port", true);
      document.getElementById("NUMERO_SERIAL").value = ""; // Limpiar serial al actualizar puertos
    });
  }

  const portSelectMain = document.getElementById("port");
  if (portSelectMain) {
    portSelectMain.addEventListener("change", async (event) => {
      selectedPort = event.target.value;
      if (selectedPort) {
        updatePortStatus(true, selectedPort);
        await fetchAndSetSerialNumber(selectedPort);
      } else {
        document.getElementById("NUMERO_SERIAL").value = "";
        updatePortStatus(false);
      }
      checkFormValidity();
    });
  }

  // Botón "Programar Parámetros" (el original)
  const executeParamsButton = document.getElementById("executeButton");
  if (executeParamsButton) {
    // El onsubmit del form ya llama a executeAndProgram
  }

  // Botón "Rellenar Formulario"
  const searchSerialBtn = document.getElementById("searchSerialBtn");
  if (searchSerialBtn) {
    searchSerialBtn.addEventListener("click", async () => {
      const serialNumber = document.getElementById("NUMERO_SERIAL").value;
      if (!serialNumber) {
        alert("Por favor, detecta o ingresa un número serial primero.");
        return;
      }
      try {
        const response = await fetch(
          `/search_serial?serial_number=${serialNumber.trim()}`
        );
        const result = await response.json();
        if (
          result.status === "success" &&
          result.data &&
          result.data.env_vars
        ) {
          const data = result.data.env_vars;
          // Llenar todos los campos del formulario principal
          Object.keys(data).forEach((key) => {
            const field = document.getElementById(key);
            if (field) field.value = data[key];
          });
          alert("Datos del último certificado cargados en el formulario.");
        } else {
          alert(
            result.message ||
              "No se encontró información para este serial o hubo un error."
          );
        }
      } catch (error) {
        console.error("Error al buscar número serial para rellenar:", error);
        alert("Ocurrió un error al buscar la información del serial.");
      }
      checkFormValidity();
    });
  }

  // Botón "Buscar Programaciones"
  const searchCertBtn = document.getElementById("searchCertificateBtn");
  if (searchCertBtn) {
    searchCertBtn.addEventListener("click", async () => {
      const serialNumber = document.getElementById("NUMERO_SERIAL").value;
      if (!serialNumber) {
        alert(
          "Por favor, detecta o ingresa un número serial para buscar programaciones."
        );
        return;
      }
      const certificatesList = document.getElementById("certificatesList");
      certificatesList.innerHTML = "<p>Buscando programaciones...</p>";
      try {
        const response = await fetch(
          `/search_certificates?serial_number=${serialNumber.trim()}`
        );
        const result = await response.json();
        certificatesList.innerHTML = ""; // Limpiar
        if (
          result.status === "success" &&
          Array.isArray(result.data) &&
          result.data.length > 0
        ) {
          result.data.forEach((cert) => {
            const data = cert.document_data;
            const card = document.createElement("div");
            card.className = "card m-2"; // Añadido margen
            card.style.minWidth = "250px"; // Ancho mínimo para tarjetas

            card.innerHTML = `
              <div class="card-body">
                <h5 class="card-title">ID: ${cert.document_id.substring(
                  0,
                  8
                )}...</h5>
                <p class="card-text">
                  <strong>Fecha:</strong> ${new Date(
                    data.date
                  ).toLocaleString()}<br>
                  <strong>Prog:</strong> ${data.user || "N/A"}<br>
                  <strong>Patente:</strong> ${data.env_vars?.PATENTE || "N/A"}
                </p>
                <button class="btn btn-primary btn-sm" onclick='showCertificateDetails(${JSON.stringify(
                  data
                )})'>
                  <i class="fas fa-eye"></i> Ver detalles
                </button>
              </div>`;
            certificatesList.appendChild(card);
          });
        } else {
          certificatesList.innerHTML =
            "<p>No se encontraron programaciones previas para este número serial.</p>";
        }
      } catch (error) {
        console.error("Error al buscar programaciones:", error);
        certificatesList.innerHTML =
          "<p>Ocurrió un error al buscar programaciones.</p>";
      }
    });
  }

  // Botón "Resetear Cibtron"
  const resetButton = document.getElementById("resetButton");
  if (resetButton) {
    resetButton.addEventListener("click", async () => {
      if (!selectedPort) {
        alert(
          "Debe seleccionar/conectar un puerto primero para poder resetear el dispositivo."
        );
        return;
      }
      if (
        !confirm(
          "¿Estás seguro de que deseas resetear el firmware del taxímetro al de lectura de serial?"
        )
      ) {
        return;
      }
      addLogMessage("Iniciando reseteo de firmware del Cibtron...", "params");
      try {
        const formData = new FormData();
        formData.append("port", selectedPort);
        // firmware_path se define en el backend
        const response = await fetch("/resetcibtron", {
          method: "POST",
          body: formData,
        });
        const result = await response.json();
        if (result.status === "success") {
          addLogMessage(
            "Dispositivo reseteado con firmware de lectura de serial. Refrescando...",
            "params"
          );
          alert("El dispositivo ha sido reseteado. La página se recargará.");
          location.reload();
        } else {
          addLogMessage(`Error al resetear: ${result.message}`, "params");
          alert(`Error al resetear: ${result.message}`);
        }
      } catch (error) {
        console.error("Error al resetear:", error);
        addLogMessage(`Error al resetear: ${error.message}`, "params");
        alert(`Error al resetear: ${error.message}`);
      }
    });
  }

  // --- NUEVA LÓGICA PARA "PROGRAMAR NUEVO NÚMERO SERIAL" ---
  const setSerialButton = document.getElementById("setSerialButton");
  if (setSerialButton) {
    setSerialButton.addEventListener("click", () => {
      // Poblar el selector de puertos en el modal #setSerialModal
      const mainPortSelect = document.getElementById("port");
      const modalPortSelect = document.getElementById("modal_port_set_serial");
      if (mainPortSelect && modalPortSelect) {
        modalPortSelect.innerHTML = ""; // Limpiar opciones previas
        // Añadir opción placeholder
        modalPortSelect.appendChild(
          new Option("Selecciona un puerto...", "", true, true)
        );

        Array.from(mainPortSelect.options).forEach((opt) => {
          if (opt.value && !opt.disabled) {
            // Copiar solo opciones válidas
            modalPortSelect.appendChild(new Option(opt.text, opt.value));
          }
        });

        if (mainPortSelect.value) {
          modalPortSelect.value = mainPortSelect.value; // Preseleccionar si hay uno válido en el form principal
        } else {
          modalPortSelect.value = ""; // Si no hay selección en el principal, dejar el placeholder
        }
      }
      // Limpiar campos del modal por si se abrió antes
      const serialToProgInput = document.getElementById(
        "modal_NUMERO_SERIAL_A_PROGRAMAR"
      );
      const accessKeyInput = document.getElementById("modal_CLAVE_ACCESO");
      if (serialToProgInput) serialToProgInput.value = "";
      if (accessKeyInput) accessKeyInput.value = "";

      // $('#setSerialModal').modal('show'); // Esto se maneja por data-toggle y data-target en el HTML
    });
  }

  const executeSetSerialJobButton = document.getElementById(
    "executeSetSerialJobButton"
  );
  if (executeSetSerialJobButton) {
    executeSetSerialJobButton.addEventListener("click", async () => {
      const serialToProgram = document
        .getElementById("modal_NUMERO_SERIAL_A_PROGRAMAR")
        .value.trim();
      const accessKey = document
        .getElementById("modal_CLAVE_ACCESO")
        .value.trim();
      const portForSetSerial = document.getElementById(
        "modal_port_set_serial"
      ).value;
      const userEmailField = document.getElementById("USER"); // USER del programador

      if (!userEmailField || !userEmailField.value) {
        alert("Error: No se pudo obtener el email del programador.");
        return;
      }
      const userForJob = userEmailField.value;

      if (!serialToProgram || !/^\d+$/.test(serialToProgram)) {
        alert("Por favor, ingresa un número serial válido (solo números).");
        return;
      }
      if (!accessKey || accessKey.length !== 10) {
        alert(
          "Por favor, ingresa una clave de acceso válida de 10 caracteres."
        );
        return;
      }
      if (!portForSetSerial) {
        alert(
          "Por favor, selecciona un puerto para la programación del dispositivo."
        );
        return;
      }

      addLogMessage(
        `Iniciando proceso para programar nuevo serial: ${serialToProgram}`,
        "set_serial"
      );
      updateGlobalJobStatus(
        "Iniciando programación de nuevo serial...",
        "set_serial"
      );

      const uuidForJob = generateUUID();

      const formData = new FormData();
      formData.append("NUMERO_SERIAL_A_PROGRAMAR", serialToProgram);
      formData.append("CLAVE_ACCESO", accessKey);
      formData.append("USER", userForJob);
      formData.append("UUID", uuidForJob);
      formData.append("port", portForSetSerial);

      $("#setSerialModal").modal("hide");

      try {
        const response = await fetch("/execute_set_serial_job", {
          method: "POST",
          body: formData,
        });
        const result = await response.json();

        if (result.status === "success") {
          addLogMessage(
            `Job para programar serial ${serialToProgram} (UUID: ${uuidForJob}) iniciado. Monitoreando...`,
            "set_serial"
          );
          // El monitoreo de logs se gestiona a través de Socket.IO desde el backend
        } else {
          addLogMessage(
            `Error al iniciar job de programación de serial: ${result.message}`,
            "set_serial"
          );
          alert(`Error: ${result.message}`);
          updateGlobalJobStatus("Error", "set_serial");
        }
      } catch (error) {
        console.error("Error en fetch /execute_set_serial_job:", error);
        addLogMessage(
          `Error de red al ejecutar job de programación de serial: ${error.message}`,
          "set_serial"
        );
        alert(`Error de red: ${error.message}`);
        updateGlobalJobStatus("Error de red", "set_serial");
      }
    });
  }
}); // Fin de DOMContentLoaded

// --- Funciones de Socket.IO ---
if (socket) {
  socket.on("connect", () => {
    console.log("Socket.IO: Conectado al servidor.");
    addLogMessage("Conectado al servidor de notificaciones.", "system");
  });

  socket.on("disconnect", () => {
    console.warn("Socket.IO: Desconectado del servidor.");
    addLogMessage("Desconectado del servidor de notificaciones.", "system");
  });

  socket.on("connect_error", (error) => {
    console.error("Socket.IO: Error de conexión:", error);
    addLogMessage(
      `Error de conexión al servidor de notificaciones: ${error.message}`,
      "system"
    );
  });

  // Listener para el job original de programación de parámetros
  socket.on("job_status_update", (data) => {
    const currentStatus = data.status;
    const message = data.message || "";
    console.log(
      "Socket.IO (Job Parámetros) - Estado:",
      currentStatus,
      "Mensaje:",
      message
    );

    if (currentStatus !== lastStatus) {
      addLogMessage(`Estado: ${currentStatus}. ${message}`, "params");
      lastStatus = currentStatus;
    }
    updateGlobalJobStatus(currentStatus, "params");

    const executeBtn = document.getElementById("executeButton");
    if (
      currentStatus === "completed" ||
      currentStatus === "success" ||
      currentStatus === "Programación completa." ||
      currentStatus === "Finalizado"
    ) {
      if (!isProgramFinished) {
        // Evitar múltiples alertas/reloads
        isProgramFinished = true;
        if (executeBtn)
          executeBtn.textContent = "Programar Parámetros del Cibtron WB-001";
        // El mensaje de éxito y reload se maneja en la función checkStatus original si se mantiene.
        // Si no, se puede añadir aquí:
        alert(
          `Programación de parámetros para ${
            document.getElementById("NUMERO_SERIAL").value
          } completada. Desconecte el taxímetro.`
        );
        if (!isReloading) {
          isReloading = true;
          location.reload();
        }
      }
    } else if (
      (currentStatus && currentStatus.toLowerCase().includes("error")) ||
      currentStatus === "failed" ||
      currentStatus === "blocked"
    ) {
      if (executeBtn) {
        executeBtn.textContent = "Programar Parámetros del Cibtron WB-001";
        executeBtn.disabled = false; // Habilitar para reintentar
      }
      isProgramFinished = true; // Marcar como finalizado para detener polling si es necesario
    } else if (currentStatus) {
      if (executeBtn) executeBtn.textContent = currentStatus; // Actualizar texto del botón
    }
  });

  // NUEVO Listener para el job de SETEO DE SERIAL
  socket.on("set_serial_job_status_update", (data) => {
    const currentStatus = data.status;
    const message = data.message || "";
    const logData = data.log_data || {}; // Para obtener numero_serial_a_programar si es necesario
    const serialProgrammed =
      logData.numero_serial_a_programar ||
      document.getElementById("modal_NUMERO_SERIAL_A_PROGRAMAR")?.value ||
      "desconocido";

    console.log(
      "Socket.IO (Set Serial) - Estado:",
      currentStatus,
      "Mensaje:",
      message
    );

    if (currentStatus !== lastSetSerialStatus) {
      addLogMessage(`Estado: ${currentStatus}. ${message}`, "set_serial");
      lastSetSerialStatus = currentStatus;
    }
    updateGlobalJobStatus(currentStatus, "set_serial");

    if (currentStatus === "Programación de Serial Completa") {
      alert(
        `El nuevo serial ${serialProgrammed} ha sido programado en el dispositivo. Puede desconectarlo.`
      );
      // Aquí podrías querer reiniciar alguna parte de la UI o simplemente informar.
      // No se hace reload automático para no perder los logs.
      // Se podría limpiar el formulario del modal o resetear el estado del botón del modal.
    } else if (
      (currentStatus && currentStatus.toLowerCase().includes("error")) ||
      currentStatus === "failed" ||
      currentStatus === "auth_failed"
    ) {
      alert(
        `Error durante la programación del nuevo serial ${serialProgrammed}: ${message}`
      );
    }
  });
} else {
  console.error(
    "Socket.IO no está disponible. Las actualizaciones en tiempo real no funcionarán."
  );
  alert(
    "No se pudo conectar al servidor de notificaciones. Algunas funcionalidades pueden no estar disponibles."
  );
}

// --- Funciones de la UI original (executeAndProgram, logout, etc.) ---
async function executeAndProgram(event) {
  // Programación de parámetros
  event.preventDefault();
  isProgramFinished = false; // Resetear para la nueva ejecución
  isReloading = false;
  lastStatus = null; // Resetear último estado para logs

  const formData = new FormData(document.getElementById("jobForm"));
  const localSelectedPort = document.getElementById("port").value;

  if (!localSelectedPort) {
    alert(
      "Debe seleccionar un puerto y conectar el taxímetro antes de ejecutar el trabajo."
    );
    return;
  }
  // formData.append("port", localSelectedPort); // 'port' ya está en formData si el select tiene name="port"

  addLogMessage("Iniciando programación de parámetros...", "params");
  updateGlobalJobStatus("Iniciando...", "params");

  const executeBtn = document.getElementById("executeButton");
  if (executeBtn) {
    executeBtn.disabled = true;
    executeBtn.textContent = "Procesando...";
  }

  try {
    const response = await fetch("/execute_and_program", {
      method: "POST",
      body: formData,
    });
    const result = await response.json();
    if (result.status === "success") {
      addLogMessage(
        "Job de programación de parámetros iniciado. Monitoreando...",
        "params"
      );
      // El monitoreo de logs se gestiona a través de Socket.IO desde el backend
    } else {
      addLogMessage(
        `Error al iniciar job de parámetros: ${result.message}`,
        "params"
      );
      alert(`Error: ${result.message}`);
      if (executeBtn) {
        executeBtn.textContent = "Programar Parámetros del Cibtron WB-001";
        executeBtn.disabled = false; // Re-habilitar si falla el inicio
      }
      updateGlobalJobStatus("Error", "params");
    }
  } catch (error) {
    console.error("Error en fetch /execute_and_program:", error);
    addLogMessage(
      `Error de red al ejecutar job de parámetros: ${error.message}`,
      "params"
    );
    alert(`Error de red: ${error.message}`);
    if (executeBtn) {
      executeBtn.textContent = "Programar Parámetros del Cibtron WB-001";
      executeBtn.disabled = false;
    }
    updateGlobalJobStatus("Error de red", "params");
  }
}

function logout() {
  if (!confirm("¿Estás seguro de que deseas cerrar sesión?")) return;
  fetch("/logout", { method: "POST", credentials: "include" })
    .then((response) => response.json())
    .then((data) => {
      if (data.status === "success") {
        alert("Sesión cerrada correctamente.");
        window.location.href = "/login";
      } else {
        alert("Error al cerrar sesión.");
      }
    })
    .catch((error) => {
      console.error("Error al cerrar sesión:", error);
      alert("Error al cerrar sesión.");
    });
}

function showCertificateDetails(data) {
  // Renombrada para claridad
  const modalContent = document.getElementById("modalContent");
  if (!modalContent) return;
  modalContent.innerHTML = ""; // Limpiar contenido anterior

  const fieldMapping = {
    UUID: "UUID Compilación",
    USER: "Programador",
    DATE: "Fecha Compilación",
    NUMERO_SERIAL: "N° Serie Dispositivo",
    NUMERO_SELLO: "N° Sello",
    MARCA_TAXIMETRO: "Marca Taxímetro",
    MODELO_TAXIMETRO: "Modelo Taxímetro",
    NOMBRE_PROPIETARIO: "Nombre Propietario",
    APELLIDO_PROPIETARIO: "Apellido Propietario",
    MARCA_VEHICULO: "Marca Vehículo",
    YEAR_VEHICULO: "Año Vehículo",
    PATENTE: "Patente",
    RESOLUCION: "Resolución",
    CANTIDAD_PULSOS: "Divisor Pulsos",
    TARIFA_INICIAL: "Tarifa Inicial",
    TARIFA_CAIDA_PARCIAL_METROS: "Caída Parcial Metros",
    TARIFA_CAIDA_PARCIAL_MINUTO: "Caída Parcial Minuto",
    MOSTRAR_VELOCIDAD_EN_PANTALLA: "Mostrar Metros",
    COLOR_FONDO_PANTALLA: "Color Fondo",
    COLOR_LETRAS_PANTALLA: "Color Letras",
    COLOR_PRECIO_PANTALLA: "Color Precio",
    PROPAGANDA_1: "Propaganda 1",
    PROPAGANDA_2: "Propaganda 2",
    PROPAGANDA_3: "Propaganda 3",
    PROPAGANDA_4: "Propaganda 4",
  };

  if (data && data.env_vars && typeof data.env_vars === "object") {
    for (const [key, title] of Object.entries(fieldMapping)) {
      const value =
        data.env_vars[key] !== undefined && data.env_vars[key] !== ""
          ? data.env_vars[key]
          : "N/A";
      modalContent.innerHTML += `<p><strong>${title}:</strong> ${value}</p>`;
    }
    if (data.path) {
      // Mostrar ruta del binario si existe
      modalContent.innerHTML += `<p><strong>Ruta Binario:</strong> ${data.path}</p>`;
    }
  } else {
    modalContent.innerHTML =
      "<p>No hay detalles de variables de entorno disponibles para esta programación.</p>";
  }
  $("#detailsModal").modal("show");
}

// Polling de estado original (si se mantiene junto con Socket.IO como fallback o para ciertos estados)
// Se recomienda depender más de Socket.IO para actualizaciones en tiempo real.
// Si se va a quitar el polling, la lógica de `isProgramFinished` y `isReloading`
// debe ser manejada enteramente dentro de los callbacks de Socket.IO.

/* 
// Comentado porque Socket.IO es preferible para actualizaciones de estado.
// Si se reactiva, asegurar que no entre en conflicto con la lógica de Socket.IO.
setInterval(async () => {
  if (isProgramFinished) return; // Si el job de parámetros terminó, no seguir polleando para él.

  try {
    const response = await fetch("/get_job_status"); // Esta ruta obtiene el estado del job de parámetros
    const data = await response.json();
    const currentPollingStatus = data.status;

    if (currentPollingStatus && currentPollingStatus !== lastStatus) {
      console.log("Polling (Job Parámetros) - Estado:", currentPollingStatus);
      // addLogMessage(`Estado (Polling): ${currentPollingStatus}`, "params"); // Podría ser redundante si Socket.IO funciona
      lastStatus = currentPollingStatus;
    }

    if (currentPollingStatus === "Finalizado" || currentPollingStatus === "completed" || currentPollingStatus === "success") {
      if (!isProgramFinished) {
        isProgramFinished = true;
        if (!isReloading) {
          alert(`Programación de parámetros para ${document.getElementById("NUMERO_SERIAL").value} completada (vía polling). Desconecte el taxímetro.`);
          isReloading = true;
          location.reload();
        }
      }
    }
  } catch (error) {
    console.error("Error en polling de estado (Job Parámetros):", error);
    // addLogMessage("Error en polling de estado (Job Parámetros).", "params");
  }
}, 5000); // Intervalo de polling (ej. cada 5 segundos)
*/

// Intervalo para verificar conexión del puerto seleccionado (si existe uno)
setInterval(async () => {
  if (!selectedPort) {
    // selectedPort se actualiza en el 'change' del select principal
    updatePortStatus(false); // Si no hay puerto seleccionado, mostrar desconectado
    return;
  }
  try {
    const response = await fetch("/check_port_status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port: selectedPort }),
    });
    const data = await response.json();
    if (data.status === "success") {
      updatePortStatus(data.connected, selectedPort);
      if (!data.connected) {
        // Si el puerto seleccionado se desconecta, limpiar selección y N° Serie, y recargar puertos.
        addLogMessage(
          `Puerto ${selectedPort} desconectado. Actualizando...`,
          "system"
        );
        selectedPort = null;
        document.getElementById("port").value = "";
        document.getElementById("NUMERO_SERIAL").value = "";
        fetchAndPopulatePorts("port", true);
        checkFormValidity();
        // No se hace location.reload() aquí para evitar interrupciones si el usuario está haciendo otra cosa.
      }
    } else {
      console.warn("Advertencia al verificar estado del puerto:", data.message);
      updatePortStatus(false); // Asumir desconectado si hay error en la verificación
    }
  } catch (error) {
    console.error("Error en solicitud de check_port_status:", error);
    updatePortStatus(false); // Asumir desconectado si hay error de red
  }
}, 3000); // Verificar cada 3 segundos
