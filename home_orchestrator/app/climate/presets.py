"""
Presets con nombre, en vez de un horario fijo.

Por que se elimino el horario: una franja "07:00-23:00 = confort" no sabe
si hay alguien de verdad en la habitacion — asume una rutina fija. Los
presets, combinados con presencia REAL medida ahora mismo (nunca prevista,
seria una caja negra — ver climate.py), se adaptan a lo que de verdad esta
pasando: si vuelves antes o tarde, la zona reacciona al instante en vez de
esperar a la hora programada.

Cada preset lleva DOS consignas independientes — calor ("invierno") y
frio ("verano") — no una sola, para poder decir "nunca por debajo de 21°C
en invierno, nunca por encima de 25°C en verano" dentro del MISMO preset
"Confort", sin duplicar presets por estacion. En una zona de un solo
sentido (solo calor o solo frio) basta con declarar el lado que aplica.

Estas consignas NO se leen de aqui en directo durante la decision de cada
ciclo: `parse_presets` solo se usa para SEMBRAR las entidades number.* la
primera vez que se crea la zona (ver number.py) — a partir de ahi el
valor vivo de esas entidades manda, para poder ajustarlas desde Lovelace o
una automatizacion sin volver a "Configurar". Por eso este modulo ya no
expone la temperatura resuelta de un preset, solo su NOMBRE activo — el
valor lo busca climate.py en las entidades number.* correspondientes.

`PRESET_AUTO` es el modo por defecto: deja que Climate Orchestrator elija
solo entre el preset "con presencia" y el "sin presencia" segun la
presencia real. Elegir CUALQUIER OTRO preset a mano (termostato, voz,
Google Home, un puente Matter/HomeKit) es una eleccion PERSISTENTE que se
queda fijada hasta que vuelvas a poner "Automatico" tu mismo.

`PRESET_MANUAL` es un preset especial mas: no lo declaras tu en
`presets_text` (como "Confort" o "Ausente"), lo activa SOLO climate.py
cuando ajustas la temperatura directamente desde la tarjeta del
termostato en vez de elegir un preset — a diferencia de la version
anterior de esto (una anulacion TEMPORAL de un par de horas), pasar a
"Manual" es tan persistente como cualquier otro preset: se queda con la
temperatura que hayas puesto hasta que tu mismo cambies a otro preset o a
"Automatico". Su valor no vive en una entidad number.* (no tiene sentido,
lo pones tu directo en el termostato) — climate.py lo guarda como su
propio estado, restaurado tras un reinicio igual que el resto.
"""

from __future__ import annotations

import math

PRESET_AUTO = "Automático"
PRESET_MANUAL = "Manual"


# Etiquetas admitidas al declarar los dos lados por nombre en vez de con
# la barra ("Ausente; calor=18; frio=27" en vez de "Ausente: 18/27").
_HEAT_LABELS = ("calor", "heat", "invierno")
_COOL_LABELS = ("frio", "frío", "cool", "verano")


def _finite_temp(value: float, name: str, raw: str) -> float:
    # BUG REAL, confirmado por fuzzing adversarial: `float("nan")`,
    # `float("inf")` y un literal que desborda a infinito (p.ej.
    # "1e400") no lanzan excepcion al convertirlos -- pasaban tal cual
    # el resto de validaciones (una comparacion con NaN es SIEMPRE
    # False, asi que ni siquiera el chequeo "calor < frio" lo detecta)
    # y envenenaban la consigna de ese preset para siempre, con el mismo
    # efecto irreversible que un NaN colandose en una lectura de sensor.
    if not math.isfinite(value):
        raise ValueError(f"«{raw.strip()}» no es una temperatura valida para «{name}»")
    return value


def _split_entries(text: str) -> list[str]:
    """Separa el texto en entradas de preset.

    BUG REAL, visto en produccion: una zona declarada con
    "Presente; calor=21; frio=25\\nAusente; calor=18; frio=27" (un preset
    por LINEA, sin ninguna coma) llegaba aqui como un unico trozo sin ":",
    `parse_presets` lanzaba, `zone_runner` se tragaba el ValueError y la
    zona se quedaba con CERO presets -- con `away_preset` apuntando a un
    nombre que ya no existia y, por tanto, todas las consignas a null: sin
    mandos en la tarjeta de HA ni en el cliente Matter, en ningun modo.

    Un salto de linea es un separador de entradas al menos tan natural
    como la coma, asi que se admiten los dos. El punto y coma se admite
    tambien, pero SOLO como separador de entradas cuando no se esta usando
    ya dentro de la entrada para separar nombre y valores -- por eso se
    resuelve por linea y no de golpe sobre todo el texto.
    """
    entries: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        # "Ausente; calor=18; frio=27" es UNA entrada, no tres: si la linea
        # trae etiquetas con "=", el ";" separa los campos de un mismo
        # preset. Sin etiquetas ("Confort: 21/25; Ausente: 17/28") el ";"
        # cumple el mismo papel que la coma.
        parts = [line] if "=" in line else [p for p in line.split(";") if p.strip()]
        for part in parts:
            entries.extend(c for c in part.split(",") if c.strip())
    return [e.strip() for e in entries if e.strip()]


def _parse_labelled_sides(fields: list[str], name: str) -> tuple[float, float] | None:
    """Interpreta "calor=21", "frio=25" (en cualquier orden, y con solo uno
    de los dos lados en zonas de un unico sentido). None si estos campos no
    usan etiquetas -- entonces decide `parse_presets` con el formato de la
    barra."""
    heat = cool = None
    for field in fields:
        if "=" not in field:
            return None
        label, _, raw = field.partition("=")
        label = label.strip().lower()
        try:
            value = float(raw.strip().rstrip("°CcFf ").strip() or raw.strip())
        except ValueError as e:
            raise ValueError(f"«{raw.strip()}» no es una temperatura valida para «{name}»") from e
        value = _finite_temp(value, name, raw)
        if label in _HEAT_LABELS:
            heat = value
        elif label in _COOL_LABELS:
            cool = value
        else:
            raise ValueError(
                f"«{label}» no es un lado valido en «{name}» — usa «calor» o «frío»"
            )
    if heat is None and cool is None:
        raise ValueError(f"«{name}» no declara ninguna temperatura")
    # Un solo lado declarado vale para una zona de un unico sentido; se
    # replica para que el otro lado no quede en null (ver zone_runner:
    # publicar un hueco rompe la tarjeta de HA y el puente Matter).
    return (heat if heat is not None else cool), (cool if cool is not None else heat)


def parse_presets(text: str) -> list[dict]:
    """Convierte el texto declarado en el asistente en una lista de
    presets. Cada preset es "Nombre: calor/frio" (dos consignas) o
    "Nombre: temperatura" (una sola, valida para el lado que corresponda
    en zonas de un solo sentido). Ejemplo: "Confort: 21/25, Ausente:
    17/28" o, en una zona solo de calor, "Confort: 21, Ausente: 17".

    Se admite tambien declarar los lados por nombre y un preset por linea:
        Presente; calor=21; frio=25
        Ausente; calor=18; frio=27

    Lanza ValueError con un mensaje legible si el texto no tiene el
    formato esperado."""
    presets: list[dict] = []
    seen = set()
    for chunk in _split_entries(text):
        # El nombre se separa de los valores con ":" o, en el formato de
        # etiquetas, con el primer ";".
        if ":" in chunk:
            name, temps_str = chunk.split(":", 1)
            fields = [f for f in temps_str.split(";") if f.strip()]
        elif ";" in chunk:
            name, _, rest = chunk.partition(";")
            fields = [f for f in rest.split(";") if f.strip()]
        else:
            raise ValueError(f"«{chunk}» no tiene el formato «Nombre: temperatura»")
        name = name.strip()
        if not name or name in (PRESET_AUTO, PRESET_MANUAL):
            raise ValueError(f"«{name}» no es un nombre de preset valido")
        if name in seen:
            raise ValueError(f"el preset «{name}» esta repetido")
        seen.add(name)

        labelled = _parse_labelled_sides(fields, name) if any("=" in f for f in fields) else None
        if labelled is not None:
            heat_temp, cool_temp = labelled
            if heat_temp >= cool_temp and heat_temp != cool_temp:
                raise ValueError(
                    f"«{name}»: la consigna de calor ({heat_temp}°C) tiene que ser menor que la de frío "
                    f"({cool_temp}°C)"
                )
            presets.append({"name": name, "heat_temp": heat_temp, "cool_temp": cool_temp})
            continue

        temps_str = ";".join(fields).strip()
        if "/" in temps_str:
            heat_str, cool_str = temps_str.split("/", 1)
            try:
                heat_temp = float(heat_str.strip())
                cool_temp = float(cool_str.strip())
            except ValueError as e:
                raise ValueError(f"«{temps_str}» no es un par valido «calor/frio» para «{name}»") from e
            heat_temp = _finite_temp(heat_temp, name, heat_str)
            cool_temp = _finite_temp(cool_temp, name, cool_str)
            # Con calor >= frio (p.ej. "25/21" en vez de "21/25", el orden
            # invertido) la zona en Auto no tendria NINGUNA temperatura
            # que la deje tranquila: por debajo de 25 "hace falta calor",
            # por encima de 21 "hace falta frio" — las dos cosas a la vez,
            # siempre, sin importar la temperatura real. En la practica
            # eso es o bien calor y frio luchando entre si sin parar (el
            # peor derroche posible, ver `_async_execute`/mutual
            # exclusion), o un ciclado constante entre los dos — nunca un
            # estado estable. Se corta aqui, en vez de dejar que la zona
            # lo sufra en produccion.
            if heat_temp >= cool_temp:
                raise ValueError(
                    f"«{name}»: la consigna de calor ({heat_temp}°C) tiene que ser menor que la de frío "
                    f"({cool_temp}°C) — si no, Auto no encontraría nunca una temperatura que no pidiera las dos "
                    "cosas a la vez"
                )
        else:
            try:
                heat_temp = cool_temp = float(temps_str)
            except ValueError as e:
                raise ValueError(f"«{temps_str}» no es una temperatura valida para «{name}»") from e
            heat_temp = cool_temp = _finite_temp(heat_temp, name, temps_str)

        presets.append({"name": name, "heat_temp": heat_temp, "cool_temp": cool_temp})
    if not presets:
        raise ValueError("declara al menos un preset")
    return presets


def resolve_active_preset_name(preset_mode: str, preset_names: list[str], presence_preset: str,
                                away_preset: str, presence_now: bool | None) -> tuple[str, str]:
    """Devuelve (nombre_del_preset_activo, motivo) — la TEMPERATURA de ese
    preset se busca aparte, en las entidades number.* que lo respaldan
    (ver climate.py: `_preset_value`).

    `presence_now`: True/False si hay lectura fiable de los sensores de
    presencia FISICA declarados (ver climate.py — pensados para ser
    sensores de presencia de la propia habitacion, tipo PIR o mmWave, no
    solo "en casa"), None si no hay ninguno declarado o ninguno da un dato
    fiable ahora mismo.
    """
    if preset_mode == PRESET_MANUAL:
        return PRESET_MANUAL, "modo manual: temperatura fijada a mano"

    if preset_mode != PRESET_AUTO and preset_mode in preset_names:
        return preset_mode, f"preset «{preset_mode}» fijado a mano"

    if presence_now is None:
        return away_preset, "automático sin sensor de presencia fiable: usando el preset de ausencia"
    if presence_now:
        return presence_preset, "automático: presencia detectada en la zona"
    return away_preset, "automático: sin presencia en la zona"
