"""Validated vector masks and script-free portable image-occlusion cards."""
import html
import json
import math
import re
from .errors import CollectionError


def masks(value):
    if not isinstance(value,str) or len(value)>100000:raise CollectionError('Las máscaras superan el tamaño permitido.')
    try:data=json.loads(value)
    except (ValueError,TypeError) as exc:raise CollectionError('Las máscaras no son válidas.') from exc
    if not isinstance(data,dict) or data.get('version')!=1 or data.get('mode') not in ('all','one') or not isinstance(data.get('shapes'),list) or not 1<=len(data['shapes'])<=200:
        raise CollectionError('Dibujá entre 1 y 200 máscaras y elegí un modo de oclusión.')
    clean=[];ids=set()
    def coordinate(v):
        if type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1:raise CollectionError('Una máscara queda fuera de la imagen.')
        return round(v,6)
    for item in data['shapes']:
        if not isinstance(item,dict) or not isinstance(item.get('id'),str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',item['id']) or item['id'] in ids or type(item.get('group')) is not int or not 1<=item['group']<=500:
            raise CollectionError('Una máscara tiene un identificador o grupo no válido.')
        shape={'id':item['id'],'group':item['group'],'shape':item.get('shape')};ids.add(item['id'])
        if shape['shape'] in ('rect','ellipse'):
            shape.update({k:coordinate(item.get(k)) for k in ('x','y','w','h')})
            if min(shape['w'],shape['h'])<.002 or shape['x']+shape['w']>1.000001 or shape['y']+shape['h']>1.000001:raise CollectionError('Una máscara es demasiado pequeña o queda fuera de la imagen.')
        elif shape['shape']=='polygon':
            points=item.get('points')
            if not isinstance(points,list) or not 3<=len(points)<=50 or any(not isinstance(p,list) or len(p)!=2 for p in points):raise CollectionError('El polígono necesita entre 3 y 50 puntos.')
            points=[[coordinate(v) for v in p] for p in points]
            area=abs(sum(p[0]*q[1]-q[0]*p[1] for p,q in zip(points,points[1:]+points[:1])))/2
            if area<.000004:raise CollectionError('El polígono no tiene área suficiente.')
            shape['points']=points
        else:raise CollectionError('Usá rectángulo, elipse o polígono.')
        clean.append(shape)
    return {'version':1,'mode':data['mode'],'shapes':clean}


def frame(image, data, group, answer):
    shapes=[]
    for shape in data['shapes']:
        active=shape['group']==group
        if not active and data['mode']=='one':continue
        attributes=' data-mask-active="'+str(int(active))+'" fill="'+('none' if active and answer else '#b94a53' if active else '#505761')+'" stroke="'+('#b94a53' if active else '#505761')+'" stroke-width="2"'
        if shape['shape']=='rect':
            attributes+=' '+ ' '.join(k+'="'+str(round(shape[v]*1000,3))+'"' for k,v in [('x','x'),('y','y'),('width','w'),('height','h')]);tag='rect'
        elif shape['shape']=='ellipse':
            attributes+=' '+ ' '.join(k+'="'+str(round(v*1000,3))+'"' for k,v in [('cx',shape['x']+shape['w']/2),('cy',shape['y']+shape['h']/2),('rx',shape['w']/2),('ry',shape['h']/2)]);tag='ellipse'
        else:
            attributes+=' points="'+' '.join(str(round(x*1000,3))+','+str(round(y*1000,3)) for x,y in shape['points'])+'"';tag='polygon'
        shapes.append('<'+tag+attributes+'/>')
    return '<div class="memoria-occlusion" style="position:relative;max-width:100%;display:inline-block;width:100%">'+image+'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-label="Máscaras de oclusión" style="position:absolute;inset:0;width:100%;height:100%;pointer-events:none">'+''.join(shapes)+'</svg></div>'


def generate(fields):
    data=masks(fields[2]);source=re.search(r'\bsrc="([^"]+)"',fields[0])
    if source is None:raise CollectionError('Adjuntá una imagen para la oclusión.')
    image='<img src="'+html.escape(html.unescape(source[1]),quote=True)+'" alt="Imagen para oclusión" style="display:block;width:100%;height:auto">'
    extra=''.join('<div>'+f+'</div>' for f in (fields[1],fields[4]) if f)
    header='<div>'+fields[3]+'</div>' if fields[3] else ''
    return [{'ordinal':group-1,'front':header+frame(image,data,group,False),'back':header+frame(image,data,group,True)+extra} for group in sorted({s['group'] for s in data['shapes']})]
