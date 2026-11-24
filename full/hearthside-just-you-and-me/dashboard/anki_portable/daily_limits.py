"""Shared quota defaults and scoped absolute overrides; no scheduler formulas."""
import json
from .errors import CollectionError

KEYS = ('new_per_day', 'reviews_per_day')
EXTRAS = {'new_per_day':'new_extra', 'reviews_per_day':'review_extra'}
CONFIG_KEY = 'daily_limit_config_v1'
TODAY_KEY = 'daily_limit_today_v1'
TODAY_MAX = 136500


def _read(db, key, default):
    row = db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
    if row is None: return default
    try:
        result = json.loads(row['value'])
        if not isinstance(result, dict): raise ValueError()
        return result
    except (ValueError, TypeError) as exc:
        raise CollectionError('Los límites diarios guardados no son válidos.') from exc


def _write(db, key, value):
    db.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value WHERE metadata.value<>excluded.value', (key,json.dumps(value,sort_keys=True)))


def _value(value, maximum, nullable=False):
    if value is None and nullable: return value
    if type(value) is not int or not 0 <= value <= maximum:
        raise CollectionError('El límite diario debe ser un entero entre 0 y %s.' % maximum)
    return value


def config(db):
    from .study import DEFAULT_OPTIONS
    saved = _read(db, CONFIG_KEY, {})
    return {key:_value(saved.get(key,DEFAULT_OPTIONS[key]),36500) for key in KEYS}


def _stored(db, deck_id):
    row = db.execute('SELECT options_json FROM deck_options WHERE deck_id=?',(deck_id,)).fetchone()
    return json.loads(row['options_json']) if row else {}


def _today(db, day):
    saved = _read(db,TODAY_KEY,{})
    if saved.get('day') != day: return {}
    decks = saved.get('decks',{})
    if not isinstance(decks,dict): raise CollectionError('Los límites diarios guardados no son válidos.')
    return decks


def _write_today(db,day,decks):
    decks = {str(deck):{key:value for key,value in values.items() if key in KEYS and value is not None} for deck,values in decks.items()}
    decks = {deck:values for deck,values in decks.items() if values}
    if decks: _write(db,TODAY_KEY,{'day':day,'decks':decks})
    else: db.execute('DELETE FROM metadata WHERE key=?',(TODAY_KEY,))


def _extras(db,deck_id,day):
    row = db.execute('SELECT new_extra,review_extra FROM daily_limit_overrides WHERE deck_id=? AND day=?',(deck_id,day)).fetchone()
    return dict(row) if row else {'new_extra':0,'review_extra':0}


def today_values(db,deck_id,day,permanent):
    explicit = _today(db,day).get(str(deck_id),{})
    extras = _extras(db,deck_id,day)
    values = {}
    for key in KEYS:
        value = explicit.get(key)
        if value is None and extras[EXTRAS[key]]:
            value = permanent[key] + extras[EXTRAS[key]]
        values[key] = _value(value,TODAY_MAX,nullable=True)
    return values


def info(collection,db,deck_id,now):
    permanent = collection._options(db,deck_id)
    saved = _stored(db,deck_id)
    day = collection._day(now)[0]
    return {'version':1,'day':day,'config':config(db),
            'deck':{key:saved.get(key) for key in KEYS},
            'today':today_values(db,deck_id,day,permanent)}


def apply_today(db,options,day):
    # One metadata read and one legacy-extra query for the entire collection.
    overrides = _today(db,day)
    extras = {row['deck_id']:dict(row) for row in db.execute('SELECT * FROM daily_limit_overrides WHERE day=?',(day,))}
    for deck_id,values in options.items():
        explicit = overrides.get(str(deck_id),{})
        for key in KEYS:
            value = explicit.get(key)
            values[key] = _value(value,TODAY_MAX) if value is not None else values[key]+extras.get(deck_id,{}).get(EXTRAS[key],0)


def validate_patch(payload,day):
    if not isinstance(payload,dict) or set(payload)-{'day','config','deck','today'}:
        raise CollectionError('Los alcances de los límites diarios no son válidos.')
    if payload.get('day') != day:
        raise CollectionError('Cambió el día de estudio. Cerrá y volvé a abrir las opciones antes de guardar los límites diarios.')
    for scope in ('config','deck','today'):
        if scope not in payload: continue
        values = payload[scope]
        if not isinstance(values,dict) or set(values)-set(KEYS):
            raise CollectionError('Los alcances de los límites diarios no son válidos.')
        for value in values.values(): _value(value,TODAY_MAX if scope=='today' else 36500,nullable=scope!='config')


def freeze_legacy(collection,db,day):
    # Legacy custom-study boosts used additive rows. Preserve their exact current
    # totals before a permanent/shared setting changes; no migration on reads.
    active = _today(db,day)
    changed = False
    for row in db.execute('SELECT * FROM daily_limit_overrides WHERE day=?',(day,)).fetchall():
        if not any(row[EXTRAS[key]] for key in KEYS): continue
        permanent = collection._options(db,row['deck_id'])
        values = active.setdefault(str(row['deck_id']),{})
        for key in KEYS:
            if row[EXTRAS[key]] and values.get(key) is None:
                values[key] = _value(permanent[key]+row[EXTRAS[key]],TODAY_MAX)
                changed = True
    if changed: _write_today(db,day,active)


def save(db,deck_id,payload,day):
    if payload.get('config'):
        shared = config(db);shared.update(payload['config']);_write(db,CONFIG_KEY,shared)
    if payload.get('deck'):
        stored = _stored(db,deck_id)
        for key,value in payload['deck'].items():
            if value is None: stored.pop(key,None)
            else: stored[key] = value
        db.execute('UPDATE deck_options SET options_json=? WHERE deck_id=?',(json.dumps(stored),deck_id))
    if payload.get('today'):
        active = _today(db,day);values = active.setdefault(str(deck_id),{})
        for key,value in payload['today'].items():
            if value is None: values.pop(key,None)
            else: values[key] = value
            # An absolute edit/clear replaces the old additive custom-study boost.
            db.execute('UPDATE daily_limit_overrides SET '+EXTRAS[key]+'=0 WHERE deck_id=? AND day=?',(deck_id,day))
        _write_today(db,day,active)


def clear_today(db,deck_id,day):
    active = _today(db,day);active.pop(str(deck_id),None);_write_today(db,day,active)
    db.execute('DELETE FROM daily_limit_overrides WHERE deck_id=? AND day=?',(deck_id,day))


def increase(collection,db,deck_id,key,amount,day):
    if not amount: return
    permanent = collection._options(db,deck_id)
    current = today_values(db,deck_id,day,permanent)[key]
    current = permanent[key] if current is None else current
    active = _today(db,day);active.setdefault(str(deck_id),{})[key] = _value(current+amount,TODAY_MAX)
    _write_today(db,day,active)


def remove_decks(db,ids):
    saved = _read(db,TODAY_KEY,{})
    if not saved: return
    decks = saved.get('decks',{})
    for deck_id in ids: decks.pop(str(deck_id),None)
    _write_today(db,saved.get('day'),decks)
