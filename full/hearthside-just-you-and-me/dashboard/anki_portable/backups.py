"""Private online SQLite snapshots and explicitly confirmed transactional restores.

The collection embeds its media: snapshots include them. Retention follows the
observed calendar-day/week/month policy, independently implemented here.
"""
from contextlib import contextmanager,closing
from datetime import datetime
from pathlib import Path
import hashlib,json,os,re,sqlite3,time,uuid
from .errors import CollectionError

NAME=re.compile(r'^(memoria|safety)-(\d{10})-([0-9a-f]{32})\.sqlite3$')


def retained(entries,prefs,now):
    limits=[prefs['backup_daily'],prefs['backup_weekly'],prefs['backup_monthly']]
    last=None;keep=set();yesterday=datetime.fromtimestamp(now).date().toordinal()-1
    for item in sorted(entries,key=lambda x:(x['created_at'],x['name']),reverse=True):
        day=datetime.fromtimestamp(item['created_at']).date()
        periods=(day.toordinal(),(day.toordinal()-1)//7,day.year*12+day.month)
        stage=next((i for i,n in enumerate(limits) if n),None)
        recent=periods[0]>=yesterday
        if recent or (stage is not None and (last is None or periods[stage]<last[stage])):
            keep.add(item['name']);last=periods
            if not recent and stage is not None:limits[stage]-=1
    return keep


def _tables(db,schema='main'):
    return [r[0] for r in db.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _identifier(name):return '"'+name.replace('"','""')+'"'


def _token(db):
    digest=hashlib.sha256()
    for name in _tables(db):
        digest.update(name.encode())
        for row in db.execute('SELECT * FROM main.'+_identifier(name)+' ORDER BY rowid'):
            for value in row:
                raw=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode()
                digest.update(type(value).__name__.encode()+b':'+str(len(raw)).encode()+b':');digest.update(raw)
    return digest.hexdigest()


class BackupsMixin:
    @property
    def backup_directory(self):return self.path.parent/'backups'

    def _backup_entries(self):
        folder=self.backup_directory
        if folder.is_symlink():raise CollectionError('La carpeta de copias no puede ser un enlace simbólico.')
        if not folder.exists():return []
        return sorted([{'name':p.name,'created_at':int(NAME.fullmatch(p.name)[2]),'bytes':p.stat().st_size,'safety':p.name.startswith('safety-')}
                       for p in folder.iterdir() if NAME.fullmatch(p.name) and not p.is_symlink() and p.is_file()],key=lambda r:(r['created_at'],r['name']),reverse=True)

    @contextmanager
    def _backup_lock(self):
        folder=self.backup_directory
        if folder.is_symlink():raise CollectionError('La carpeta de copias no puede ser un enlace simbólico.')
        folder.mkdir(mode=0o700,parents=True,exist_ok=True);folder.chmod(0o700)
        lock=folder/'.backup-lock.sqlite3'
        if lock.is_symlink():raise CollectionError('El bloqueo de copias no es un archivo seguro.')
        with closing(sqlite3.connect(str(lock),timeout=60)) as guard:
            lock.chmod(0o600)
            guard.execute('CREATE TABLE IF NOT EXISTS backup_lock(id INTEGER PRIMARY KEY)')
            guard.execute('BEGIN IMMEDIATE')
            try:yield
            finally:guard.rollback()

    def backup_status(self):
        with self._study_connection() as db:
            db.execute('BEGIN');token=_token(db)
        return {'backups':self._backup_entries(),'state_token':token}

    def create_backup(self,force=False,now=None,safety=False):
        at=self._now(now);prefs=self.full_preferences()
        if not force and not any(prefs[k] for k in ('backup_daily','backup_weekly','backup_monthly')):return None
        with self._backup_lock():
            entries=self._backup_entries()
            if not force and any(0<=at-e['created_at']<prefs['backup_interval_minutes']*60 for e in entries):return None
            name=('safety' if safety else 'memoria')+'-'+str(at).zfill(10)+'-'+uuid.uuid4().hex+'.sqlite3'
            target=self.backup_directory/name;temp=self.backup_directory/('.'+uuid.uuid4().hex+'.pending')
            deadline=time.monotonic()+60
            def progress(status,remaining,total):
                if time.monotonic()>deadline:raise CollectionError('La colección está ocupada; no se pudo completar la copia.')
            try:
                fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
                with closing(sqlite3.connect(str(temp))) as destination:
                    with self._study_connection() as source:source.backup(destination,pages=256,progress=progress,sleep=.05)
                    if destination.execute('PRAGMA quick_check').fetchone()[0]!='ok' or destination.execute('PRAGMA foreign_key_check').fetchone():raise CollectionError('La copia no superó la comprobación de integridad.')
                with temp.open('rb+') as handle:os.fsync(handle.fileno())
                os.replace(temp,target)
                # A pre-restore safety copy must not prune the snapshot about to be restored.
                if not safety:
                    current=[e for e in self._backup_entries() if not e['safety']]
                    keep=retained(current,prefs,at)
                    for entry in current:
                        if entry['name'] not in keep:(self.backup_directory/entry['name']).unlink()
                return {'name':name,'created_at':at,'bytes':target.stat().st_size,'safety':safety}
            finally:
                temp.unlink(missing_ok=True)

    def restore_backup(self,name,expected_state,confirm=False):
        if confirm is not True:raise CollectionError('Confirmá expresamente que querés reemplazar la colección.')
        if not isinstance(name,str) or not NAME.fullmatch(name):raise CollectionError('La copia elegida no es válida.')
        source=self.backup_directory/name
        if source.is_symlink() or not source.is_file():raise CollectionError('La copia elegida no existe o no es un archivo seguro.')
        if expected_state!=self.backup_status()['state_token']:raise CollectionError('La colección cambió. Actualizá la lista antes de restaurar.')
        safety=self.create_backup(force=True,safety=True)
        with self._backup_lock():
            db=sqlite3.connect(self.path.resolve().as_uri(),uri=True,timeout=10);db.row_factory=sqlite3.Row
            try:
                db.execute('PRAGMA trusted_schema=OFF');db.execute('PRAGMA foreign_keys=OFF')
                db.execute('ATTACH DATABASE ? AS restore_source',(source.resolve().as_uri()+'?mode=ro&immutable=1',))
                if db.execute('PRAGMA restore_source.quick_check').fetchone()[0]!='ok':raise CollectionError('La copia está dañada.')
                tables=_tables(db)
                source_tables=_tables(db,'restore_source')
                missing=set(tables)-set(source_tables)
                # Older backups predate the archive; restoring them clears it.
                if set(source_tables)-set(tables) or missing not in (set(), {'deleted_deck_review_activity'}):
                    raise CollectionError('La estructura de la copia no es compatible con esta versión.')
                for table in source_tables:
                    ident=_identifier(table)
                    if [tuple(r) for r in db.execute('PRAGMA main.table_info('+ident+')')]!=[tuple(r) for r in db.execute('PRAGMA restore_source.table_info('+ident+')')]:raise CollectionError('La estructura de la copia no es compatible con esta versión.')
                db.execute('BEGIN IMMEDIATE')
                if expected_state!=_token(db):raise CollectionError('La colección cambió mientras preparábamos la restauración. No se reemplazó nada.')
                has_sequence=db.execute("SELECT 1 FROM main.sqlite_master WHERE name='sqlite_sequence'").fetchone()
                previous_sequences=dict(db.execute('SELECT name,seq FROM main.sqlite_sequence')) if has_sequence else {}
                for table in tables:db.execute('DELETE FROM main.'+_identifier(table))
                for table in source_tables:db.execute('INSERT INTO main.'+_identifier(table)+' SELECT * FROM restore_source.'+_identifier(table)+' ORDER BY rowid')
                if has_sequence:
                    # External reward/event consumers must never observe reused IDs.
                    restored_sequences=dict(db.execute('SELECT name,seq FROM restore_source.sqlite_sequence'))
                    db.execute('DELETE FROM main.sqlite_sequence')
                    db.executemany('INSERT INTO main.sqlite_sequence(name,seq) VALUES(?,?)',[(name,max(previous_sequences.get(name,0),restored_sequences.get(name,0))) for name in sorted(set(previous_sequences)|set(restored_sequences)) if name in tables])
                if db.execute('PRAGMA main.foreign_key_check').fetchone():raise CollectionError('La copia contiene referencias inconsistentes. No se reemplazó nada.')
                db.commit()
            except sqlite3.DatabaseError as err:
                db.rollback();raise CollectionError('No se pudo restaurar la copia. La colección actual se conservó. Detalle: '+str(err)) from err
            except Exception:
                db.rollback();raise
            finally:db.close()
        return {'restored':name,'safety_backup':safety['name']}
