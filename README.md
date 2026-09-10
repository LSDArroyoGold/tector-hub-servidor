# Tector Hub — Servidor

Servicio que respalda la app móvil de administración de estaciones
**LSD-Tector**, del Laboratorio de Sistemas Dinámicos (FCEyN, UBA).

Hace tres cosas y nada más:

1. **Cuentas.** La lista de usuarios que pueden entrar a la app, en un
   servidor propio del proyecto, sin autenticación de terceros.
2. **Identidad de los dispositivos.** Registra el número de serie de cada
   Tector y resuelve colisiones. Guarda qué dispositivo es de quién.
3. **Puente a Google Drive.** Lee detecciones, logs y estado; escribe la
   configuración de horarios.

## Por qué existe el punto 3

La app podría hablarle a Drive directamente, pero entonces cada teléfono
tendría que llevar credenciales de escritura sobre las carpetas de todos los
Tectors de esa cuenta.

Este proyecto ya se quemó una vez con credenciales de Drive fuera de lugar: un
`rclone.conf` commiteado hizo que GitHub lo detectara por *secret scanning*,
Google revocara el token en silencio, y la sincronización se rompiera sin
ningún aviso, una semana después.

Con el servidor en el medio, las credenciales viven en un solo lugar
controlado y la app solo tiene un token de sesión revocable.

---

## Arquitectura

```
   App                Servidor              Google Drive           Tector
    │                     │                      │                   │
    ├── login ───────────►│                      │                   │
    │◄── token ───────────┤                      │                   │
    │                     │                      │                   │
    ├── GET detecciones ─►├── rclone lsjson ────►│                   │
    │◄── JSON ────────────┤◄─────────────────────┤                   │
    │                     │                      │                   │
    ├── PUT horarios ────►├── rclone rcat ──────►│                   │
    │                     │                      │◄── baja config ───┤
    │                     │                      │    al abrir/cerrar│
    │                     │                      │    ventana        │
    │                     │◄─────────────────────┤── sube estado.json┤
    │                     │                      │   detecciones,log │
    │                     │                                          │
    │                     │◄──── POST /dispositivos/registrar ───────┤
    │                     │      (primer arranque con internet)      │
```

El servidor **nunca** habla directamente con un Tector, y el Tector solo le
habla al servidor una vez, para registrarse. Todo lo demás pasa por Drive,
porque el dispositivo está apagado la mayor parte del tiempo y vive detrás del
router de quien lo hospeda.

---

## Instalación

Probado sobre Debian/Ubuntu y Raspberry Pi OS Bookworm. Necesita **Python
3.10 o mayor** y `rclone`.

### 1. Clonar e instalar dependencias

```bash
git clone https://github.com/LSDArroyoGold/tector-hub-servidor.git
cd tector-hub-servidor
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Autorizar rclone contra el Drive del proyecto

El servidor lee y escribe las mismas carpetas que usan los dispositivos, así
que necesita su propio acceso al Drive.

```bash
rclone config
```

Remoto de tipo `drive`, nombre `gdrive`. **Importante:** usar OAuth de usuario
con scope `drive.file`, no una cuenta de servicio — una cuenta de servicio no
puede escribir en un Drive personal (Google la rechaza con *"Service Accounts
do not have storage quota"*), aunque la carpeta esté compartida como Editor.
Es el mismo diagnóstico que ya se documentó en `config_general.txt` de
LSD-Tector2.0.

Verificar que quedó bien antes de seguir:

```bash
rclone lsd gdrive:
```

### 3. Configurar el entorno

```bash
sudo mkdir -p /etc/tector-hub
sudo cp systemd/entorno.ejemplo /etc/tector-hub/entorno
sudo chmod 600 /etc/tector-hub/entorno
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
sudo nano /etc/tector-hub/entorno   # pegar la clave en TECTOR_CLAVE_JWT
```

Sin `TECTOR_CLAVE_JWT`, el servidor arranca igual pero genera una clave al
azar en cada reinicio, y todas las sesiones de la app se cortan cada vez.

### 4. Crear la primera cuenta

```bash
.venv/bin/python -m scripts.crear_usuario d.arroyo "Diego Arroyo"
```

Imprime una contraseña generada, una sola vez. En la base queda solo su hash
bcrypt.

### 5. Levantar el servicio

```bash
sed -e "s|__BASE_PATH__|$(pwd)|g" -e "s|__USUARIO__|$USER|g" \
    systemd/tector-hub.service | sudo tee /etc/systemd/system/tector-hub.service
sudo systemctl daemon-reload
sudo systemctl enable --now tector-hub
systemctl status tector-hub
```

Escucha en `127.0.0.1:8099`. La documentación interactiva de la API queda en
`/docs`.

### 6. Exponerlo

El servicio escucha solo en localhost a propósito. Hay dos caminos, según qué
tan lejos tenga que llegar la app:

**Tailscale** — más simple y sin nada expuesto a internet. Los teléfonos del
laboratorio entran a la tailnet y le pegan a `http://<nombre>:8099`. Para un
puñado de usuarios conocidos, alcanza y sobra.

**Nginx con TLS** — si la app tiene que funcionar desde cualquier red:

```nginx
server {
    listen 443 ssl;
    server_name tector.ejemplo.ar;
    # ssl_certificate ... (certbot)
    location / {
        proxy_pass http://127.0.0.1:8099;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        # Los audios pueden tardar: rclone tiene que bajarlos de Drive.
        proxy_read_timeout 120s;
    }
}
```

Con el servidor expuesto a internet abierta, conviene además poner
`TECTOR_SOLO_SERIES_CONOCIDOS=1`, que hace que solo se puedan registrar
números de serie precargados a mano.

### 7. Apuntar los Tectors al servidor

En cada dispositivo, en `config/config_general.txt` de LSD-Tector2.1:

```
SERVIDOR_URL=https://tector.ejemplo.ar
```

Se aplica solo, en la próxima ventana, vía `actualizar_repo.sh`. Dejar la
clave **vacía** también es válido: el Tector graba, detecta y sube a Drive
igual, solo que no aparece en la app.

---

## La API

Documentación interactiva completa en `/docs`. Lo esencial:

| Método | Ruta | Para qué |
|---|---|---|
| `POST` | `/auth/login` | Devuelve token y si la cuenta ya tiene dispositivos |
| `GET` | `/auth/yo` | Datos de la sesión |
| `POST` | `/dispositivos/registrar` | **Lo llama el Tector**, sin token |
| `GET` | `/dispositivos` | Los Tectors de la cuenta, con su estado |
| `POST` | `/dispositivos/vincular` | Reclamar un Tector por su número de serie |
| `PATCH` | `/dispositivos/{serie}` | Cambiar el apodo |
| `DELETE` | `/dispositivos/{serie}` | Desvincular (no borra nada de Drive) |
| `GET` | `/dispositivos/{serie}/estado` | `estado.json` del equipo |
| `GET` | `/dispositivos/{serie}/fechas` | Días con detecciones |
| `GET` | `/dispositivos/{serie}/detecciones` | Detecciones, filtrables |
| `GET` | `/dispositivos/{serie}/audio?ruta=` | El mp3 de una detección |
| `GET` | `/dispositivos/{serie}/estadisticas` | Métricas del panel |
| `GET` | `/dispositivos/{serie}/horarios` | Lo escrito **y** lo vigente |
| `PUT` | `/dispositivos/{serie}/horarios` | Guarda horarios en Drive |
| `GET` | `/resumen` | Vista combinada de todos los Tectors |
| `GET` | `/especies/{nombre}` | Nombre científico, foto y atribución |
| `GET` | `/reportes/tipos` | Los cuatro tipos de reporte de error |
| `POST` | `/dispositivos/{serie}/reportes` | Reportar una detección equivocada |
| `GET` | `/reportes` | Los reportes de esta cuenta |
| `GET` | `/dispositivos/{serie}/descargar` | Zip de una carpeta de audios |

### Dos detalles que la app tiene que respetar

**`PUT /horarios` no aplica el cambio.** Escribe el archivo en Drive; el
Tector lo baja al abrir o cerrar su próxima ventana. La respuesta trae
`aplicado: false` y `se_aplica_en`, para que el diálogo de confirmación diga
cuándo va a pasar en vez de fingir que fue inmediato.

**`GET /horarios` devuelve dos cosas distintas.** `en_drive` es lo que la app
escribió; `en_dispositivo` es lo que el equipo está corriendo de verdad, según
su `estado.json`. Mientras hay un cambio pendiente, difieren — y mostrar esa
diferencia es más honesto que elegir una de las dos.

---

## Reportes de error

La app deja marcar que una detección estaba mal. **No deja confirmar que
estaba bien**, a propósito: si se pudiera, lo que llegaría sería una mezcla de
«escuché y estaba bien» con «toqué sin escuchar», indistinguibles entre sí. Un
reporte significa siempre lo mismo.

Cuatro tipos: no hay ningún ave, hay un ave pero no es esta (no sé cuál), hay
un ave pero no es esta (y la elijo de un selector con las 6297 especies del
vocabulario de BirdSet), y el canto está cortado o partido en dos —este último
apunta directo a `SILENCIO_FIN_EVENTO_S` y `DURACION_MAXIMA_EVENTO_S`, que son
parámetros calibrados y ajustables.

Para revisarlos:

```bash
python3 -m scripts.exportar_reportes reportes.csv
```

Se guardan la especie, la confianza y la fecha además de la ruta, porque los
audios viejos se borran por retención y el reporte tiene que seguir siendo
legible después.

## Precargar las fotos

`servidor/especies.py` resuelve cualquier especie sola la primera vez que
alguien la pide, así que **no hay lista de especies soportadas**. Lo único que
se nota sin precarga es que la primera vez tarda un segundo o dos.

```bash
python3 -m herramientas.precalentar_fotos --limite 200
python3 -m herramientas.precalentar_fotos            # las ~6300 del catálogo
```

Reanudable: se corta con Ctrl-C y al volver sigue donde quedó.

## Modelo de seguridad

**Contraseñas.** Hash bcrypt con sal. Un login con usuario inexistente también
corre el hash, contra uno falso: sin eso responde mucho más rápido que uno con
usuario válido y clave mala, y esa diferencia alcanza para enumerar quién
tiene cuenta.

**Registro de dispositivos sin token.** Es deliberado: un Tector recién
flasheado no tiene ninguna credencial que presentar. Lo que protege el sistema
es que registrarse no da acceso a nada — un Tector registrado no le pertenece
a nadie hasta que una cuenta lo reclama con su número de serie, y una vez
reclamado no lo puede reclamar otra.

**Aislamiento entre cuentas.** Toda ruta que toca datos de un Tector pasa por
`dispositivo_propio()`. Un dispositivo ajeno responde `404`, no `403`: un
`403` confirmaría que ese número de serie existe.

**Rutas de audio.** Se valida que la ruta pedida caiga dentro de la carpeta de
Drive de ese dispositivo, y se rechaza `..`. Sin ese chequeo, un usuario
podría pedir la carpeta de otro y leer detecciones ajenas.

---

## Lo que este servidor **no** hace

- **No guarda detecciones.** La fuente de verdad sigue siendo Drive, y el
  registro de cada detección sigue siendo su nombre de archivo. Acá solo se
  parsea y se cachea un par de minutos.
- **No habla con los Tectors.** No puede: están apagados casi todo el tiempo y
  detrás de un router ajeno.
- **No recupera contraseñas.** Con cuentas cargadas a mano, la recuperación es
  correr `crear_usuario.py` de nuevo. Si el sistema crece, esto hay que
  resolverlo bien.
- **No limita intentos de login.** Para una instalación en Tailscale no hace
  falta. Antes de exponerlo a internet abierta, agregar rate limiting (en
  nginx alcanza).
