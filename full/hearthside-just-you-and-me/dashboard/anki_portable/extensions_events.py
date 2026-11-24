"""Local dated events; expiry follows the configured collection study day."""
import datetime,json,re,uuid
from .collection import CollectionError
KEY='study_events_v1'
MAX_EVENTS=100


def _date(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise CollectionError('Usá una fecha AAAA-MM-DD válida.')
    try:return datetime.date.fromisoformat(value)
    except ValueError:raise CollectionError('La fecha no existe.') from None


def _load(db):
    row=db.execute('SELECT value FROM metadata WHERE key=?',(KEY,)).fetchone()
    try:
        events=json.loads(row[0]) if row else []
        if not isinstance(events,list) or len(events)>MAX_EVENTS:raise ValueError()
        seen=set()
        for event in events:
            if not isinstance(event,dict) or set(event)!={'id','title','date'} or not re.fullmatch('[a-f0-9]{32}',str(event['id'])) or event['id'] in seen or not isinstance(event['title'],str) or not 1<=len(event['title'].strip())<=160:raise ValueError()
            _date(event['date']);seen.add(event['id'])
        return events
    except (ValueError,TypeError,KeyError,CollectionError):raise CollectionError('Los eventos guardados no son válidos; no se sobrescribieron.') from None


def read(collection,today=None):
    with collection._study_connection() as db:
        db.execute('BEGIN IMMEDIATE')
        day,_,next_rollover=collection._day(collection._now(None))
        rollover_hour=collection.full_preferences()['rollover_hour']
        today=today or datetime.date.fromisoformat(day)
        events=_load(db)
        kept=[e for e in events if _date(e['date'])>=today]
        if len(kept)!=len(events):
            db.execute('INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)',(KEY,json.dumps(kept,ensure_ascii=False)))
            db.commit()
        events=kept
    result=[dict(e,days=(_date(e['date'])-today).days) for e in events]
    result.sort(key=lambda e:(e['date'],e['title'].casefold(),e['id']))
    return {'version':1,'today':today.isoformat(),'rollover_hour':rollover_hour,'next_rollover_at':next_rollover,'events':result,'upcoming':[e for e in result if e['days']>=0],'expired':[]}


def save(collection,payload):
    if not isinstance(payload,dict) or set(payload)-{'id','title','date'}:raise CollectionError('Datos del evento no válidos.')
    title=payload.get('title');day=payload.get('date');identifier=payload.get('id')
    if not isinstance(title,str) or not 1<=len(title.strip())<=160 or any(ord(x)<32 for x in title):raise CollectionError('Escribí un título de 1 a 160 caracteres.')
    _date(day)
    with collection._study_connection() as db:
        db.execute('BEGIN IMMEDIATE');events=_load(db)
        if identifier is not None:
            event=next((e for e in events if e['id']==identifier),None)
            if event is None:raise CollectionError('El evento ya no existe.')
        else:
            if len(events)>=MAX_EVENTS:raise CollectionError('Máximo: 100 eventos.')
            event={'id':uuid.uuid4().hex};events.append(event)
        event.update(title=title.strip(),date=day)
        db.execute('INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)',(KEY,json.dumps(events,ensure_ascii=False)));db.commit()
    return dict(event)


def delete(collection,identifier):
    with collection._study_connection() as db:
        db.execute('BEGIN IMMEDIATE');events=_load(db);kept=[e for e in events if e['id']!=identifier]
        if len(kept)==len(events):raise CollectionError('El evento ya no existe.')
        db.execute('INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)',(KEY,json.dumps(kept,ensure_ascii=False)));db.commit()
    return {'deleted':identifier}
