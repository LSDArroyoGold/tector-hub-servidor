# Lanzamiento de Tector Hub

Guion para poner el sistema en marcha. Escrito el 10/9/2026.

Todo lo que está acá **necesita a alguien del laboratorio**: son autorizaciones
de cuentas de Google y sesiones interactivas que no se pueden automatizar. La
parte de código ya está hecha y probada.

Tiempo estimado: **una hora**, la mayor parte esperando a rclone.

---

## Antes de empezar

| | |
|---|---|
| Notebook con Tailscale **conectado** | Al 10/9 estaba deslogueado y `ssh celu` daba *timeout* |
| El S10e enchufado y en la tailnet | Con «VPN siempre activada» en Ajustes de Android |
| Acceso a `lsdarroyogold@gmail.com` | Para el respaldo |
| Acceso al Drive donde suben los Tectors | Para leer detecciones |

---

## 1 · Reconectar Tailscale  ·  *notebook*

```bash
tailscale status          # tiene que listar s10e-de-tomas
ssh celu "echo ok"
```

Si `ssh celu` no responde, nada de lo que sigue funciona. En el teléfono,
abrir la app de Tailscale y verificar que esté conectada.

> Pendiente viejo que conviene resolver de una vez: en
> login.tailscale.com/admin/machines, menú «...» → *Disable key expiry* para
> los dos equipos. Si no, a los ~6 meses la clave vence y el teléfono se cae
> de la red sin aviso.

## 2 · Instalar el servidor  ·  *S10e, por SSH*

```bash
ssh celu
proot-distro login debian          # la Debian de adentro, no Termux

apt update && apt install -y python3-venv rclone git
mkdir -p /opt && cd /opt
git clone https://github.com/LSDArroyoGold/tector-hub-servidor.git
cd tector-hub-servidor
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

> Las pruebas piden algo más (`httpx`), que está en `requirements-pruebas.txt`
> aparte a propósito: en el teléfono no hace falta. Para correrlas desde la
> notebook: `pip install -r requirements.txt -r requirements-pruebas.txt` y
> después `python -m pruebas.probar_api` y `python -m pruebas.probar_integracion`.

> `requirements.txt` trae `bcrypt`, que es código compilado. En la Debian
> aarch64 hay rueda y entra en segundos; si por lo que sea no la encontrara,
> `apt install -y build-essential libffi-dev` y reintentar. Es el único
> paquete con riesgo.

## 3 · Autorizar rclone, dos veces

**Una** para leer las detecciones, **otra** para el respaldo. A propósito
separadas: un respaldo guardado en la misma cuenta que los datos se pierde
junto con la cuenta.

```bash
rclone config
```

| Remoto | Cuenta | Para qué |
|---|---|---|
| `gdrive` | la que usan los Tectors | Leer detecciones, escribir configuración |
| `gdrive-lsd` | **lsdarroyogold@gmail.com** | Respaldo de la base |

Los dos: tipo `drive`, scope **`drive.file`**, OAuth de usuario.

> **No usar cuenta de servicio.** Google la rechaza con *"Service Accounts do
> not have storage quota"* al escribir en un Drive personal, aunque la
> carpeta esté compartida como Editor. Ya se perdió una tarde con esto el
> 6/9.

Como el teléfono no tiene navegador usable para el OAuth, rclone da un
comando `rclone authorize` para correr en la notebook y pegar el token de
vuelta. Verificar antes de seguir:

```bash
rclone lsd gdrive:
rclone lsd gdrive-lsd:
```

## 4 · Configurar y arrancar

```bash
mkdir -p /etc/tector-hub
cp systemd/entorno.ejemplo /etc/tector-hub/entorno
chmod 600 /etc/tector-hub/entorno
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
nano /etc/tector-hub/entorno
```

Completar `TECTOR_CLAVE_JWT` con lo que imprimió, y `TECTOR_RESPALDO_REMOTE=gdrive-lsd`.

Adentro del proot **no hay systemd**: el servicio va por el supervisor propio
del teléfono. Del lado de Termux:

```
# en ~/servidor/servicios.txt
tector-hub   .venv/bin/uvicorn servidor.main:app --host 0.0.0.0 --port 8099
```

```bash
bash ~/servidor/ctl.sh arrancar tector-hub
bash ~/servidor/ctl.sh estado
curl http://localhost:8099/salud
```

> `--host 0.0.0.0` y no `127.0.0.1`: tiene que ser alcanzable desde la
> tailnet. Lo que lo protege es que el teléfono solo está en la tailnet, no
> expuesto a internet.

Desde la notebook: `curl http://100.72.251.58:8099/salud`

## 5 · Respaldo diario

```bash
.venv/bin/python -m scripts.respaldar     # probarlo a mano una vez
crontab -e
```

```
17 3 * * *  cd /opt/tector-hub-servidor && .venv/bin/python -m scripts.respaldar
```

## 6 · Las cuentas

```bash
.venv/bin/python -m scripts.crear_usuario lsd "Laboratorio de Sistemas Dinámicos"
```

Imprime una contraseña generada, **una sola vez**. Es la cuenta que van a
compartir Diego, Gabo y Tomás en sus teléfonos: los tokens son independientes,
pueden estar los tres logueados a la vez sin pisarse.

## 7 · Dar de alta los Tectors

**Tector 1 (software 1.1)** — no hay que tocar el equipo:

```bash
.venv/bin/python -m scripts.precargar_serie 0001 --heredado --drive-path "Tector 1"
```

El número de serie vive solo acá: la 1.1 no lo conoce ni lo necesita.

> **Hay cambios en vuelo hacia ese equipo** (pusheados el 11/9). Los baja solo
> `actualizar_repo.sh` en la próxima apertura de ventana, sin que nadie haga
> nada:
>
> - **el borrado automático de Drive queda apagado** (`BORRAR_DE_DRIVE=NO` en
>   `limpiar_retencion.sh` — el interruptor va ahí y no en `config_general.txt`,
>   que no se sincroniza);
> - **empieza a escribir un resumen diario** en `Tector 1/Resumenes`, y en su
>   primera corrida resume de una todo el historial que hoy existe únicamente
>   como nombres de archivo.
>
> Conviene mirar `Tector 1/Resumenes` en Drive después de esa ventana: si están
> los CSV viejos, el historial quedó a salvo. Si no, la carpeta local ya se
> había vaciado y solo se resumirá de acá en adelante.

**Tector 2** — migrar a la 2.1 y correr el instalador:

```bash
ssh <tector2>
cd ~ && git clone https://github.com/LSDArroyoGold/LSD-Tector2.1.git
cd LSD-Tector2.1 && ./install.sh          # imprime el número de serie
nano config/config_general.txt            # SERVIDOR_URL=http://100.72.251.58:8099
```

Anotar el número de serie en la caja del equipo.

> **Orden que importa.** Recién *después* de que el Tector 2 esté en la 2.1,
> actualizar TectorNet en ese equipo. La 2.1 cuenta detecciones con
> `(?:birdnet|tectornet)-`; las versiones anteriores solo con `birdnet-`, así
> que al revés «Detecciones subidas» queda en 0 aunque el motor detecte bien.

## 8 · Precargar las fotos  *(opcional, mientras tanto)*

```bash
.venv/bin/python -m herramientas.precalentar_fotos --limite 300
```

## 9 · La app

Ya está publicada: **https://lsdarroyogold.github.io/tector-hub-app/**

Falta apuntarla al servidor. En `api.js`, arriba:

```js
const SERVIDOR_POR_DEFECTO = 'http://100.72.251.58:8099';
```

Commit y push; GitHub Pages reconstruye solo en un par de minutos.

> **Acá aparece el problema de siempre.** Pages sirve por HTTPS y el servidor
> habla HTTP: el navegador bloquea el pedido por contenido mixto y la app no
> va a poder hablarle. Hay dos caminos, y hay que elegir uno:
>
> **A · Servir la app también desde el S10e, por HTTP.** Todo funciona,
> incluido el asistente de sincronización en modo automático. Se pierde poder
> instalarla como PWA (Chrome exige HTTPS) — queda como acceso directo del
> navegador.
>
> ```
> # en ~/servidor/servicios.txt
> tector-app   python3 -m http.server 8080
> ```
>
> **B · Dejarla en Pages y poner el servidor detrás de HTTPS.** Se mantiene
> la PWA instalable y el asistente en modo guiado. Requiere un dominio y un
> certificado, o sea plata o un servicio de terceros.
>
> Para arrancar, **A** — se prueba todo el sistema de punta a punta hoy. **B**
> cuando el laboratorio decida si vale un dominio.

---

## Verificar que quedó andando

1. Abrir la app, entrar con la cuenta nueva
2. Vincular el Tector 1 con `0001`
3. Tienen que verse sus detecciones, escucharse un audio, y bajarse un zip
4. El chip de estado del Tector 1 tiene que decir algo real —batería en %,
   hora de la próxima ventana— y no «sin reporte de estado». Eso sale de su
   log, no de un `estado.json`, que esa versión no escribe; la pantalla del
   dispositivo lo aclara. Si dice «Quedó una ventana sin cerrar», el equipo
   dejó de escribir en medio de una ventana: mirar el log antes de seguir.
5. Cambiar la duración de una ventana y confirmar que el diálogo dice
   **cuándo** se va a aplicar
6. Reportar una detección y ver que aparece en
   `.venv/bin/python -m scripts.exportar_reportes`

## Lo que se sabe que falta

Nada de esto bloquea el lanzamiento; está acá para que no se descubra solo.

| | |
|---|---|
| Sin notificaciones | Decidido lanzar así. El panel guarda las preferencias pero no hay quién las envíe. |
| El Tector 2 no se apaga solo | Falta el circuito de corte de energía; la Pi queda encendida. Es anterior a todo esto y está documentado en `set_wake_rtc.py` y el README de la 2.1. |
| Un Drive por usuario | Para cuando haya Tectors de terceros. Hoy todo va a la cuenta del laboratorio. |

## Si algo no anda

```bash
bash ~/servidor/ctl.sh log tector-hub 50
```

El servidor escribe al log el motivo cuando no puede leer Drive. Los errores
de rclone salen ahí con todas las letras.
