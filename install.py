#!/usr/bin/env python3
"""Install a reviewed local Hearthside bundle without cloning credentials or replacing user data."""
from __future__ import annotations
import argparse,json,os,re,shutil,subprocess,sys,uuid
from pathlib import Path

ID='hearthside-just-you-and-me'; CODE_MARKER='.hearthside-install.json'; PROFILE_MARKER='.hearthside-profile.json'
PROJECT_MARKER='.hearthside-project.json'; BUNDLE=Path(__file__).resolve().parent
STORY_BINDING_FILE='hearthside-story-binding.json'
GUIDANCE='''# Hearthside

Read Nori.md, Room.md and Outfits.md as the current story, room and wardrobe. Journal.md is this project's journal: preserve entries and record only established events, never invented memories. Proposed entries require the user's approval before appending. Identity belongs to the Bot profile's SOUL.md, not a second project copy.
'''

def safe(path:Path)->Path:
    path=path.expanduser().absolute()
    for part in (path,*path.parents):
        if part.is_symlink()or(part.exists()and getattr(part.lstat(),'st_file_attributes',0)&0x400):raise RuntimeError('Refusing a symlink/reparse point: '+str(part))
    return path.resolve(strict=False)

def read_json(path:Path,default=None):
    safe(path)
    return json.loads(path.read_text()) if path.is_file() else default

def write_json(path:Path,value):
    safe(path);path.parent.mkdir(parents=True,exist_ok=True)
    stage=path.with_name(path.name+'.tmp');safe(stage);stage.write_text(json.dumps(value,indent=2));stage.replace(path)

def cli(home:Path,*args:str,check=True):
    env={**os.environ,'HERMES_HOME':str(home),'HERMES_PROFILE':'default'}
    env.pop('PYTHONPATH',None)
    # A root HERMES_HOME alone still follows Hermes' saved active_profile.
    # Pin root operations explicitly; named-profile homes and explicit -p calls keep their scope.
    scoped_args=list(args)
    if home.parent.name!='profiles' and not any(a in ('-p','--profile') or a.startswith('--profile=') for a in scoped_args):
        scoped_args=['-p','default',*scoped_args]
    try:
        result=subprocess.run([HERMES,*scoped_args],env=env,capture_output=True,text=True,timeout=180)
    except subprocess.TimeoutExpired as exc:
        detail=exc.stderr.decode(errors='replace') if isinstance(exc.stderr,bytes) else (exc.stderr or '')
        raise RuntimeError('Hermes command timed out: '+' '.join(args[:4])+'\n'+detail[-4000:]) from exc
    if check and result.returncode:raise RuntimeError('Hermes command failed: '+' '.join(args[:4])+'\n'+result.stderr[-1500:]+result.stdout[-1500:])
    return result

def config(home:Path,key:str):
    output=cli(home,'config','get',key,'--json',check=False)
    if output.returncode:return None
    try:return json.loads(output.stdout.strip())
    except ValueError:raise RuntimeError('Hermes returned invalid JSON for '+key)

def save_backend_enabled(home:Path):
    # Persist through the public config CLI without a live gateway reload.
    # This plugin declares no privileged capabilities or core-tool overrides.
    enabled=config(home,'plugins.enabled')or[];disabled=config(home,'plugins.disabled')or[]
    if not isinstance(enabled,list)or not isinstance(disabled,list):raise RuntimeError('Invalid plugin activation lists.')
    if ID not in enabled:enabled.append(ID)
    disabled=[name for name in disabled if name!=ID]
    cli(home,'config','set','plugins.enabled',json.dumps(enabled))
    cli(home,'config','set','plugins.disabled',json.dumps(disabled))
    if ID not in(config(home,'plugins.enabled')or[])or ID in(config(home,'plugins.disabled')or[]):raise RuntimeError('Backend activation read-back failed.')

def exclusive(path:Path,data:bytes):
    safe(path);path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with path.open('xb')as stream:stream.write(data)
    except FileExistsError:
        if not path.is_file():raise RuntimeError('File/directory collision: '+str(path))

def fill_missing(source:Path,target:Path):
    for src in sorted(source.rglob('*')):
        if src.is_symlink():raise RuntimeError('Bundle symlink refused')
        rel=src.relative_to(source)
        # Native presentation defaults are first-install-only; absence may be a deliberate user choice.
        if rel.parts[0]in('profile.yaml','assets'):continue
        if src.is_file():exclusive(target/rel,src.read_bytes())

def desktop_data_default():
    if sys.platform=='darwin':return Path.home()/'Library/Application Support/Hermes'
    if sys.platform.startswith('linux'):return Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'Hermes'
    if sys.platform=='win32':return Path(os.environ.get('APPDATA')or str(Path.home()/'AppData/Roaming'))/'Hermes'
    raise RuntimeError('Unrecognized platform; set --desktop-data-dir explicitly.')

def story_binding(edition:str,profile:str)->dict:
    if edition not in('full','lite'):raise ValueError('Invalid edition.')
    if edition=='full'and(not isinstance(profile,str)or not re.fullmatch(r'[a-z][a-z0-9-]{0,47}',profile)or profile=='default'):raise ValueError('Invalid project owner profile.')
    return {'owner':ID,'schema':1,'edition':edition,'profile':profile if edition=='full'else None}

def root_default():
    value=Path(os.environ.get('HERMES_HOME',str(Path.home()/'.hermes'))).expanduser()
    return value.parent.parent if value.parent.name=='profiles' else value

def code_metadata(target:Path)->dict:
    meta=read_json(target/CODE_MARKER,{})
    if not isinstance(meta,dict):raise RuntimeError('Invalid code ownership metadata.')
    if target.parent.name=='desktop-plugins':
        native=read_json(target/'.hermes-package.json',{})
        if native:
            package=target.parent.parent/'plugins'/ID
            if native.get('package')!=ID or native.get('source')!=str(package/'desktop'):
                raise RuntimeError('Desktop package linkage collision: '+str(target))
            owner=read_json(package/CODE_MARKER,{})
            if not isinstance(owner,dict) or owner.get('owner')!=ID:raise RuntimeError('Desktop source is not owned by this installer.')
            return owner
    return meta

def ensure_owned_code(target:Path,edition:str,replace_lite:bool):
    safe(target)
    if ID in read_json(target.parent/'.install-metadata.json',{}):
        raise RuntimeError('Hermes-managed installation tracking exists. Remove its code through the original Hermes channel, preserving its data, before using this independent installer. No provenance records were changed.')
    if not target.exists():return
    if not target.is_dir():raise RuntimeError('Plugin directory collision: '+str(target))
    meta=code_metadata(target)
    if meta.get('owner')==ID:
        if meta.get('edition')=='full' and edition=='lite':raise RuntimeError('Refusing to downgrade Full to Lite.')
        if meta.get('edition')=='lite' and edition=='full' and not replace_lite:raise RuntimeError('Use --replace-lite to authorize the switch to Full updates.')
        return
    manifest=target/'plugin.yaml';entry=target/'plugin.js'
    recognized=(manifest.is_file()and re.search(r'^name:\s*[\'\"]?hearthside-just-you-and-me[\'\"]?\s*$',manifest.read_text(),re.M))or(entry.is_file()and re.search(r"(?:const ID = ['\"]hearthside-just-you-and-me|id:\s*['\"]hearthside-just-you-and-me)",entry.read_text()))
    if not(recognized and replace_lite):raise RuntimeError('Existing unmanaged installation; inspect it and use --replace-lite only for a deliberate Hearthside channel migration: '+str(target))

def ensure_no_legacy_install(home:Path,data:Path,profile:Path):
    # A new ID is a different storage namespace. Never silently start over or copy private state.
    legacy='hearthside-nori'
    candidates=[home/'plugins'/legacy,home/'desktop-plugins'/legacy,home/'plugin-data'/legacy,
                profile/'plugins'/legacy,profile/'plugin-data'/legacy,data/'plugin-files/data'/legacy]
    found=[str(safe(path))for path in candidates if path.exists()]
    markers=[home/'hearthside-install.json',home/'hearthside-last-removal.json',profile/PROFILE_MARKER,
             profile/'workspace/Hearthside'/PROJECT_MARKER]
    for path in markers:
        if read_json(path,{}).get('owner')==legacy:found.append(str(path))
    if found:
        raise RuntimeError('A previous identifier/legacy installation was found. Stop and plan an explicit backed-up migration; this installer does not rename, delete or import its private state automatically. Locations: '+'; '.join(found))

def project_preflight(home:Path):
    project=home/'workspace/Hearthside';safe(project)
    marker=read_json(project/PROJECT_MARKER)
    if project.exists()and marker is None and any(project.iterdir()):raise RuntimeError('Unowned Hearthside project directory.')
    if marker is not None and(marker.get('owner')!=ID or marker.get('schema')!=1):raise RuntimeError('Hearthside project ownership collision.')
    for name in ('Nori.md','Room.md','Outfits.md','Journal.md','AGENTS.md'):safe(project/name)
    existing=cli(home,'project','show','hearthside',check=False)
    if existing.returncode==0 and str(project)not in re.sub(r'\x1b\[[0-9;]*m','',existing.stdout):raise RuntimeError('Project slug hearthside already belongs to another folder.')

def configure_project(home:Path,fresh:bool):
    project_preflight(home);project=home/'workspace/Hearthside';project.mkdir(parents=True,exist_ok=True)
    exclusive(project/PROJECT_MARKER,json.dumps({'owner':ID,'schema':1,'project_slug':'hearthside'}).encode())
    for name in ('Nori.md','Room.md','Outfits.md','Journal.md'):exclusive(project/name,(BUNDLE/'full/project-templates'/name).read_bytes())
    exclusive(project/'AGENTS.md',GUIDANCE.encode())
    existing=cli(home,'project','show','hearthside',check=False)
    if existing.returncode:
        args=['project','create','Hearthside',str(project),'--slug','hearthside']
        if fresh:args.append('--use')
        cli(home,*args)
    if fresh and config(home,'terminal.cwd')in(None,'','auto','.',str(home/'workspace')):cli(home,'config','set','terminal.cwd',str(project))
    shown=cli(home,'project','show','hearthside').stdout
    if str(project)not in re.sub(r'\x1b\[[0-9;]*m','',shown):raise RuntimeError('Project registration read-back failed.')
    return project

def install(args):
    home=safe(args.hermes_home or root_default());data=safe(args.desktop_data_dir or desktop_data_default())
    current=read_json(home/'hearthside-install.json',{})
    if args.profile is None:args.profile=current.get('profile')if current.get('owner')==ID and current.get('profile')else'nori'
    profile=home/'profiles'/args.profile
    native_root=Path.home()/'.hermes'
    if home!=native_root and native_root in home.parents:raise RuntimeError('Use the native Hermes root, not a subdirectory inside it.')
    if not home.is_dir():raise RuntimeError('An existing Hermes home is required. Install Hermes first: https://hermes-agent.nousresearch.com/docs/quickstart/')
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,47}',args.profile)or args.profile=='default':raise RuntimeError('A dedicated non-default profile name is required.')
    ensure_no_legacy_install(home,data,profile)
    package=BUNDLE/args.edition/ID
    if not(package/'plugin.yaml').is_file()or not(package/'desktop/plugin.js').is_file():raise RuntimeError('The selected package is incomplete.')
    version=cli(home,'--version').stdout
    parsed=re.search(r'v(\d+)\.(\d+)\.(\d+)',version)
    if not parsed or tuple(map(int,parsed.groups()))<(0,22,5):raise RuntimeError('This bundle requires Hermes >=0.22.5 and the matching Desktop SDK. See INSTALL.md.')
    targets=[home/'plugins'/ID,home/'desktop-plugins'/ID]
    if args.edition=='full':targets.append(profile/'plugins'/ID)
    for target in targets:ensure_owned_code(target,args.edition,args.replace_lite)
    fresh_profile=args.edition=='full'and not profile.exists()
    if args.edition=='full'and not fresh_profile:
        if read_json(profile/PROFILE_MARKER,{}).get('owner')!=ID:raise RuntimeError('The selected profile already belongs to another setup; choose --profile with a new name. No files were changed.')
        project_preflight(profile)
        for src in (BUNDLE/'full/profile').rglob('*'):
            if src.is_file():safe(profile/src.relative_to(BUNDLE/'full/profile'))
    resources=read_json(package/'runtime-resources.json',[])
    sdk=data/'plugin-files/data'/ID
    binding=story_binding(args.edition,args.profile)
    binding_path=safe(sdk/STORY_BINDING_FILE)
    prior_binding=read_json(binding_path)
    if prior_binding is not None and(not isinstance(prior_binding,dict)or prior_binding.get('owner')!=ID):raise RuntimeError('Unowned project binding; no files were changed.')
    for rel in resources:
        if Path(rel).is_absolute()or '..'in Path(rel).parts:raise RuntimeError('Invalid resource path.')
        source_asset=safe(package/'desktop'/rel);destination_asset=safe(sdk/rel)
        if not source_asset.is_file():raise RuntimeError('Missing packaged resource: '+rel)
        if destination_asset.exists()and not destination_asset.is_file():raise RuntimeError('Resource destination collides with a directory: '+rel)
    print('Hearthside '+args.edition+' — installation scope:')
    for target in targets:print('  '+str(target))
    print('  '+str(sdk)+' (packaged images and project-owner metadata)')
    if args.edition=='full':print('  '+str(profile)+' (dedicated Bot, skills and project; existing files preserved)')
    print('This bundle is installed and updated locally. Review INSTALL.md and THIRD_PARTY_NOTICES.md.')
    if not args.yes:
        if not sys.stdin.isatty():raise RuntimeError('Interactive confirmation required; use --yes only after reviewing this scope.')
        if input('Install these Hearthside components? [y/N] ').strip().lower()not in('y','yes'):return {'status':'cancelled'}
    backup=home/'backups/hearthside'/uuid.uuid4().hex;backup.mkdir(parents=True)
    previous=[];written=[]
    # Full profile installation is native, fresh-only and never clones credentials.
    if fresh_profile:
        cli(home,'profile','install',str(BUNDLE/'full/profile'),'--name',args.profile,'--yes')
        if not(profile/'SOUL.md').is_file():raise RuntimeError('Native profile installation did not use the expected destination; stop and inspect before continuing.')
        write_json(profile/PROFILE_MARKER,{'owner':ID,'schema':1})
    elif args.edition=='full':fill_missing(BUNDLE/'full/profile',profile)
    homes=[home]+([profile]if args.edition=='full'else[])
    enabled_before={str(h):ID in(config(h,'plugins.enabled')or[])and ID not in(config(h,'plugins.disabled')or[])for h in homes}
    prior_receipt=read_json(home/'hearthside-install.json',{})
    try:
        if binding_path.is_file():
            binding_backup=backup/'sdk'/STORY_BINDING_FILE;binding_backup.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(binding_path,binding_backup)
        write_json(binding_path,binding)
        for i,target in enumerate(targets):
            old=backup/str(i)
            if target.exists():shutil.copytree(target,old,symlinks=True)
            previous.append({'target':str(target),'backup':str(old)if old.exists()else None})
            stage=target.with_name(target.name+'.hearthside-stage-'+uuid.uuid4().hex);stage.mkdir(parents=True)
            if target.parent.name=='desktop-plugins':shutil.copytree(package/'desktop',stage,dirs_exist_ok=True)
            else:
                for src in package.iterdir():
                    # Keep the Desktop half and bundled media in the canonical agent package.
                    # Electron adopts/materializes the matching app-level copy as one package.
                    if src.name in('README.md','IDEAS.md'):continue
                    if src.is_symlink():raise RuntimeError('Bundle symlink refused')
                    if src.is_dir():shutil.copytree(src,stage/src.name)
                    else:shutil.copyfile(src,stage/src.name)
            if target.parent.name != 'desktop-plugins':
                for rel in resources:
                    resource_target = stage / 'desktop' / rel
                    resource_target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(package / 'desktop' / rel, resource_target)
            write_json(stage/CODE_MARKER,{'owner':ID,'edition':args.edition,'version':'1.0.0','update_channel':'independent-local-installer'})
            if target.exists():shutil.rmtree(target)
            stage.replace(target);written.append(target)
        for rel in resources:
            dest=sdk/rel;dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists():copy_old=backup/'sdk'/rel;copy_old.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(dest,copy_old)
            shutil.copyfile(package/'desktop'/rel,dest)
        project=configure_project(profile,fresh_profile)if args.edition=='full'else None
        for h in homes:
            # First explicit install enables its backend; later runs respect a deliberate disable.
            previous_here=next((x for x in previous if x['target']==str(h/'plugins'/ID)),None)
            if args.enable or previous_here is None or previous_here['backup']is None or enabled_before[str(h)]:save_backend_enabled(h)
        receipt={'owner':ID,'edition':args.edition,'version':'1.0.0','profile':args.profile if args.edition=='full'else None,'project':str(project)if project else None,'desktop_data_dir':str(data),'previous':previous,'backup':str(backup),'resources':resources,'story_binding':binding,'enabled_before':enabled_before,'created_profile':fresh_profile,'active_profile_unchanged':True,'provider_and_credentials_unchanged':True}
        same_edition=prior_receipt.get('owner')==ID and prior_receipt.get('edition')==args.edition
        receipt['initial_previous']=prior_receipt.get('initial_previous',prior_receipt.get('previous',previous))if same_edition else previous
        receipt['initial_enabled_before']=prior_receipt.get('initial_enabled_before',prior_receipt.get('enabled_before',enabled_before))if same_edition else enabled_before
        receipt_path=home/'hearthside-install.json';write_json(receipt_path,receipt)
        # Exact file read-back, not just subprocess exit status.
        for target in targets:
            if code_metadata(target).get('owner')!=ID:raise RuntimeError('Install marker read-back failed.')
        if(home/'desktop-plugins'/ID/'plugin.js').read_bytes()!=(package/'desktop/plugin.js').read_bytes():raise RuntimeError('Desktop module read-back failed.')
        for rel in resources:
            if(sdk/rel).read_bytes()!=(package/'desktop'/rel).read_bytes():raise RuntimeError('Resource read-back failed: '+rel)
        if read_json(binding_path)!=binding:raise RuntimeError('Project-owner binding read-back failed.')
        result={'status':'installed','edition':args.edition,'profile':receipt['profile'],'project':receipt['project'],'resources':len(resources),'receipt':str(receipt_path),'preserved_user_data':True}
        print(json.dumps(result,indent=2));print('Open Hermes Desktop and enable Hearthside in Plugins if needed (the Desktop switch in its unified card). Then select Nori in Bots. Configure its model through Hermes if needed; no credentials were cloned.'if args.edition=='full'else'Open Hermes Desktop and enable Hearthside in Plugins if needed.')
        print('An already-running gateway may need a user-controlled restart to load a newly installed backend. This installer does not restart anything.')
        return result
    except Exception:
        # Restore code only; never purge a profile, journal or collection after a failed install.
        for item in reversed(previous):
            target=Path(item['target'])
            if target.exists()and read_json(target/CODE_MARKER,{}).get('owner')==ID:shutil.rmtree(target)
            if item['backup']:shutil.copytree(item['backup'],target,dirs_exist_ok=True,symlinks=True)
        for rel in resources:
            old=backup/'sdk'/rel
            if old.is_file():shutil.copyfile(old,sdk/rel)
        binding_backup=backup/'sdk'/STORY_BINDING_FILE
        if binding_backup.is_file():shutil.copyfile(binding_backup,binding_path)
        elif read_json(binding_path)==binding:binding_path.unlink()
        raise

def parse_args():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--edition',choices=('full','lite'),default='full')
    parser.add_argument('--profile',default=None,help='Dedicated profile name; updates retain its installed name, or use nori for a fresh install. Unrelated profiles are never adopted.')
    parser.add_argument('--hermes-home',type=Path)
    parser.add_argument('--desktop-data-dir',type=Path,help='Override the Hermes Desktop user-data directory.')
    parser.add_argument('--replace-lite',action='store_true',help='Explicitly migrate an existing Hearthside/Lite installation to the independent Full update channel, with a backup.')
    parser.add_argument('--enable',action='store_true',help='Explicitly re-enable a previously disabled backend.')
    parser.add_argument('--yes',action='store_true',help='Confirm the printed local installation scope without a prompt.')
    return parser.parse_args()

HERMES=shutil.which('hermes')
if __name__=='__main__':
    try:
        if not HERMES:raise RuntimeError('Hermes is required. Install it first: https://hermes-agent.nousresearch.com/docs/quickstart/')
        install(parse_args())
    except Exception as exc:print('Hearthside installation stopped: '+str(exc),file=sys.stderr);sys.exit(1)
