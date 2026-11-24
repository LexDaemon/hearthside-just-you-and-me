"""Validated collection-scoped local preferences, independent of host settings."""
from copy import deepcopy
import hashlib,json,re
from .errors import CollectionError

KEY='review_preferences_v1'
DEFAULT={
 'learn_ahead_seconds':1200,'show_remaining':True,'rollover_hour':4,'timebox_seconds':0,
 'show_intervals':True,'spacebar_rates':True,'answer_keys':['1','2','3','4'],'generate_latex':False,
 'hide_top_bar':False,'hide_bottom_bar':False,'hide_top_mode':'fullscreen','hide_bottom_mode':'fullscreen',
 'reduce_motion':True,'minimalist':False,'paste_images_as_png':False,'paste_strips_formatting':False,
 'adding_uses_current_deck':True,'default_search':'','ignore_accents':False,
 'backup_interval_minutes':30,'backup_daily':12,'backup_weekly':10,'backup_monthly':9,
}
RANGES={'learn_ahead_seconds':(0,599940),'rollover_hour':(0,23),'timebox_seconds':(0,599940),
        'backup_interval_minutes':(5,9999),'backup_daily':(0,9999),'backup_weekly':(0,9999),'backup_monthly':(0,9999)}


def load(db):
    row=db.execute('SELECT value FROM metadata WHERE key=?',(KEY,)).fetchone()
    return dict(deepcopy(DEFAULT),**(json.loads(row[0]) if row else {}))


def version(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def validate(value):
    if not isinstance(value,dict) or set(value)!=set(DEFAULT):raise CollectionError('Las preferencias no son válidas.')
    for key,default in DEFAULT.items():
        val=value[key]
        if type(default) is bool and type(val) is not bool:raise CollectionError('La casilla '+key+' no es válida.')
        if key in RANGES and (type(val) is not int or not RANGES[key][0]<=val<=RANGES[key][1]):raise CollectionError('El valor de '+key+' está fuera de rango.')
    if any(value[k] not in ('fullscreen','always') for k in ('hide_top_mode','hide_bottom_mode')):raise CollectionError('Elegí cuándo esconder las barras.')
    keys=value['answer_keys']
    if not isinstance(keys,list) or len(keys)!=4 or any(not isinstance(k,str) or not re.fullmatch(r'(?:(?:Ctrl|Alt|Shift|Meta)\+)*(?:[^\s+]|F(?:[1-9]|1[0-2]))',k,re.I) for k in keys) or len({'+'.join(sorted(k.casefold().split('+')[:-1])+[k.casefold().split('+')[-1]]) for k in keys})!=4 or any(len(k.split('+')[:-1])!=len(set(k.casefold().split('+')[:-1])) for k in keys):raise CollectionError('Las cuatro teclas de respuesta deben ser válidas y distintas.')
    if not isinstance(value['default_search'],str) or len(value['default_search'])>4096 or '\x00' in value['default_search']:raise CollectionError('El texto de búsqueda predeterminado no es válido.')
    return deepcopy(value)


def save(db,value,expected_version=None):
    clean=validate(value)
    if expected_version is not None and expected_version!=version(load(db)):raise CollectionError('Las preferencias cambiaron desde que las abriste. Cerrá y volvé a abrir el diálogo.')
    db.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(KEY,json.dumps(clean,ensure_ascii=False)))
    return clean
