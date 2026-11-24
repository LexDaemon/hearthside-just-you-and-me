"""Independent table adapter; UI contracts observed in Anki Desktop 25.09.2."""
import copy
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from .browser import configure, plain, version
from .errors import CollectionError
from .note_types import definitions
from .note_generation import MODES
from .note_schema import load as schema_load

# IDs are the native column identifiers, not SQL fragments supplied by clients.
COLUMNS=[
 ('noteFld','Campo para ordenar','Campo para ordenar','asc','asc'),
 ('noteCrt','Creada','Creada','desc','desc'),
 ('difficulty','Dificultad','Dificultad','desc',None),
 ('noteMod','Editado','Editado','desc','desc'),
 ('stability','Estabilidad','Estabilidad','desc',None),
 ('noteTags','Etiquetas','Etiquetas','asc','asc'),
 ('cardEase','Facilidad','Facilidad promedio','desc','desc'),
 ('cardIvl','Intervalo','Intervalo promedio','desc','desc'),
 ('deck','Mazo','Mazo','asc','asc'),
 ('cardMod','Modificada','Modificada','desc','desc'),
 ('cardLapses','Olvidos','Olvidos','desc','desc'),
 ('originalPosition','Posición','Posición','desc','desc'),
 ('question','Pregunta','Pregunta',None,None),
 ('cardDue','Programadas','Programadas','desc','desc'),
 ('retrievability','Recuperabilidad','Recuperabilidad','desc',None),
 ('cardReps','Repasos','Repasos','desc','desc'),
 ('answer','Respuesta','Respuesta',None,None),
 ('note','Tipo de nota','Tipo de nota','asc','asc'),
 ('template','Tipo de tarjeta','Tarjetas','desc','desc')]
KEY='browser_ui_v3'
DEFAULT={'mode':'cards','layout':'vertical','sidebar':True,'sidebar_width':250,'split':0.5,'zoom':1.0,
         'cards_columns':['noteFld','template','cardDue','deck'],
         'notes_columns':['noteFld','template','noteTags'],
         'cards_sort':'noteFld','notes_sort':'noteFld','cards_descending':False,'notes_descending':False,
         'widths':{},'collapsed':['type:'+ident for ident in MODES]}


def configuration(db,state=None):
    if state is not None:
        if not isinstance(state,dict) or set(state)!=set(DEFAULT):raise CollectionError('La configuración de Explorar no es válida.')
        if state['mode'] not in ('cards','notes') or state['layout'] not in ('auto','vertical','horizontal') or type(state['sidebar']) is not bool:raise CollectionError('El diseño de Explorar no es válido.')
        if type(state['sidebar_width']) not in (int,float) or not 100<=state['sidebar_width']<=600 or type(state['split']) not in (int,float) or not .1<=state['split']<=.9 or type(state['zoom']) not in (int,float) or not .5<=state['zoom']<=3:raise CollectionError('El tamaño de Explorar no es válido.')
        for mode in ('cards','notes'):
            cols=state[mode+'_columns'];sort=state[mode+'_sort']
            if not isinstance(cols,list) or not 1<=len(cols)<=len(COLUMNS) or len(cols)!=len(set(cols)) or any(c not in {x[0] for x in COLUMNS} for c in cols):raise CollectionError('La columna de Explorar no es válida.')
            if sort not in {c[0] for c in COLUMNS if c[3 if mode=='cards' else 4]} or type(state[mode+'_descending']) is not bool:raise CollectionError('El orden de Explorar no es válido.')
        if not isinstance(state['widths'],dict) or any(k not in {c[0] for c in COLUMNS} or type(v) not in (int,float) or not 40<=v<=2000 for k,v in state['widths'].items()):raise CollectionError('El ancho de columna no es válido.')
        if not isinstance(state['collapsed'],list) or len(state['collapsed'])>1000 or any(not isinstance(v,str) or len(v)>500 for v in state['collapsed']):raise CollectionError('La barra lateral no es válida.')
        db.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(KEY,json.dumps(state,ensure_ascii=False,allow_nan=False)))
    row=db.execute('SELECT value FROM metadata WHERE key=?',(KEY,)).fetchone()
    if row:
        value=json.loads(row[0])
    else:
        legacy=db.execute("SELECT value FROM metadata WHERE key='browser_ui_v2'").fetchone()
        value=json.loads(legacy[0]) if legacy else copy.deepcopy(DEFAULT)
        value['split']=.5
        value['layout']='vertical'
    return {'version':3,'state':value,'columns':[{'key':c[0],'cards_label':c[1],'notes_label':c[2],'cards_order':c[3],'notes_order':c[4]} for c in COLUMNS]}


def duration(days):
    if days is None:return ''
    if days<1:return f'{days*24:.1f}'.rstrip('0').rstrip('.')+' horas'
    value,unit=(days/365,'años') if days>=365 else (days/30,'meses') if days>=30 else (days,'días')
    return (f'{value:.1f}'.rstrip('0').rstrip('.')+' '+unit).replace('.',',')


def date_string(at,clock=False):
    return datetime.fromtimestamp(at).strftime('%Y-%m-%d, %H:%M') if at and clock else datetime.fromtimestamp(at).strftime('%Y-%m-%d') if at else ''


def table(collection,db,query='',mode='cards',sort='noteFld',descending=False,limit=100,offset=0,current_deck_id=None,now=None):
    if mode not in ('cards','notes') or sort not in {c[0] for c in COLUMNS if c[3 if mode=='cards' else 4]} or type(descending) is not bool:raise CollectionError('El orden o modo de Explorar no es válido.')
    if type(limit) is not int or not 1<=limit<=200 or type(offset) is not int or offset<0:raise CollectionError('La ventana de resultados no es válida.')
    if not isinstance(query,str) or len(query)>2097152:raise CollectionError('La búsqueda supera el límite permitido.')
    if current_deck_id is not None and (type(current_deck_id) is not int or current_deck_id<1):raise CollectionError('El mazo actual no es válido.')
    at=collection._now(now);configure(db)
    from .browser_search import Search
    where,args=Search(collection,db,at,current_deck_id).compile(query)
    base=" FROM cards c JOIN notes n ON n.id=c.note_id JOIN decks d ON d.id=c.deck_id LEFT JOIN metadata nt ON nt.key='authoring_note_type:'||n.guid "
    matching=list(db.execute('SELECT c.id,n.id AS nid'+base+'WHERE '+where,args))
    matched={r['id'] if mode=='cards' else r['nid'] for r in matching}
    if not matched:return {'ids':[],'rows':[],'total':0,'mode':mode,'offset':offset,'limit':limit}
    raw=list(db.execute('SELECT c.*,n.guid AS note_guid,n.fields_json,n.tags_json,n.model_kind,n.created_at AS note_created_at,n.modified_at,COALESCE(nt.value,n.model_kind) AS note_type_id,d.name AS deck'+base+'WHERE '+('c.id' if mode=='cards' else 'n.id')+' IN (SELECT '+('c.id' if mode=='cards' else 'n.id')+base+'WHERE '+where+') ORDER BY c.id',args))
    grouped=defaultdict(list)
    for row in raw:grouped[row['id'] if mode=='cards' else row['note_id']].append(row)
    schemas={t['id']:schema_load(db,t['id']) for t in definitions(db)}
    logs={r['card_id']:r['last'] for r in db.execute('SELECT card_id,MAX(reviewed_at) AS last FROM reviews GROUP BY card_id')}
    changed={int(r['key'].split(':')[-1]):int(r['value']) for r in db.execute("SELECT key,value FROM metadata WHERE key LIKE 'browser_modified_at:%'")}
    engines={};result=[]
    for ident,cards in grouped.items():
        first=cards[0];schema=schemas[first['note_type_id']];fields=json.loads(first['fields_json']);tags=json.loads(first['tags_json'])
        sf=next(i for i,f in enumerate(schema['fields']) if f['id']==schema['sort_field'])
        tpl=schema['templates'][0 if first['model_kind'] in ('cloze','image-occlusion') else min(first['ordinal'],len(schema['templates'])-1)]
        all_siblings=list(db.execute('SELECT id,ordinal FROM cards WHERE note_id=? ORDER BY id',(first['note_id'],)))
        reviewed=[c for c in cards if c['card_type']!=0];intervals=[c['interval_days'] for c in cards if c['card_type'] in (2,3)]
        due=first['due_at'];due_cell=('Nueva #'+str(first['new_position'] or first['id'])) if first['card_type']==0 else date_string(due)
        if first['suspended'] or first['buried_until']>at or first['queue']<0:due_cell='('+due_cell+')'
        if mode=='notes':
            valid=[c['due_at'] for c in cards if c['card_type']!=0 and not c['suspended'] and c['buried_until']<=at and c['queue']>=0]
            due=min(valid) if valid else 0;due_cell=date_string(due)
        difficulty=stability=retrievability=None
        if first['fsrs_json']:
            from . import scheduler as fsrs
            options=collection._options(db,first['deck_id'])
            if first['deck_id'] not in engines:engines[first['deck_id']]=fsrs.scheduler(options,collection._parameters(db,first['deck_id']))
            card=fsrs.restore(first,[],engines[first['deck_id']],options)
            stability=card.stability;difficulty=(card.difficulty-1)/9 if card.difficulty is not None else None
            if card.last_review:retrievability=engines[first['deck_id']].get_card_retrievability(card,datetime.fromtimestamp(at,timezone.utc))
        deck_names={c['deck'] for c in cards}
        ease=sum(c['ease_factor'] for c in reviewed)/len(reviewed) if reviewed else None
        ivl=sum(intervals)/len(intervals) if intervals else None
        modified=max(changed.get(c['id'],logs.get(c['id'],c['added_at'])) for c in cards)
        numbers={'noteCrt':first['note_created_at'],'noteMod':first['modified_at'],'cardMod':modified,'cardDue':due,
                 'cardReps':sum(c['reps'] for c in cards),'cardLapses':sum(c['lapses'] for c in cards),'cardEase':ease or 0,'cardIvl':ivl or 0,
                 'originalPosition':first['new_position'] or 0,'stability':stability or 0,'difficulty':difficulty or 0,'retrievability':retrievability or 0,
                 'template':len(cards) if mode=='notes' else first['ordinal']}
        cells={'noteFld':plain(fields[sf]),'noteCrt':date_string(first['note_created_at'],True),'noteMod':date_string(first['modified_at'],True),
               'note':schema['name'],'noteTags':' '.join(tags),'cardMod':date_string(modified,True),'cardDue':due_cell,
               'cardReps':str(numbers['cardReps']),'cardLapses':str(numbers['cardLapses']),
               'deck':'('+str(len(deck_names))+')' if len(deck_names)>1 else first['deck'],
               'cardEase':str(int(ease/10))+'%' if ease is not None else 'Nueva',
               'cardIvl':('Nueva' if first['card_type']==0 else 'Aprendiendo') if mode=='cards' and first['card_type'] in (0,1) else duration(ivl),
               'originalPosition':str(first['new_position']) if first['new_position'] else '',
               'template':str(len(cards)) if mode=='notes' else tpl['name']+(' '+str(first['ordinal']+1) if first['model_kind']=='cloze' else ''),
               'stability':duration(stability),'difficulty':f'{difficulty*100:.0f}%' if difficulty is not None else '',
               'retrievability':f'{retrievability*100:.0f}%' if retrievability is not None else ''}
        # Browser-specific template overrides do not alter the studying templates.
        from .note_templates import render
        if tpl.get('bqfmt') or tpl.get('bafmt'):
            vals={f['name']:fields[i] for i,f in enumerate(schema['fields'])};vals.update(Tags=' '.join(tags),Type=schema['name'],Deck=first['deck'],Card=tpl['name'])
            question=render(tpl.get('bqfmt') or tpl['qfmt'],vals,vals,active=first['ordinal']+1)
            answer=render(tpl.get('bafmt') or tpl['afmt'],vals,dict(vals,FrontSide=question),answer=True,active=first['ordinal']+1)
        else:question=first['front'];answer=first['back']
        # Remove CSS content before stripping tags.
        import re
        cells['question']=plain(re.sub(r'<style\b[^>]*>[\s\S]*?</style>','',question,flags=re.I))
        cells['answer']=plain(re.sub(r'<style\b[^>]*>[\s\S]*?</style>','',answer,flags=re.I))
        item=dict(first);item.pop('fields_json');item.pop('tags_json');item.update(id=ident,card_id=first['id'],fields=fields,tags=tags,cells=cells,version=version(first),model_label=schema['name'],sort_field=cells['noteFld'],card_ids=[c['id'] for c in all_siblings],ordinals=[c['ordinal'] for c in all_siblings])
        item['_sort']=numbers.get(sort,cells[sort].casefold());result.append(item)
    result.sort(key=lambda r:r['id']);result.sort(key=lambda r:r['_sort'],reverse=descending)
    ids=[r['id'] for r in result];window=result[offset:offset+limit]
    for row in window:row.pop('_sort',None)
    return {'ids':ids,'rows':window,'total':len(ids),'mode':mode,'offset':offset,'limit':limit}
