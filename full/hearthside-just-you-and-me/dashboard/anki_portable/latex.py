"""Local TeX images. Compiler processes have no network or private-file access."""
import base64,hashlib,html,json,os,re,signal,struct,subprocess,sys,tempfile
from pathlib import Path
from .errors import CollectionError

SETTINGS='authoring_latex_v1'
HEADER=r'''\documentclass[12pt]{article}
\special{papersize=3in,5in}
\usepackage[utf8]{inputenc}
\usepackage{amssymb,amsmath}
\pagestyle{empty}
\setlength{\parindent}{0in}
\begin{document}'''
PATTERN=re.compile(r'\[(latex|\$\$|\$)\](.*?)\[/\1\]',re.S)


def defaults():return {'header':HEADER,'footer':r'\end{document}','svg':False}


def validate(value):
    if not isinstance(value,dict) or set(value)!={'header','footer','svg'} or type(value['svg']) is not bool or any(not isinstance(value[k],str) or not value[k].strip() or len(value[k])>16000 or '\x00' in value[k] for k in ('header','footer')):
        raise CollectionError('Las opciones LaTeX no son válidas.')
    return dict(value)


def settings(db,ident):
    row=db.execute('SELECT value FROM metadata WHERE key=?',(SETTINGS,)).fetchone()
    return validate(json.loads(row[0]).get(ident,defaults())) if row else defaults()


def runtime():
    home=Path(os.environ.get('HERMES_HOME',str(Path.home()/'.hermes')))
    root=home/'runtimes/hearthside-tex/TinyTeX'
    directories=list((root/'bin').glob('*')) if (root/'bin').is_dir() else []
    binary=next((p for p in directories if all((p/name).is_file() for name in ('latex','dvipng','dvisvgm'))),None)
    if sys.platform!='darwin' or binary is None or not Path('/usr/bin/sandbox-exec').is_file():
        raise CollectionError('Falta el entorno local de LaTeX con aislamiento. La generación local requiere macOS y un runtime TinyTeX; no se compila sin sandbox.')
    return root.resolve(),binary.resolve()


def status():
    try:runtime();return {'available':True,'engine':'latex','png':'dvipng','svg':'dvisvgm','sandbox':True}
    except CollectionError as exc:return {'available':False,'reason':str(exc)}


def run(binary,args,job,root):
    profile='(version 1)(deny default)(allow process-fork)(allow process-exec (subpath '+json.dumps(str(root))+'))(allow file-read-metadata)(allow file-read-data '+''.join('(subpath '+json.dumps(p)+')' for p in (str(root),str(job),'/System/Library','/usr/lib','/usr/share','/Library/Fonts'))+'(literal "/")(literal "/dev/null")(literal "/dev/random")(literal "/dev/urandom"))(allow file-write* (subpath '+json.dumps(str(job))+')(literal "/dev/null"))(allow sysctl-read)'
    # Set limits before exec, not in preexec_fn (unsafe in a threaded server).
    wrapper='import os,resource,sys;resource.setrlimit(resource.RLIMIT_CPU,(12,12));resource.setrlimit(resource.RLIMIT_FSIZE,(16777216,16777216));resource.setrlimit(resource.RLIMIT_NOFILE,(96,96));os.execv(sys.argv[1],sys.argv[1:])'
    env={'PATH':str(binary.parent)+':/usr/bin:/bin','HOME':str(job),'TMPDIR':str(job),'TEXMFOUTPUT':str(job),'TEXMFVAR':str(root/'texmf-var'),'TEXMFCONFIG':str(job),'openin_any':'p','openout_any':'p','shell_escape':'f','LC_ALL':'C','max_print_line':'200'}
    argv=[sys.executable,'-I','-c',wrapper,'/usr/bin/sandbox-exec','-p',profile,str(binary),*args]
    with (job/'process.log').open('wb') as log:
        process=subprocess.Popen(argv,cwd=job,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:process.wait(timeout=18)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL);process.wait();raise CollectionError('La compilación LaTeX superó el tiempo permitido.')
    with (job/'process.log').open('rb') as log:
        log.seek(max(0,(job/'process.log').stat().st_size-1800));output=log.read(1800)
    if process.returncode:
        text=output.decode('utf8','replace')[-1800:].replace(str(job),'[temporal]').replace(str(root),'[TeX]')
        raise CollectionError('No se pudo compilar LaTeX (salida '+str(process.returncode)+'):\n'+text)


def clean_svg(raw):
    import xml.etree.ElementTree as ET
    if len(raw)>8*1024*1024 or b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:raise CollectionError('El SVG generado no es válido.')
    try:tree=ET.fromstring(raw)
    except ET.ParseError as exc:raise CollectionError('No se pudo leer el SVG generado.') from exc
    allowed={'svg','g','defs','path','use','rect','circle','ellipse','line','polyline','polygon','clipPath'}
    if tree.tag.split('}')[-1]!='svg':raise CollectionError('La salida no es una imagen SVG.')
    for node in tree.iter():
        if node.tag.split('}')[-1] not in allowed:raise CollectionError('El SVG contiene elementos no permitidos.')
        for key,value in node.attrib.items():
            key=key.split('}')[-1].lower()
            if key.startswith('on') or key in ('style','src') or (key=='href' and not value.startswith('#')) or ('url(' in value and not re.fullmatch(r'url\(#[A-Za-z0-9_.:-]+\)',value)):
                raise CollectionError('El SVG contiene referencias no permitidas.')
    try:
        dimensions=tree.attrib.get('viewBox','').split();height=float(dimensions[3]) if len(dimensions)==4 else float(re.sub('[A-Za-z]+$','',tree.attrib['height']))
        width=float(dimensions[2]) if len(dimensions)==4 else float(re.sub('[A-Za-z]+$','',tree.attrib['width']))
        if not 0<width<=20000 or not 0<height<=20000:raise ValueError()
        tree.set('width',str(round(width*1.8,3))+'px');tree.set('height',str(round(height*1.8,3))+'px')
    except (KeyError,ValueError,IndexError):raise CollectionError('El tamaño del SVG no es válido.')
    ET.register_namespace('','http://www.w3.org/2000/svg')
    return ET.tostring(tree,encoding='utf-8',xml_declaration=True)


def compile_tex(source,options):
    options=validate(options)
    if not isinstance(source,str) or not source.strip() or len(source)>12000 or '\x00' in source:raise CollectionError('El fragmento LaTeX debe tener entre 1 y 12000 caracteres.')
    root,binary=runtime()
    base=Path(os.environ.get('TMPDIR',str(Path.home()/'.hermes/cache/scratch')));base.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='memoria-tex-',dir=base) as directory:
        job=Path(directory).resolve();(job/'equation.tex').write_text(options['header']+'\n'+source+'\n'+options['footer'],encoding='utf8')
        run(binary/'latex',['-no-shell-escape','-interaction=nonstopmode','-halt-on-error','equation.tex'],job,root)
        dvi=(job/'equation.dvi').read_bytes();index=len(dvi)-1
        while index>=0 and dvi[index]==223:index-=1
        if index<5 or dvi[index-5]!=249:raise CollectionError('La salida DVI no es válida.')
        post=int.from_bytes(dvi[index-4:index],'big')
        if int.from_bytes(dvi[post+27:post+29],'big')!=1:raise CollectionError('El fragmento LaTeX debe ocupar una sola página.')
        if options['svg']:
            run(binary/'dvisvgm',['--no-fonts','--no-specials','--bbox=min','--exact','--output=equation.svg','equation.dvi'],job,root)
            return clean_svg((job/'equation.svg').read_bytes())
        run(binary/'dvipng',['-T','tight','-D','160','-bg','White','-o','equation.png','equation.dvi'],job,root)
        raw=(job/'equation.png').read_bytes()
        if len(raw)>8*1024*1024 or not raw.startswith(b'\x89PNG\r\n\x1a\n'):raise CollectionError('La imagen LaTeX supera el límite permitido.')
        width,height=struct.unpack('>II',raw[16:24])
        if width*height>4000000:raise CollectionError('La imagen LaTeX supera cuatro millones de píxeles.')
        return raw


class Renderer:
    def __init__(self,db,ident,persist=False,options=None,allow_compile=True):
        from .preferences import load
        self.generation_enabled=load(db)['generate_latex']
        self.db=db;self.options=validate(options) if options is not None else settings(db,ident);self.persist=persist;self.allow_compile=allow_compile;self.cache={}

    def markup(self,value):
        from html.parser import HTMLParser
        def replace(match):
            class Plain(HTMLParser):
                def __init__(self):super().__init__(convert_charrefs=True);self.parts=[]
                def handle_data(self,data):self.parts.append(data)
                def handle_starttag(self,tag,attrs):
                    if tag in ('br','div','p'):self.parts.append('\n')
            parser=Plain();parser.feed(match[2]);parser.close();body=''.join(parser.parts)
            if match[1]=='$':body=r'\('+body+r'\)'
            elif match[1]=='$$':body=r'\['+body+r'\]'
            extension='svg' if self.options['svg'] else 'png'
            digest=hashlib.sha256(json.dumps([1,self.options,body],sort_keys=True,ensure_ascii=False).encode()).hexdigest();name='latex-'+digest+'.'+extension
            raw=self.cache.get(name)
            if raw is None:
                row=self.db.execute('SELECT data FROM media WHERE name=?',(name,)).fetchone()
                if row:raw=bytes(row[0])
                else:
                    if not self.generation_enabled:return html.escape(match.group(0))
                    if not self.allow_compile:raise CollectionError('Falta una imagen LaTeX guardada. Abrí la nota y guardala para generarla localmente.')
                    if len(self.cache)>=32:raise CollectionError('La operación supera 32 fórmulas LaTeX distintas.')
                    raw=compile_tex(body,self.options)
                    if self.persist:self.db.execute('INSERT INTO media(name,data) VALUES(?,?)',(name,raw))
                self.cache[name]=raw
            if extension=='svg':raw=clean_svg(raw)
            src=name if self.persist else 'data:image/'+('svg+xml' if extension=='svg' else 'png')+';base64,'+base64.b64encode(raw).decode()
            return '<img class="memoria-latex" src="'+src+'" alt="'+html.escape(body,quote=True)+'" style="background-color:white">'
        return PATTERN.sub(replace,value)

    def cards(self,cards):
        if len({m.group(0) for card in cards for field in ('front','back') for m in PATTERN.finditer(card[field])})>32:raise CollectionError('La nota supera 32 fórmulas LaTeX distintas.')
        return [dict(card,front=self.markup(card['front']),back=self.markup(card['back'])) for card in cards]
