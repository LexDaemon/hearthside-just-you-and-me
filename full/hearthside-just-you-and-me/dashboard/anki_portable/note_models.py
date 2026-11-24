"""Native text-mode APKG templates; occlusion uses portable script-free templates."""
import json
from .errors import CollectionError, UnsupportedNoteType
from .note_generation import MODES, prepare, generate

IDS={'basic':1,'basic-reversed':2,'basic-optional-reversed':3,'basic-typing':4,'cloze':5}
NAMES={'basic':'Basic','basic-reversed':'Basic (and reversed card)','basic-optional-reversed':'Basic (optional reversed card)','basic-typing':'Basic (type in the answer)','cloze':'Cloze'}


def model(ident, name, now, kind, mask_data=None, retired=None):
    from .collection import _basic_model
    result=_basic_model(ident,name,now,reversed_cards=kind=='basic-reversed')
    result['flds']=[dict(result['flds'][0],name=field,ord=i) for i,field in enumerate(MODES[kind][1])]
    base=result['tmpls'][0]
    if kind=='basic-optional-reversed':
        other=dict(base,name='Card 2',ord=1,qfmt='{{#Add Reverse}}{{Back}}{{/Add Reverse}}',afmt='{{FrontSide}}\n\n<hr id=answer>\n\n{{Front}}')
        result['tmpls'].append(other);result['req'].append([1,'all',[1,2]])
    elif kind=='basic-typing':
        result['tmpls'][0]=dict(base,qfmt='{{Front}}\n\n{{type:Back}}',afmt='{{Front}}\n\n<hr id=answer>\n\n{{type:Back}}')
    elif kind=='cloze':
        result['type']=1;result['req']=[]
        result['tmpls']=[dict(base,qfmt='{{cloze:Text}}',afmt='{{cloze:Text}}<br>{{Back Extra}}')]
        result['css']+=' .cloze {font-weight:bold;color:#1672a6;}'
    elif kind=='image-occlusion':
        from .occlusion import masks,frame
        data=masks(json.dumps(mask_data));retired=retired or []
        if not isinstance(retired,list) or len(retired)>500:raise CollectionError('Las variantes retiradas no son válidas.')
        cached={};clean_retired=[]
        for item in retired:
            if not isinstance(item,dict) or type(item.get('ordinal')) is not int or not 0<=item['ordinal']<500 or item['ordinal'] in cached:raise CollectionError('Una variante retirada no es válida.')
            fields,_=prepare('image-occlusion',item.get('fields'));card=next((c for c in generate('image-occlusion',fields) if c['ordinal']==item['ordinal']),None)
            if card is None or any(s['group']==item['ordinal']+1 for s in data['shapes']):raise CollectionError('Una variante retirada no coincide con sus máscaras.')
            cached[item['ordinal']]=card;clean_retired.append({'ordinal':item['ordinal'],'fields':fields})
        result['hearthside_image_occlusion']={'version':1,'masks':data,'retired':clean_retired}
        result['tmpls']=[];result['req']=[]
        for ordinal,group in enumerate(sorted({s['group'] for s in data['shapes']}|{n+1 for n in cached})):
            old=cached.get(group-1)
            question=old['front'] if old else '{{Header}}'+frame('{{Image}}',data,group,False)
            answer=old['back'] if old else '{{Header}}'+frame('{{Image}}',data,group,True)+'{{Back Extra}}{{Comments}}'
            result['tmpls'].append(dict(base,name='Mask '+str(group),ord=ordinal,qfmt=question,afmt=answer))
            result['req'].append([ordinal,'all',[0]])
        result['css']+=' .memoria-occlusion>img{display:block;width:100%;height:auto;}'
    return result


def kind_for_model(value):
    from .schema_package import KEY,document
    if KEY in value:
        schema=document(value[KEY]);kind=schema['kind']
        if value.get('type')!=(1 if kind=='cloze' else 0):raise UnsupportedNoteType('El esquema importado no coincide con el tipo de plantilla.')
        if kind=='image-occlusion':
            from .occlusion import masks
            config=value.get('hearthside_image_occlusion')
            if not isinstance(config,dict) or config.get('version')!=1:raise UnsupportedNoteType('El formato de oclusión no es compatible.')
            masks(json.dumps(config.get('masks')))
        return kind
    from .note_types import validate_definition
    definition=value.get('hearthside_note_type')
    if definition is not None:
        definition=validate_definition(definition);kind=definition['kind']
        if value.get('name')!=definition['name']:raise UnsupportedNoteType('El nombre del tipo propio no coincide.')
    elif value.get('hearthside_builtin') is not None:
        definition=value['hearthside_builtin']
        if not isinstance(definition,dict) or definition.get('id') not in MODES or definition.get('kind')!=definition['id'] or definition.get('builtin') is not True or not isinstance(definition.get('name'),str) or not definition['name'].strip() or len(definition['name'])>100 or any(ord(c)<32 for c in definition['name']) or value.get('name')!=definition['name']:raise UnsupportedNoteType('La definición del tipo predefinido no es válida.')
        kind=definition['kind']
    elif value.get('hearthside_image_occlusion') is not None:kind='image-occlusion'
    else:
        name=str(value.get('name','')).casefold().strip()
        aliases={v.casefold():k for k,v in NAMES.items()}
        aliases.update({v[0].casefold():k for k,v in MODES.items() if k!='image-occlusion'})
        kind=aliases.get(name)
        if kind is None:raise UnsupportedNoteType('El tipo «'+str(value.get('name','sin nombre'))+'» no es compatible. No se importó ningún dato.')
    if definition is not None or kind=='image-occlusion':
        config=value.get('hearthside_image_occlusion',{})
        if kind=='image-occlusion' and (not isinstance(config,dict) or config.get('version')!=1):raise UnsupportedNoteType('El formato de oclusión no es compatible.')
        expected=model(1,value.get('name',''),0,kind,config.get('masks'),config.get('retired'))
        templates=value.get('tmpls')
        if value.get('type')!=expected['type'] or not isinstance(templates,list) or len(templates)!=len(expected['tmpls']) or any(not isinstance(t,dict) or any(t.get(k)!=e[k] for k in ('qfmt','afmt','ord')) for t,e in zip(templates,expected['tmpls'])):
            raise UnsupportedNoteType('Las plantillas del tipo propio no son compatibles.')
    elif value.get('type')!=(1 if kind=='cloze' else 0):raise UnsupportedNoteType('El modelo no coincide con su tipo de plantilla.')
    return kind


def io_ordinals(value):
    return [g-1 for g in sorted({s['group'] for s in value['hearthside_image_occlusion']['masks']['shapes']}|{r['ordinal']+1 for r in value['hearthside_image_occlusion'].get('retired',[])})]


def fields_for_note(row, value, kind):
    from .collection import FIELD_SEPARATOR
    fields=str(row['flds'] or '').split(FIELD_SEPARATOR)
    names=[str(f.get('name','')).casefold() for f in (value.get('flds') or [])]
    from .schema_package import KEY,document
    if KEY in value:
        schema=document(value[KEY])
        if names!=[f['name'].casefold() for f in schema['fields']] or len(fields)!=len(names):raise UnsupportedNoteType('Los campos no coinciden con el esquema importado.')
        from .authoring import clean_field
        return [field if kind=='image-occlusion' and schema['fields'][i].get('role')=='Masks' else clean_field(field,allow_empty=True)[0] for i,field in enumerate(fields)]
    if names!=[s.casefold() for s in MODES[kind][1]] or len(fields)!=len(names):raise UnsupportedNoteType('Los campos no coinciden con el tipo «'+str(value.get('name',''))+'».')
    if kind not in ('basic','basic-reversed'):
        fields,_=prepare(kind,fields);generate(kind,fields)
    if kind=='image-occlusion' and json.loads(fields[2])!=value['hearthside_image_occlusion']['masks']:raise CollectionError('Las máscaras y las plantillas del paquete no coinciden.')
    return fields


def export_models(notes,now,cards):
    used={n['model_kind'] for n in notes}
    if used-set(MODES):raise UnsupportedNoteType('La colección contiene un tipo que no se puede exportar.')
    models={str(IDS[k]):model(IDS[k],NAMES[k],now,k) for k in used if k!='image-occlusion'}
    if not models and not used:models['1']=model(1,'Basic',now,'basic')
    custom={n['authoring_type']['id']:n['authoring_type'] for n in notes if n.get('authoring_type') and not n['authoring_type']['builtin'] and n['model_kind']!='image-occlusion'}
    custom_ids={}
    for ident,(key,definition) in enumerate(sorted(custom.items()),100):
        m=model(ident,definition['name'],now,definition['kind']);m['hearthside_note_type']=definition;models[str(ident)]=m;custom_ids[key]=ident
    by_note={}
    for note in notes:
        definition=note.get('authoring_type',{});kind=note['model_kind']
        if kind=='image-occlusion':
            retired=[{'ordinal':c['ordinal'],'fields':c['retired_generation']['fields']} for c in cards if c['note_id']==note['id'] and c.get('retired_generation')]
            ident=1000+int(note['id']);m=model(ident,definition.get('name','Oclusión de imagen'),now,kind,json.loads(json.loads(note['fields_json'])[2]),retired)
            if definition and not definition['builtin']:m['hearthside_note_type']=definition
            models[str(ident)]=m;by_note[note['id']]=ident
        else:by_note[note['id']]=custom_ids.get(definition.get('id'),IDS[kind])
    for note in notes:
        m=models[str(by_note[note['id']])];options=note.get('latex_options')
        definition=note.get('authoring_type',{})
        if definition.get('builtin') and definition.get('name')!=MODES[note['model_kind']][0]:m.update(name=definition['name'],hearthside_builtin=definition)
        if options:m.update(latexPre=options['header'],latexPost=options['footer'],latexsvg=options['svg'],hearthside_latex=options)
    from .schema_package import apply_model
    for note in notes:
        if note.get('editable_schema'):
            ident=str(by_note[note['id']]);models[ident]=apply_model(models[ident],note['editable_schema'],note['authoring_type'],now,[c for c in cards if c['note_id']==note['id']])
    return models,by_note
