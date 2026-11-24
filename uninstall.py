#!/usr/bin/env python3
"""Remove Hearthside code while preserving the Bot, stories, skills and saved data."""
from pathlib import Path
import argparse,json,shutil,sys
sys.dont_write_bytecode=True
import install as setup

def remove(args):
    home=setup.safe(args.hermes_home or setup.root_default())
    receipt_path=home/'hearthside-install.json';receipt=setup.read_json(receipt_path,{})
    if receipt.get('owner')!=setup.ID:raise RuntimeError('No owned Hearthside installation receipt was found.')
    if receipt.get('edition')not in('full','lite'):raise RuntimeError('Invalid edition in installation receipt.')
    profile=receipt.get('profile');targets=[home/'plugins'/setup.ID,home/'desktop-plugins'/setup.ID]
    if profile:
        import re
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,47}',profile)or profile=='default':raise RuntimeError('Invalid profile in installation receipt.')
        targets.append(home/'profiles'/profile/'plugins'/setup.ID)
    saved=receipt.get('initial_previous',receipt.get('previous',[]))
    allowed={str(p)for p in targets}
    for item in saved:
        if item.get('target')not in allowed:raise RuntimeError('Unexpected removal target in receipt.')
        backup=item.get('backup')
        if backup and not setup.safe(Path(backup)).is_relative_to(home/'backups/hearthside'):raise RuntimeError('Unexpected backup location.')
        if args.restore_previous and backup and not Path(backup).is_dir():raise RuntimeError('The previous-code backup is missing; removal was not started.')
    for target in targets:
        setup.safe(target)
        if target.exists()and setup.code_metadata(target).get('owner')!=setup.ID:raise RuntimeError('Refusing an unmanaged plugin directory: '+str(target))
    data=setup.safe(Path(receipt['desktop_data_dir']))/'plugin-files/data'/setup.ID
    backup=setup.safe(Path(receipt['backup']))
    if not backup.is_relative_to(home/'backups/hearthside'):raise RuntimeError('Unexpected resource backup location.')
    for rel in receipt.get('resources',[]):
        if not isinstance(rel,str)or Path(rel).is_absolute()or '..'in Path(rel).parts:raise RuntimeError('Invalid resource path in receipt.')
        setup.safe(data/rel);setup.safe(backup/'sdk'/rel)
    print('Hearthside removal scope:')
    for target in targets:print('  '+str(target))
    print('The Nori profile, SOUL, native avatar, skills, project, journals, decks and saved progress will be kept.')
    if not args.yes:
        if not sys.stdin.isatty():raise RuntimeError('Interactive confirmation required; review scope before using --yes.')
        if input('Remove Hearthside code? [y/N] ').strip().lower()not in('y','yes'):return
    homes=[home]+([home/'profiles'/profile]if profile else[])
    for scope in homes:
        enabled=setup.config(scope,'plugins.enabled')or[];disabled=setup.config(scope,'plugins.disabled')or[]
        setup.cli(scope,'config','set','plugins.enabled',json.dumps([x for x in enabled if x!=setup.ID]))
        if setup.ID not in disabled:disabled.append(setup.ID)
        setup.cli(scope,'config','set','plugins.disabled',json.dumps(disabled))
    for target in targets:
        if target.exists():shutil.rmtree(target)
    if args.restore_previous:
        for item in saved:
            if item.get('backup'):shutil.copytree(item['backup'],item['target'],symlinks=True)
        previous_enabled=receipt.get('initial_enabled_before',receipt.get('enabled_before',{}))
        for scope in homes:
            if previous_enabled.get(str(scope)):setup.save_backend_enabled(scope)
    for rel in receipt.get('resources',[]):
        target=setup.safe(data/rel);original=backup/'sdk'/rel
        packaged=setup.BUNDLE/receipt['edition']/setup.ID/'desktop'/rel
        if args.restore_previous and original.is_file():shutil.copyfile(original,target)
        elif target.is_file()and packaged.is_file()and target.read_bytes()==packaged.read_bytes():target.unlink()
    binding_path=setup.safe(data/setup.STORY_BINDING_FILE);binding_backup=backup/'sdk'/setup.STORY_BINDING_FILE
    expected_binding=receipt.get('story_binding',setup.story_binding(receipt['edition'],profile))
    if args.restore_previous and binding_backup.is_file():shutil.copyfile(binding_backup,binding_path)
    elif setup.read_json(binding_path)==expected_binding:binding_path.unlink()
    receipt['removed']=True;receipt['restored_previous']=bool(args.restore_previous)
    setup.write_json(home/'hearthside-last-removal.json',receipt);receipt_path.unlink()
    for target in targets:
        if not args.restore_previous and target.exists():raise RuntimeError('Removal read-back failed.')
    print(json.dumps({'status':'removed','restored_previous':bool(args.restore_previous),'bot_and_user_data_preserved':True},indent=2))
    print('No running gateway was restarted or reloaded. Apply the change through your normal user-controlled restart if needed.')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--hermes-home',type=Path);parser.add_argument('--restore-previous',action='store_true');parser.add_argument('--yes',action='store_true')
    try:
        if not setup.HERMES:raise RuntimeError('Hermes is required.')
        remove(parser.parse_args())
    except Exception as exc:print('Hearthside removal stopped: '+str(exc),file=sys.stderr);sys.exit(1)
