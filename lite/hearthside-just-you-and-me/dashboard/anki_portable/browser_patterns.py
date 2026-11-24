"""Timeout-bounded regular expressions; no Anki engine or scripts."""
from functools import lru_cache
import re
from .errors import CollectionError

MAX_PATTERN=4096
TIMEOUT=0.05


@lru_cache(maxsize=128)
def compile_pattern(pattern,case_sensitive=False,literal=False):
    if not isinstance(pattern,str) or not 1<=len(pattern)<=MAX_PATTERN:
        raise CollectionError('La expresión debe tener entre 1 y 4096 caracteres.')
    try:
        import regex
    except ImportError as exc:
        raise CollectionError('Falta la dependencia local regex del paquete. No se modificó la colección.') from exc
    if not literal and re.search(r'(?<!\\)\\[1-9]|\\g<|\(\?[=!>R0-9]|\(\?<[=!]|\(\?P=',pattern):
        raise CollectionError('Esa construcción no pertenece a la sintaxis de expresiones regulares admitida.')
    try:
        return regex.compile(regex.escape(pattern) if literal else pattern,regex.VERSION1|(0 if case_sensitive else regex.IGNORECASE))
    except (regex.error,OverflowError,RecursionError,MemoryError) as exc:
        raise CollectionError('La expresión regular no es válida.') from exc


def search(pattern,value):
    try:
        return int(bool(compile_pattern(pattern).search(str(value or ''),timeout=TIMEOUT)))
    except TimeoutError as exc:
        raise CollectionError('La expresión regular excedió el tiempo permitido.') from exc



def replacement_function(value,regular):
    """Expand the native $1/${name}/$name notation; $$ is a literal $."""
    if not isinstance(value,str) or len(value)>100000:raise CollectionError('El reemplazo no es válido.')
    token=re.compile(r'\$\$|\$\{([A-Za-z_][A-Za-z_0-9]*|[0-9]+)\}|\$([A-Za-z_][A-Za-z_0-9]*|[0-9]+)')
    def replace(match):
        if not regular:return value
        def expand(item):
            if item.group(0)=='$$':return '$'
            key=item.group(1) or item.group(2)
            try:return match.group(int(key) if key.isdecimal() else key) or ''
            except (IndexError,KeyError):return ''
        return token.sub(expand,value)
    return replace


def substitute(compiled,text,replacement):
    size=[0]
    def bounded(match):
        value=replacement(match);size[0]+=len(value)
        if size[0]>100000:raise CollectionError('El reemplazo excede el tamaño permitido para un campo.')
        return value
    try:result,count=compiled.subn(bounded,text,timeout=TIMEOUT)
    except TimeoutError as exc:raise CollectionError('El reemplazo excedió el tiempo permitido. No se modificó la colección.') from exc
    if len(result)>100000:raise CollectionError('El reemplazo excede el tamaño permitido para un campo.')
    return result,count
