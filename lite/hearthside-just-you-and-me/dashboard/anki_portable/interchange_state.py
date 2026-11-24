"""Versioned private metadata inside otherwise standard legacy Anki packages.

This preserves our own round-trips; it does not claim native Anki FSRS parity.
"""
import json
import math
from .errors import CollectionError
from .scheduler import dependencies, scheduler
from .study import validate_options, DEFAULT_OPTIONS


def card_state(raw):
    try:
        data = json.loads(raw or '{}')
    except (ValueError,TypeError):
        return None
    metadata = data.get('hearthside') if isinstance(data,dict) else None
    if metadata is None:
        return None
    try:
        if metadata['version'] != 1:
            raise ValueError('version')
        state = metadata['fsrs']
        if state is None:
            return None
        if type(state['state']) is not int or state['state'] not in (1,2,3):
            raise ValueError('state')
        if state['step'] is not None and (type(state['step']) is not int or not 0 <= state['step'] <= 8):
            raise ValueError('step')
        for name in ('stability','difficulty'):
            value = state[name]
            if value is not None and (type(value) not in (float,int) or not math.isfinite(value) or value <= 0):
                raise ValueError(name)
        Card,_,_,_,_ = dependencies()
        restored = Card.from_dict(state)
        if not 0 < restored.due.timestamp() < 253402214400 or restored.due.tzinfo is None:
            raise ValueError('due')
        if restored.last_review and (restored.last_review.tzinfo is None or restored.last_review > restored.due):
            raise ValueError('last_review')
        return restored.to_dict()
    except (KeyError,TypeError,ValueError,OverflowError) as exc:
        raise CollectionError('El paquete contiene un estado FSRS propio no válido. No se importó ningún dato.') from exc


def classic_state(raw):
    from .classic_scheduler import Card
    try:
        data=json.loads(raw or '{}')
        metadata=data.get('hearthside',{}) if isinstance(data,dict) else {}
        state=metadata.get('classic')
        if state is None:return None
        if metadata.get('version')!=1:raise ValueError('version')
        return json.loads(Card.from_json(json.dumps(state)).to_json())
    except (KeyError,TypeError,ValueError,OverflowError,AttributeError) as exc:
        raise CollectionError('El paquete contiene un estado clásico no válido. No se importó ningún dato.') from exc


def review_metadata(raw):
    try:
        data=json.loads(raw or '{}').get('hearthside_reviews',{})
        if not isinstance(data,dict):raise ValueError('reviews')
        for key,entry in data.items():
            if not key.isdecimal() or not isinstance(entry,dict):raise ValueError('review')
            if entry.get('scheduler') not in ('fsrs-6','sm2-classic-v1','legacy'):raise ValueError('scheduler')
            at=entry.get('reviewed_at')
            if type(at) is not int or not 0<=at<253402214400:raise ValueError('reviewed_at')
            log=entry.get('fsrs_log')
            if log is not None:
                if entry['scheduler']!='fsrs-6' or not isinstance(log,str) or len(log)>65536:raise ValueError('fsrs_log')
                raw_log=json.loads(log)
                if not isinstance(raw_log,dict):raise ValueError('fsrs_log')
                from fsrs import ReviewLog
                restored=ReviewLog.from_json(log)
                if restored.review_datetime.tzinfo is None or int(restored.review_datetime.timestamp())!=at:raise ValueError('review_datetime')
                detail=raw_log.get('_memoria_info_v1')
                if detail is not None:
                    if not isinstance(detail,dict) or set(detail)!={'interval_seconds','difficulty_after','state_before'}:raise ValueError('info')
                    seconds=detail['interval_seconds'];difficulty=detail['difficulty_after'];state=detail['state_before']
                    if type(seconds) is not int or not 0<=seconds<=36500*86400:raise ValueError('interval_seconds')
                    if type(difficulty) not in (int,float) or not math.isfinite(difficulty) or not 1<=difficulty<=10:raise ValueError('difficulty_after')
                    if type(state) is not int or state not in (1,2,3):raise ValueError('state_before')

        return data
    except (ValueError,TypeError,AttributeError,KeyError,OverflowError) as exc:
        raise CollectionError('Los metadatos de motores de repaso no son válidos.') from exc


def order_state(raw, position, added_at):
    try:
        data = json.loads(raw or '{}')
    except (ValueError,TypeError):
        data = {}
    metadata = data.get('hearthside',{}) if isinstance(data,dict) else {}
    if not isinstance(metadata,dict):
        raise CollectionError('Los metadatos de orden no son válidos.')
    result = (metadata.get('new_position',position),metadata.get('added_at',added_at))
    if any(type(v) is not int or not 0 <= v < 2**63-1 for v in result):
        raise CollectionError('Los metadatos de orden no son válidos.')
    return result


def deck_settings(decks):
    result = {}
    for deck in decks.values():
        metadata = deck.get('hearthside') if isinstance(deck,dict) else None
        if metadata is None:
            continue
        try:
            if metadata['version'] != 1:
                raise ValueError('version')
            options = metadata['options']
            validate_options(options)
            options = dict(DEFAULT_OPTIONS, **options)
            parameters = metadata.get('parameters')
            if parameters is not None:
                if not isinstance(parameters,list) or len(parameters) != 21 or any(type(v) not in (int,float) or not math.isfinite(v) for v in parameters):
                    raise ValueError('parameters')
                scheduler(options,parameters)
            result[deck['name']] = dict(options=options,parameters=parameters)
        except (KeyError,TypeError,ValueError) as exc:
            raise CollectionError('El paquete contiene opciones propias no válidas. No se importó ningún dato.') from exc
    return result


def history_skip(raw):
    try:
        data=json.loads(raw or '{}')
        metadata=data.get('hearthside',{}) if isinstance(data,dict) else {}
        value=metadata.get('review_history_skip',0)
        if type(value) is not int or not 0<=value<=100000000:raise ValueError('skip')
        return value
    except (ValueError,TypeError,AttributeError) as exc:
        raise CollectionError('El paquete contiene un reinicio de historial no válido.') from exc
