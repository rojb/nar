"""Spanish texts shown to the user. Keys and placeholders must match messages_en.py."""

from __future__ import annotations

MESSAGES: dict[str, str] = {
    "repl.banner": "nar · {provider} · {model}. Escribe 'exit' para salir.",
    "warning.prefix": "aviso:",
    "approval.needed": "Se necesita aprobación: {title}",
    "approval.question": "¿Permitir {tool}? [s]í / [n]o / [a] siempre {tool} en esta sesión: ",
    "approval.session_noted": "{tool} no volverá a preguntar en esta sesión",
    "approval.invalid": "Responde s, n o a.",
    "approval.reason": "Motivo (opcional, Enter para omitir): ",
    "approval.shortened": "(vista previa acortada)",
    "approval.more_lines.one": "... {count} línea más sin mostrar",
    "approval.more_lines.other": "... {count} líneas más sin mostrar",
    "trust.header": "Este proyecto tiene archivos de herramientas en {folder}:",
    "trust.warning": "Se ejecutan como código con tus permisos en cuanto los permitas.",
    "trust.question": "¿Ejecutar estos archivos? [s]í / [n]o: ",
    "key.not_set": "{env} no está definida.",
    "key.paste": "Pega tu clave de API de {provider} (oculta; Enter para cancelar): ",
    "key.save": "¿Guardar {env} para tu usuario, para la próxima vez? [s]í / [n]o: ",
    "key.reason.ctrl_v": "Ctrl+V no pega aquí: pega con clic derecho",
    "key.reason.invisible": "la clave tiene caracteres invisibles; vuelve a pegarla",
    "key.reason.spaces": "una clave no tiene espacios; vuelve a pegarla",
}
