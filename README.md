# Monitor de trabajos React en Colombia

Guía paso a paso para ejecutar y probar los tres proyectos:
[GUIA_PRUEBAS.md](https://github.com/antonioace/scraping_jobs_backend/blob/main/GUIA_PRUEBAS.md).

Script en Python con Playwright. Consulta LinkedIn cada **900 segundos (15 minutos)**
y genera reportes locales. Usa tu propia sesión, guardada en un perfil exclusivo de
este proyecto, o importada desde un JSON privado de cookies; no requiere escribir
contraseñas ni cookies en el código.

## Usar tu sesión mediante cookies JSON

Puedes reutilizar una sesión propia válida sin abrir el login del scraper.
Esto simplifica el acceso; la consulta sigue usando Chromium y no se hace más
rápida por importar cookies. Una cuenta restringida o una sesión revocada no se
recuperan copiando cookies. El monitor se detiene ante verificación o bloqueo.

En este PC está creado `linkedin.cookies.json` para rellenarlo. En otro equipo,
copia `linkedin.cookies.example.json` a `linkedin.cookies.json` **solo si no existe**.
El archivo real está excluido de Git y de la imagen Docker; la plantilla pública
contiene únicamente un valor vacío.

1. Abre LinkedIn en tu navegador habitual con tu sesión iniciada.
2. En DevTools (`F12`), entra en **Application / Aplicación → Storage /
   Almacenamiento → Cookies → https://www.linkedin.com**. En Firefox, usa la
   pestaña Almacenamiento. Copia el valor completo de `li_at` al campo `value`
   del JSON. Al ser HttpOnly no se obtiene con `document.cookie`.
3. Mantén `name`, `domain` y `path` como aparecen en la plantilla. Si dispones
   de un export JSON de las cookies de LinkedIn, también puedes usarlo: acepta
   una lista de cookies o un objeto `{"cookies": [...]}`. No exportes cookies de
   otros sitios. Puedes incluir `JSESSIONID` de la misma sesión si está disponible;
   conserva su valor exacto, escapando sus comillas como `\"` en JSON.
4. Guarda el archivo y ejecuta el monitor. Nunca pegues estas cookies en chats,
   capturas, `.env` o el frontend. No necesitas instalar una extensión para copiar
   `li_at` desde DevTools.

### Windows (desde la carpeta del scraper)

Después de instalar las dependencias y Chromium como se indica más abajo:

```powershell
# Consulta local sin login interactivo, usando las cookies que rellenaste.
.\.venv\Scripts\python.exe linkedin_jobs.py --cookies-file .\linkedin.cookies.json --require-session --once

# Enviar resultados a tu API local, leyendo el token del .env privado existente.
..\backend\.venv\Scripts\python.exe -m dotenv -f .env run -- .\.venv\Scripts\python.exe linkedin_jobs.py --cookies-file .\linkedin.cookies.json --require-session --api-url http://127.0.0.1:8000
```

Para crear el JSON en un clon nuevo sin sobrescribir uno existente:

```powershell
if (!(Test-Path .\linkedin.cookies.json)) { Copy-Item .\linkedin.cookies.example.json .\linkedin.cookies.json }
```

### Producción Linux con Docker

Mantén el `.env` del scraper con la URL y el token del backend. El Compose
opcional pertenece **solo a este proyecto** y añade el montaje del JSON privado:

```bash
cd ~/job-tracker/scrap_jobs
if [ ! -f linkedin.cookies.json ]; then
  cp linkedin.cookies.example.json linkedin.cookies.json
fi
nano linkedin.cookies.json
# El contenedor usa UID 10001; necesita permiso de lectura del archivo.
sudo chown 10001:10001 linkedin.cookies.json
sudo chmod 600 linkedin.cookies.json
docker compose stop worker
docker compose -f docker-compose.yml -f docker-compose.cookies.yml up -d --build worker
docker compose -f docker-compose.yml -f docker-compose.cookies.yml logs -f worker
```

No necesitas arrancar el servicio `login` ni abrir el escritorio temporal con este
método. Conserva ambos `-f` en las siguientes operaciones `up`/`run` para mantener
el montaje. El archivo se monta en `/run/secrets/linkedin.cookies.json` como solo
lectura; no forma parte de la imagen. La red `web` y la ausencia de Traefik para
el worker se conservan. Para editarlo después, detén el worker, usa
`sudoedit linkedin.cookies.json` y vuelve a ejecutar el comando `up` con ambos archivos.

El JSON se lee una vez por arranque y reemplaza las cookies del perfil exclusivo
del scraper. Si cambias de cuenta, comprueba que el archivo completo corresponde
a una sola sesión. Si LinkedIn pide autenticación, renueva la sesión manualmente,
actualiza el archivo y reinicia el worker. No cambia de método ni reintenta el
login automáticamente cuando el JSON está ausente, vacío, caducado o inválido.
Las cookies nunca se envían al backend; el perfil persistente también contiene
la sesión importada y debe mantenerse privado.

También puedes configurar `LINKEDIN_COOKIES_FILE` con una **ruta**, sin incluir
valores de cookies en `.env`. `--cookies-file` tiene prioridad sobre esa variable.
Python directo no carga `.env` automáticamente. `--login` explícito permite
volver al acceso interactivo y omite la ruta configurada en el entorno.

Referencia: [importar cookies con Playwright](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-add-cookies).

## Linux / Docker

Este proyecto tiene `Dockerfile` y `docker-compose.yml` propios. Usa la red externa
`web` ya existente y arranca después del backend en la misma máquina.
Copia `.env.example` a `.env`, usa `JOBS_API_URL=http://job-tracker-api:8000` y el mismo
`JOBS_INGEST_TOKEN` del backend. No necesita claves VAPID, Firebase Admin, conexión a
PostgreSQL ni contraseña de LinkedIn en `.env`. La sesión se importa desde cookies
como se explica arriba, o se guarda mediante el login interactivo que sigue.
Los `.env` privados no se publican en GitHub: al clonar en Linux debes crearlos
o copiarlos de forma privada. Conserva el mismo token en backend y worker.

```bash
docker compose build worker login
# Primera vez o renovación: detener worker antes de usar su perfil.
docker compose stop worker
docker compose run --rm --service-ports login
# Después de completar LinkedIn en el escritorio temporal:
docker compose up -d worker
docker compose logs -f worker
```

Para acceder al login desde tu PC, abre un túnel SSH
`ssh -N -L 6080:127.0.0.1:6080 usuario@servidor` y visita
[el escritorio temporal](http://127.0.0.1:6080/vnc.html). El navegador se cierra
al confirmar la sesión, conservada en el volumen privado `linkedin-data`.
No abras el puerto 6080 a Internet ni copies perfiles de Windows a Linux.
Worker y login tienen `traefik.enable=false`, sin rutas públicas.

El worker arranca con `--require-session`: si falta la sesión se detiene en lugar
de intentar abrir una ventana invisible. No se reinicia automáticamente ante
verificación de LinkedIn o fallo fatal. `docker compose stop worker` registra un
estado de parada y lo pone en la cola de entrega a la API.

Guía completa:
[DEPLOY_LINUX.md del backend](https://github.com/antonioace/scraping_jobs_backend/blob/main/DEPLOY_LINUX.md).
Los Compose son independientes y no se incluyen workflows de GitHub Actions.

## Preparación (PowerShell, Python 3.10 o superior)

Docker Compose carga `.env` automáticamente. Al ejecutar Python directamente,
exporta `JOBS_API_URL` y `JOBS_INGEST_TOKEN` en la terminal (ver la sección de
sincronización con la API); el script no carga `.env` por sí solo. Fuera de Docker,
usa una URL accesible desde ese equipo, por ejemplo `http://127.0.0.1:8000`, en
lugar del alias Docker `job-tracker-api`.

Desde esta carpeta:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

## Iniciar sesión y ejecutar

```powershell
# Abre Chromium: ingresa tú y completa la verificación del celular.
.\.venv\Scripts\python.exe linkedin_jobs.py --login

# Primer reporte inmediato y luego uno cada quince minutos, en headless.
.\.venv\Scripts\python.exe linkedin_jobs.py
```

El login se detecta al encontrar una cookie de sesión junto con la navegación de
la cuenta iniciada. El navegador se cierra y conserva el perfil local. También
puedes ejecutar directamente el segundo comando: abre el login si el perfil aún
no existe. Si caduca la sesión o LinkedIn solicita una verificación, el monitor
se detiene; renueva con `--login` y vuelve a iniciarlo.

La primera vez tienes cinco minutos para completar el acceso. Para ampliar ese
plazo: `linkedin_jobs.py --login --login-timeout 600`.

## Filtros

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
- `reports/latest.csv`: los mismos datos para abrir en Excel (UTF-8 con BOM).
- `reports/latest.json`: datos estructurados, fecha de consulta y resumen.
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
No descarga descripciones completas ni presenta solicitudes.

El proceso debe permanecer ejecutándose y el equipo encendido y conectado.
Los quince minutos se cuentan entre inicios de consulta; si una consulta tarda
más, la siguiente comienza al terminar. Los errores transitorios aplican espera
creciente y el proceso se detiene tras tres fallos consecutivos. HTTP 403/429 o
verificación detienen el monitor inmediatamente.

## Otras opciones

```powershell
# Solo una consulta, útil para comprobar la configuración.
.\.venv\Scripts\python.exe linkedin_jobs.py --once

# Ver qué está mostrando LinkedIn durante una consulta.
.\.venv\Scripts\python.exe linkedin_jobs.py --once --visible

# Ampliar la cantidad de páginas y consultar cada treinta minutos.
.\.venv\Scripts\python.exe linkedin_jobs.py --max-pages 5 --interval 1800

# Ver todas las opciones.
.\.venv\Scripts\python.exe linkedin_jobs.py --help

# Detener el monitor: Ctrl+C en su terminal.
```

Usa una sola instancia por perfil de navegador y carpeta de reportes. El perfil
`.linkedin-profile/` contiene una sesión privada y está excluido de Git junto
con los reportes; no lo compartas ni lo subas al repositorio. LinkedIn puede
restringir la automatización y cambiar su HTML; en ese caso habrá que revisar
los selectores en `linkedin_jobs.py`. No se intenta eludir verificaciones.

Documentación: [perfil persistente de Playwright](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context)
y [almacenamiento de autenticación](https://playwright.dev/python/docs/auth).

## Pruebas locales sin cuenta

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Las pruebas cubren filtros, lectura de tarjetas, deduplicación entre ejecuciones,
fallos de escritura y una consulta simulada en Chromium. No prueban acceso a tu
cuenta ni garantizan que LinkedIn conserve los mismos selectores.

## Enviar las ofertas al backend REST/SSE

El backend FastAPI de `../backend` puede guardar las ofertas en PostgreSQL y
transmitir cambios por un único `GET /api/jobs/events`. Primero sigue
[`../backend/JOB_FEED.md`](../backend/JOB_FEED.md) para configurar
`JOBS_INGEST_TOKEN`, aplicar la migración y arrancar la API.

En la terminal del scraper configura la misma clave y la URL base del backend:

```powershell
$env:JOBS_API_URL = "http://localhost:8000"
$env:JOBS_INGEST_TOKEN = "LA_MISMA_CLAVE_DEL_BACKEND"
.\.venv\Scripts\python.exe linkedin_jobs.py
```

Cada reporte se guarda junto con un lote pendiente en el SQLite local. El monitor
envía el lote a la API y lo elimina de la cola solo tras recibir confirmación. Una
caída de la API conserva las ofertas para reintentar; repetir un lote confirmado
no duplica ofertas ni eventos. Los estados de error y de verificación pendiente
también se sincronizan cuando hay conexión. La sesión de LinkedIn permanece local.

Para importar las ofertas ya guardadas y entregar lotes pendientes sin navegador:

```powershell
.\.venv\Scripts\python.exe linkedin_jobs.py --sync-only
```

Sin URL de API, se mantiene el funcionamiento local. Las variables del scraper
se toman del entorno de su proceso; el script no carga un archivo `.env`.
