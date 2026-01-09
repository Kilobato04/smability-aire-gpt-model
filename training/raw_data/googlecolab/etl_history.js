const axios = require('axios');
const { JSDOM } = require('jsdom');
const createCsvWriter = require('csv-writer').createObjectCsvWriter;
const fs = require('fs');
const path = require('path');

// CONFIGURACIÓN
const YEARS = ['2023', '2024', '2025']; // Los 2 años clave + el actual
const PARAMETERS = ['pm10', 'pm25', 'o3', 'co', 'no2', 'so2', 'tmp', 'rh', 'wsp', 'wdr'];
const OUTPUT_DIR = './raw_data';

// Asegurar que existe el directorio de salida
if (!fs.existsSync(OUTPUT_DIR)){
    fs.mkdirSync(OUTPUT_DIR);
}

// Headers "fake" para que el servidor de CDMX no nos bloquee (Tomados de tu proxy.js)
const HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Cookie': 'PHPSESSID=randomstring' // A veces ayuda mantener sesión, aunque no es estrictamente necesario
};

// Función de Pausa (Para no tirar el servidor del gobierno)
const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

async function fetchMonthData(year, month, param) {
    // Ajuste de parámetros específicos como en tu código original
    let paramValue = param;
    if (param === 'pm25') paramValue = 'pm2';
    
    const url = `http://www.aire.cdmx.gob.mx/estadisticas-consultas/concentraciones/respuesta.php?qtipo=HORARIOS&parametro=${paramValue}&anio=${year}&qmes=${month}`;
    
    try {
        console.log(`📡 Bajando: ${year}-${month} [${param}]...`);
        const response = await axios.get(url, { headers: HEADERS, timeout: 30000 }); // 30s timeout
        return response.data;
    } catch (error) {
        console.error(`❌ Error en ${year}-${month}-${param}: ${error.message}`);
        return null;
    }
}

// Tu lógica de parsing adaptada a JSDOM
function parseHtmlToData(html, year, month, param) {
    if (!html || html.length < 500) return []; // Validación básica

    const dom = new JSDOM(html);
    const doc = dom.window.document;
    const tables = doc.querySelectorAll('table');
    
    if (tables.length === 0) return [];

    // Encontrar la tabla de datos (la que tiene más filas)
    let dataTable = tables[0];
    let maxRows = 0;
    tables.forEach(table => {
        const rows = table.querySelectorAll('tr').length;
        if (rows > maxRows) {
            maxRows = rows;
            dataTable = table;
        }
    });

    const rows = dataTable.querySelectorAll('tr');
    if (rows.length < 2) return [];

    // Detectar fila de encabezados
    let headerRowIndex = 1; 
    if (param === 'o3_8h') headerRowIndex = 3; // Tu lógica específica para O3 8h

    const headerCells = rows[headerRowIndex].querySelectorAll('td');
    const headerTexts = Array.from(headerCells).map(cell => cell.textContent.trim().toLowerCase());

    // Encontrar índices clave
    const horaIndex = headerTexts.findIndex(t => t.includes('hora'));
    if (horaIndex === -1) return [];

    // Mapear Estaciones (Nombre -> Índice Columna)
    const stationsMap = {};
    for (let i = horaIndex + 1; i < headerTexts.length; i++) {
        const stationName = headerTexts[i].toUpperCase();
        if (stationName.length === 3) { // Las claves son de 3 letras (MER, PED, etc)
            stationsMap[i] = stationName;
        }
    }

    const parsedData = [];
    // Iterar filas de datos
    for (let i = headerRowIndex + 1; i < rows.length; i++) {
        const cells = rows[i].querySelectorAll('td');
        if (cells.length <= horaIndex) continue;

        // Extraer Día y Hora
        // Asumimos que el día viene en la primera columna o inferimos por el loop
        // En tu código original extraes fecha compleja, aquí simplificaremos si la tabla tiene estructura estándar
        // Nota: El formato de fecha en la tabla suele ser DD-MM-YYYY
        let day = '01';
        let fullDate = `${year}-${month}-01`;
        
        // Intento de leer fecha de la primera celda si existe
        const dateCellText = cells[0].textContent.trim();
        if(dateCellText.includes('-') || dateCellText.includes('/')) {
            // Lógica simple de fecha, ajustar según el HTML real
             const dateMatch = dateCellText.match(/(\d+)[\/\-](\d+)[\/\-](\d+)/);
             if(dateMatch) fullDate = `${year}-${month}-${dateMatch[1].padStart(2,'0')}`; // Asumiendo DD es el primer grupo cambiante
        }

        let hour = cells[horaIndex].textContent.trim();
        hour = parseInt(hour);
        if (isNaN(hour) || hour > 24) continue; // Ajuste hora 24 -> 00 sig día si fuera necesario
        
        // Extraer valor para cada estación
        Object.keys(stationsMap).forEach(colIndex => {
            if (cells[colIndex]) {
                let val = cells[colIndex].textContent.trim();
                // Limpieza de valores sucios
                if (['NR', 'N/D', '-', ''].includes(val)) {
                    val = null; 
                } else {
                    val = parseFloat(val);
                }

                if (val !== null && !isNaN(val)) {
                    parsedData.push({
                        date: fullDate, // YYYY-MM-DD
                        hour: hour,
                        station_id: stationsMap[colIndex],
                        parameter: param,
                        value: val
                    });
                }
            }
        });
    }
    return parsedData;
}

async function run() {
    console.log("🚀 Iniciando Extracción de Histórico CDMX (24 meses)...");
    
    // Definir el CSV Writer global (append mode es complejo, mejor archivos por parámetro/año)
    
    for (const year of YEARS) {
        for (const param of PARAMETERS) {
            const fileName = `${OUTPUT_DIR}/${year}_${param}.csv`;
            console.log(`\n📄 Procesando archivo: ${fileName}`);
            
            const csvWriter = createCsvWriter({
                path: fileName,
                header: [
                    {id: 'date', title: 'DATE'},
                    {id: 'hour', title: 'HOUR'},
                    {id: 'station_id', title: 'STATION'},
                    {id: 'parameter', title: 'PARAM'},
                    {id: 'value', title: 'VALUE'}
                ]
            });

            let yearData = [];

            for (let m = 1; m <= 12; m++) {
                // Saltarse meses futuros del año actual
                const now = new Date();
                if (year == now.getFullYear() && m > now.getMonth() + 1) break;

                const monthStr = m.toString().padStart(2, '0');
                
                // 1. Fetch
                const html = await fetchMonthData(year, monthStr, param);
                
                // 2. Parse
                if (html) {
                    const data = parseHtmlToData(html, year, monthStr, param);
                    yearData = yearData.concat(data);
                    console.log(`   ✅ ${year}-${monthStr}: ${data.length} registros extraídos.`);
                }

                // 3. Pausa de cortesía (importante para evitar bloqueo)
                await sleep(500); 
            }

            // 4. Guardar CSV Anual del Parámetro
            if (yearData.length > 0) {
                await csvWriter.writeRecords(yearData);
                console.log(`💾 Guardado ${fileName} con ${yearData.length} filas.`);
            } else {
                console.log(`⚠️ No hubo datos para ${fileName}`);
            }
        }
    }
    console.log("\n🏁 Extracción Completa. Datos listos en carpeta /raw_data");
}

run();
