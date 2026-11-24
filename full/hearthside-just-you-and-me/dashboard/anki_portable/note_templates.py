"""Bounded, script-free field/template rendering for editable note types."""
import html
import json
import re
from .errors import CollectionError
from .note_generation import answer_text,parse_clozes,cloze_html,MODES

TOKEN=re.compile(r'\{\{([^{}]+)\}\}')

def rename_references(markup,mapping):
    def rewrite(match):
        body=match[1].strip();prefix=body[:1] if body[:1] in '#^/' else ''
        text=body[1:] if prefix else body
        parts=text.split(':');parts[-1]=mapping.get(parts[-1],parts[-1])
        return '{{'+prefix+':'.join(parts)+'}}'
    return TOKEN.sub(rewrite,markup)

def validate_markup(markup):
    if not isinstance(markup,str) or len(markup)>100000:
        raise CollectionError('La plantilla supera el límite permitido.')
    from html.parser import HTMLParser
    class Check(HTMLParser):
        def handle_starttag(self,tag,attrs):
            if tag.casefold() in {'script','iframe','object','embed','audio','video','link','meta','base','form','input','button','style'}:
                raise CollectionError('Las plantillas no admiten scripts, audio, contenido externo ni controles activos.')
            for name,value in attrs:
                if name.casefold().startswith('on') or name.casefold() in {'srcdoc','formaction'}:
                    raise CollectionError('La plantilla contiene un atributo activo no permitido.')
                if name.casefold() in {'src','href'} and value and re.match(r'\s*(https?:|javascript:|data:|//)',value,re.I):
                    raise CollectionError('La plantilla no puede cargar recursos externos.')
    Check().feed(markup)
    depth=[]
    for token in TOKEN.findall(markup):
        token=token.strip()
        if token.startswith(('#','^')):depth.append(token[1:])
        elif token.startswith('/'):
            if not depth or depth.pop()!=token[1:]:raise CollectionError('Una sección de la plantilla no está bien cerrada.')
        if len(depth)>32:raise CollectionError('La plantilla tiene demasiados niveles.')
    if depth:raise CollectionError('Cerrá las secciones de la plantilla.')
    return markup

def validate_css(css):
    if not isinstance(css,str) or len(css)>100000:raise CollectionError('El estilo supera el límite permitido.')
    plain=re.sub(r'/\*.*?\*/','',css,flags=re.S)
    plain=re.sub(r'\\([0-9a-fA-F]{1,6}\s?|.)',lambda m:chr(int(m[1].strip(),16)) if re.fullmatch(r'[0-9a-fA-F]{1,6}\s?',m[1]) else m[1],plain).casefold()
    if '<' in plain or any(s in plain for s in ('@import','url(', 'expression(', 'behavior:', '-moz-binding')):
        raise CollectionError('El estilo no puede incluir HTML, recursos externos ni código activo.')
    return css

def prepare_fields(db,schema,values,editing=False):
    from .authoring import clean_field
    if not isinstance(values,list) or len(values)!=len(schema['fields']):raise CollectionError('Los campos no coinciden con el tipo de nota.')
    clean=[];images=[]
    for field,value in zip(schema['fields'],values):
        if schema['kind']=='image-occlusion' and field.get('role')=='Masks':
            from .occlusion import masks
            item=json.dumps(masks(value),ensure_ascii=False,separators=(',',':'));media=[]
        else:item,media=clean_field(value,allow_empty=True)
        clean.append(item);images.extend(media)
    if not editing and not answer_text(clean[0]) and '<img ' not in clean[0]:raise CollectionError('El primer campo no puede estar vacío.')
    for image in set(images):
        if not db.execute('SELECT 1 FROM media WHERE name=?',(image,)).fetchone():raise CollectionError('Una imagen ya no está en la colección.')
    return clean,images

def render(markup,values,specials,active=None,answer=False,typed=None):
    matches=list(TOKEN.finditer(markup))
    def section(start,stop=None):
        out=[];position=start
        while position<len(markup):
            match=TOKEN.search(markup,position)
            if not match:out.append(markup[position:]);return ''.join(out),len(markup)
            out.append(markup[position:match.start()]);body=match[1].strip();position=match.end()
            if body.startswith('/'):
                if body[1:]!=stop:raise CollectionError('Una sección de la plantilla no está bien cerrada.')
                return ''.join(out),position
            if body.startswith(('#','^')):
                name=body[1:];child,end=section(position,name);value=values.get(name,specials.get(name,''));visible=bool(answer_text(value) or '<img ' in value)
                if visible!=(body[0]=='^'):out.append(child)
                position=end;continue
            parts=body.split(':');name=parts.pop();value=values.get(name,specials.get(name))
            if value is None:raise CollectionError('La plantilla usa un campo que no existe: '+name)
            if 'tts' in parts:raise CollectionError('No se implementa audio en Memoria.')
            for operation in reversed(parts):
                if operation=='cloze':
                    if active is None:raise CollectionError('El filtro cloze requiere un tipo de respuesta anidada.')
                    nodes,_=parse_clozes(value);value=cloze_html(nodes,active,answer)
                elif operation=='text':value=html.escape(answer_text(value))
                elif operation=='type':
                    text=answer_text(value)
                    if typed is not None:typed.append(text)
                    value=html.escape(text) if answer else ''
                elif operation=='hint':value='<details><summary>'+html.escape(name)+'</summary>'+value+'</details>'
                elif operation not in ('edit',):raise CollectionError('El filtro no está disponible: '+operation)
            out.append(value)
        if stop:raise CollectionError('Cerrá las secciones de la plantilla.')
        return ''.join(out),position
    return section(0)[0]

def cards(db,schema,fields,tags=None,deck_name='',editing=False,preserve_ordinals=None):
    preserve_ordinals=set(preserve_ordinals or [])
    values={f['name']:value for f,value in zip(schema['fields'],fields)}
    specials={'Tags':' '.join(tags or []),'Type':schema.get('name',schema['id']),'Deck':deck_name,'Subdeck':deck_name.split('::')[-1],'CardFlag':''}
    templates=schema['templates'];kind=schema['kind'];variants=[]
    if kind=='cloze':
        numbers=set()
        for value in fields:
            if '{{c' in value:
                try:numbers.update(parse_clozes(value)[1])
                except CollectionError:
                    if not editing:raise
        variants=[(number-1,templates[0],number,None) for number in sorted(numbers|{ordinal+1 for ordinal in preserve_ordinals})]
    elif kind=='image-occlusion':
        from .note_generation import prepare,generate
        semantic={f.get('role'):value for f,value in zip(schema['fields'],fields) if f.get('role')}
        fixed=[semantic.get(name,'') for name in MODES[kind][1]]
        fixed,_=prepare(kind,fixed)
        occlusions={card['ordinal']:card for card in generate(kind,fixed)}
        missing='<div>No se encontró una máscara para esta tarjeta.</div>'
        variants=[(ordinal,templates[0],None,occlusions.get(ordinal,{'front':missing,'back':missing})) for ordinal in sorted(set(occlusions)|preserve_ordinals)]
    else:variants=[(i,template,None,None) for i,template in enumerate(templates)]
    result=[]
    for ordinal,template,active,occlusion in variants:
        typed=[];special=dict(specials,Card=template['name']);question_values=dict(values)
        if occlusion:question_values['Occlusion']=occlusion['front']
        front=render(template['qfmt'],question_values,dict(special,FrontSide=''),active=active,typed=typed)
        if kind=='cloze' and active not in numbers:front='<div>No se encontró una respuesta anidada '+str(active)+' en esta tarjeta.</div>'
        if not answer_text(front) and not re.search(r'<(?:img|svg)\b',front) and not typed and ordinal not in preserve_ordinals:continue
        answer_values=dict(values)
        if occlusion:answer_values['Occlusion']=occlusion['back']
        full_back=render(template['afmt'],answer_values,dict(special,FrontSide=front),active=active,answer=True)
        back=full_back
        if back.startswith(front):back=re.sub(r'^\s*(?:<hr\b[^>]*>\s*)?','',back[len(front):],count=1)
        if not occlusion:
            from .authoring import clean_field
            front,_=clean_field(front,allow_empty=True);back,_=clean_field(back,allow_empty=True)
        style='<style data-memoria-template-css="1">'+schema['css']+'</style>' if schema['css'] else ''
        marker='<span data-memoria-type-answer="'+html.escape(typed[0],quote=True)+'"></span>' if typed else ''
        item={'ordinal':ordinal,'front':style+'<div class="card" data-memoria-template="1">'+front+'</div>'+marker,'back':style+back,'full_back':style+'<div class="card" data-memoria-template="1">'+full_back+'</div>'}
        if typed:item['answer_text']=typed[0]
        result.append(item)
    return result
