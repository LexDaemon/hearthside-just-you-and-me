"""Keyword matches and local note previews; no scheduling or stored HTML writes."""
import html,json,re
from .errors import CollectionError
from .authoring import FieldHTML
LINK=re.compile(r'(?<!\\)\[((?:\\.|[^\]\\]){1,200})\|nid([1-9][0-9]{0,18})\]')
MAX_MATCHES=50


def _id(value):
    if type(value) is int and 0<value<2**63-1:return value
    if isinstance(value,str) and re.fullmatch(r'[1-9][0-9]{0,18}',value) and int(value)<2**63-1:return int(value)
    raise CollectionError('El identificador de la nota o tarjeta no es válido.')


def plain(value):
    parser=FieldHTML();parser.feed(value);parser.close();return ' '.join(' '.join(parser.text).split())


def extract(markup):
    found={}
    for match in LINK.finditer(markup):
        try:identifier=str(_id(match[2]))
        except CollectionError:continue
        found.setdefault(identifier,{'note_id':identifier,'title':html.unescape(re.sub(r'\\(.)',r'\1',match[1]))})
    return list(found.values())


def matches(collection,payload):
    if not isinstance(payload,dict):raise CollectionError('La búsqueda no es válida.')
    keyword=payload.get('keyword');deck_id=payload.get('deck_id');exclude=payload.get('exclude_note_id')
    if not isinstance(keyword,str) or not 1<=len(keyword.strip())<=100 or any(ord(ch)<32 for ch in keyword):raise CollectionError('Seleccioná una palabra o frase de hasta 100 caracteres.')
    if type(deck_id) is not int or deck_id<1:raise CollectionError('Elegí el mazo donde buscar los enlaces.')
    exclude=_id(exclude) if exclude is not None else None
    keyword=keyword.strip();needle=keyword.casefold();result=[]
    escaped=re.sub(r'([\\\[\]|])',r'\\\1',keyword)
    with collection._study_connection() as db:
        db.execute('PRAGMA query_only=ON')
        if not db.execute('SELECT 1 FROM decks WHERE id=?',(deck_id,)).fetchone():raise CollectionError('El mazo ya no existe.')
        rows=db.execute('SELECT n.id,n.fields_json FROM notes n WHERE EXISTS(SELECT 1 FROM cards c WHERE c.note_id=n.id AND c.deck_id=?) ORDER BY n.id',(deck_id,))
        for row in rows:
            if row['id']==exclude:continue
            fields=json.loads(row['fields_json']);text=[plain(f) for f in fields]
            if not any(needle in field.casefold() for field in text):continue
            result.append({'note_id':str(row['id']),'title':text[0][:160] if text else 'Nota '+str(row['id']),'link':f'[{escaped}|nid{row["id"]}]'})
            if len(result)>MAX_MATCHES:raise CollectionError('Hay más de 50 coincidencias; seleccioná una frase más precisa. No se insertó ningún enlace.')
    return {'keyword':keyword,'matches':result}


def target(collection,note_id,card_id=None):
    note_id=_id(note_id)
    with collection._study_connection() as db:
        db.execute('PRAGMA query_only=ON');note=db.execute('SELECT fields_json FROM notes WHERE id=?',(note_id,)).fetchone()
        if note is None:raise CollectionError('La nota enlazada ya no existe en esta colección.')
        ids=[r[0] for r in db.execute('SELECT id FROM cards WHERE note_id=? ORDER BY ordinal,id',(note_id,))]
        if not ids:raise CollectionError('La nota enlazada no tiene tarjetas.')
        selected=_id(card_id) if card_id is not None else ids[0]
        if selected not in ids:raise CollectionError('La tarjeta no pertenece a la nota enlazada.')
        fields=json.loads(note['fields_json'])
    return {'note_id':str(note_id),'title':plain(fields[0])[:160] if fields else 'Nota '+str(note_id),'fields':fields,'card_ids':[str(i) for i in ids],'preview':collection.browser_preview(selected)}
