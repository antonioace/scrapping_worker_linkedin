# Monitor de trabajos React en Colombia

Guía para ejecutar los tres proyectos:
[GUIA_PRUEBAS.md](https://github.com/antonioace/scraping_jobs_backend/blob/main/GUIA_PRUEBAS.md).

Python y Playwright consultan LinkedIn cada **900 segundos (15 minutos)**,
guardan reportes y envían ofertas al backend REST/SSE. La autenticación usa
exclusivamente un JSON privado de cookies. El script carga automáticamente el
`.env` de esta carpeta; las variables existentes del entorno tienen prioridad.

## Preparación en Windows

Desde esta carpeta, con Python 3.10 o superior:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Si ya tienes el entorno activo, usa directamente `python`. Al actualizar,
instala de nuevo `requirements.txt` para incluir `python-dotenv`.
No necesitas ejecutar el cargador dotenv desde el entorno del backend.

## Usar tu sesión mediante cookies JSON

En este PC ya existe `linkedin.cookies.json`: conserva su contenido privado.
En otro equipo, copia la plantilla únicamente si el archivo todavía no existe:

```powershell
if (!(Test-Path .\linkedin.cookies.json)) { Copy-Item .\linkedin.cookies.example.json .\linkedin.cookies.json }
```

1. Abre LinkedIn en tu navegador habitual con tu sesión iniciada.
2. Ve a `F12 → Application / Aplicación → Storage / Almacenamiento → Cookies →
   https://www.linkedin.com`. Copia el valor completo de `li_at` al campo `value`
   del JSON. Conserva el nombre, dominio y ruta de la plantilla.
3. Guarda el JSON local. Puedes usar una lista de cookies o un objeto
   `{"cookies": [...]}` exportado únicamente de LinkedIn. Si incluyes `JSESSIONID`,
   usa la misma sesión y escapa las comillas de su valor como `\"` en JSON.

El JSON real se excluye de Git y de la imagen Docker. No lo compartas ni lo
incluyas en `.env` o el frontend. La plantilla pública tiene el valor vacío.
La cookie HttpOnly se consulta en DevTools, no mediante `document.cookie`.

Se valida el JSON antes de abrir Chromium. Si falta, está vacío o caducado,
el monitor se detiene. Cada arranque crea un navegador temporal y carga las
cookies del JSON, sin leer ni guardar perfiles en disco. Si LinkedIn pide verificación, actualiza
manualmente tu sesión y el JSON, y reinicia. El script no abre un formulario de
acceso. Importar cookies no elimina una restricción de cuenta.

## Ejecutar en Windows / desarrollo

El backend debe estar ejecutándose en `http://127.0.0.1:8000`. Con el entorno
activo y el `.env` ya configurado, ejecuta:

```powershell
python linkedin_jobs.py --api-url http://127.0.0.1:8000
```

Se usa `linkedin.cookies.json` automáticamente. La primera búsqueda es inmediata
y las siguientes son cada 15 minutos. Detén el proceso con `Ctrl+C`.
Para una sola consulta, añade `--once`.

El `.env` del scraper necesita `JOBS_API_URL` y el mismo `JOBS_INGEST_TOKEN` del
backend. El argumento `--api-url` permite usar la API local aunque tu `.env`
conserve el alias Docker `http://job-tracker-api:8000`. Nunca imprime el token.
Para otra ubicación del JSON usa `--cookies-file ruta/al/archivo.json`, o la
variable opcional `LINKEDIN_COOKIES_FILE`. El argumento tiene prioridad.

## Producción Linux / Docker

Este repositorio tiene un único `docker-compose.yml` y un único servicio `worker`.
Comparte la red externa `web` con el backend, sin dominio ni router Traefik.
Configura el `.env` privado con `JOBS_API_URL=http://job-tracker-api:8000` y el mismo
`JOBS_INGEST_TOKEN` del backend. No necesita VAPID, Firebase Admin ni PostgreSQL.

```bash
cd ~/job-tracker/scrap_jobs
if [ ! -f .env ]; then cp .env.example .env; fi
chmod 600 .env
nano .env
if [ ! -f linkedin.cookies.json ]; then
  cp linkedin.cookies.example.json linkedin.cookies.json
fi
nano linkedin.cookies.json
# El contenedor usa UID 10001 y debe poder leer el JSON.
sudo chown 10001:10001 linkedin.cookies.json
sudo chmod 600 linkedin.cookies.json
docker compose up -d --build --remove-orphans worker
docker compose logs -f worker
```

El Compose monta el JSON como solo lectura en `/run/secrets/linkedin.cookies.json`.
El volumen privado `linkedin-data` conserva los reportes y la cola
SQLite. No se publica ningún puerto. El servicio y la imagen de login temporal,
noVNC y el Compose complementario de cookies fueron retirados: usa solo el
Compose principal. Los Compose de backend y frontend siguen separados.

Para renovar cookies: `docker compose stop worker`, edita con
`sudoedit linkedin.cookies.json` y ejecuta `docker compose up -d worker`.
El proceso lee el JSON una vez al arrancar. Usa una sola instancia por carpeta de reportes.
No se reinicia automáticamente ante bloqueo o fallo fatal. No elimines el volumen
si quieres conservar los reportes y la cola de envío.

Guía completa:
[DEPLOY_LINUX.md](https://github.com/antonioace/scraping_jobs_backend/blob/main/DEPLOY_LINUX.md).
No se incluyen contenedores PostgreSQL ni workflows de GitHub Actions.

## Filtros

Antes de guardar o enviar una oferta se lee su descripción completa. Debe
mencionar al menos una de estas tecnologías: **React, Next.js, Angular, Vue,
SvelteKit, Express, NestJS o FastAPI**. Se aceptan variantes como ReactJS, Vue.js,
Nest.js y Fast API, sin distinguir mayúsculas. Solo el título no es suficiente.
TypeScript/JavaScript solos, o Python con Django/Flask sin otra tecnología de
la lista, no pasan. React junto a Python sí pasa por la mención de React.
No se distingue entre requisitos obligatorios, opcionales o menciones generales.

Se consultan como máximo **20 descripciones por ciclo**, de forma secuencial.
Las decisiones se reutilizan durante **6 horas**; los IDs descartados se guardan
sin su descripción para evitar repetir consultas. Las ofertas todavía no leídas
quedan pendientes y se revisan en ciclos posteriores. Las descripciones que no
se pueden leer no se publican; si fallan todas las consultas nuevas y no hay
caché utilizable, el ciclo se registra como error.
Los cambios posteriores en una descripción pueden tardar hasta seis horas en
volver a evaluarse; las ofertas nuevas se evalúan sin esperar ese plazo.

Puedes ajustar los límites con `--max-details 10` y
`--description-cache-hours 6`. El resumen de candidatas, aceptadas, descartadas,
pendientes y caché aparece en consola, reportes y configuración del frontend.
El backend repite el filtro antes de guardar y crear notificaciones.

La búsqueda inicial conserva `developer react`. Este filtro selecciona dentro
de los resultados encontrados; no amplía automáticamente la búsqueda a puestos
que solo mencionen Angular, Vue o FastAPI. Usa `--keywords` para otra búsqueda.
Las ofertas históricas existentes no se eliminan automáticamente.

| Parámetro | Valor |
|---|---|
| `keywords` | `developer react` |
| `location` | `Colombia` |
| `f_AL` | `true` (Easy Apply / Solicitud sencilla) |
| `f_TPR` | `r86400` (últimas 24 horas) |
| `f_WT` | `2` (remoto) |
| `sortBy` | `DD` (más recientes) |
| `origin` | `JOB_SEARCH_PAGE_JOB_FILTER` |

Se omite `currentJobId`: solo selecciona una oferta en el panel de detalle y no
es un filtro de búsqueda. La URL efectiva aparece en la consola y en los reportes.
Se usan los resultados que entregue LinkedIn: su búsqueda puede incluir cargos
relacionados aunque el título no contenga literalmente «React».

## Reportes

Cada consulta correcta guarda:

- `reports/latest.md`: reporte legible con cargo, empresa, ubicación, publicación
  cuando esté disponible y enlace. Separa ofertas nuevas de las ya vistas.
- `reports/latest.csv`: datos, descripción y tecnologías para Excel (UTF-8 con BOM).
- `reports/latest.json`: datos estructurados, descripción, tecnologías y resumen.
- `reports/history/AAAAMMDD_HHMMSS.json`: histórico de cada consulta correcta.
- `reports/jobs.sqlite3`: memoria de ofertas por ID, que sobrevive a los reinicios.
- `reports/status.json`: estado del último intento, incluidos fallos.

La primera consulta considera nuevas todas las ofertas encontradas. Después solo
son nuevas las que no existan en SQLite. La consola imprime los nuevos cargos y
sus enlaces; también informa cuando no hay novedades. Las fechas del reporte
usan `America/Bogota`. `latest.*` conserva la última consulta **correcta**; ante
un fallo consulta `status.json` y la fecha del reporte para evitar confusiones.

Por defecto se revisan hasta **3 páginas** (posiciones 0, 25 y 50), desplazando
el panel para cargar tarjetas. El reporte indica si se alcanzó el límite; no
promete cubrir todas las ofertas ni los cambios que ocurran entre consultas.
Lee las descripciones de los candidatos para aplicar el filtro; no presenta solicitudes.

El proceso debe permanecer ejecutándose y el equipo encendido y conectado.
Los quince minutos se cuentan entre inicios de consulta; si una consulta tarda
más, la siguiente comienza al terminar. Los errores transitorios aplican espera
creciente y el proceso se detiene tras tres fallos consecutivos. HTTP 403/429 o
verificación detienen el monitor inmediatamente.

## Otras opciones

```powershell
# Solo una consulta, útil para comprobar la configuración.
python linkedin_jobs.py --once

# Ver qué está mostrando LinkedIn durante una consulta.
python linkedin_jobs.py --once --visible

# Ampliar la cantidad de páginas y consultar cada treinta minutos.
python linkedin_jobs.py --max-pages 5 --interval 1800

# Ver todas las opciones.
python linkedin_jobs.py --help

# Detener el monitor: Ctrl+C en su terminal.
```

Usa una sola instancia por carpeta de reportes. La sesión del navegador es
temporal y se descarta al cerrar el proceso; las cookies se leen del JSON privado.
Las carpetas de perfiles antiguos siguen ignoradas por Git. LinkedIn puede
restringir la automatización y cambiar su HTML; en ese caso habrá que revisar
los selectores en `linkedin_jobs.py`. No se intenta eludir verificaciones.

Documentación: [contextos de navegador de Playwright](https://playwright.dev/python/docs/browser-contexts)
y [almacenamiento de autenticación](https://playwright.dev/python/docs/auth).

## Pruebas locales sin cuenta

```powershell
python -m unittest discover -s tests -v
```

Las pruebas cubren filtros, lectura de tarjetas, deduplicación entre ejecuciones,
fallos de escritura y una consulta simulada en Chromium. No prueban acceso a tu
cuenta ni garantizan que LinkedIn conserve los mismos selectores.

## Enviar las ofertas al backend REST/SSE

El backend FastAPI de `../backend` puede guardar las ofertas en PostgreSQL y
transmitir cambios por un único `GET /api/jobs/events`. Primero sigue
[`../backend/JOB_FEED.md`](../backend/JOB_FEED.md) para configurar
`JOBS_INGEST_TOKEN`, aplicar la migración y arrancar la API.

Configura la URL y el token en el `.env` privado del scraper. Python lo lee
automáticamente; para desarrollo basta con:

```powershell
python linkedin_jobs.py --api-url http://127.0.0.1:8000
```

Cada reporte se guarda junto con un lote pendiente en el SQLite local. El monitor
envía el lote a la API y lo elimina de la cola solo tras recibir confirmación. Una
caída de la API conserva las ofertas para reintentar; repetir un lote confirmado
no duplica ofertas ni eventos. Los estados de error y de verificación pendiente
también se sincronizan cuando hay conexión. La sesión de LinkedIn permanece local.

Para importar las ofertas ya guardadas y entregar lotes pendientes sin navegador:

```powershell
python linkedin_jobs.py --sync-only
```

Con `JOBS_API_URL` vacío se mantiene el funcionamiento local. Python carga
automáticamente el `.env` del proyecto, sin sobrescribir variables del entorno.
