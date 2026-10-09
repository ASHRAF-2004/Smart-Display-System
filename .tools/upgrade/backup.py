"""Consistent private SQLite/media/config backup; verifies an isolated restore copy."""
import hashlib,json,shutil,sqlite3,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]

def backup(root=ROOT):
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    destination=root/'.backup'/('security-media-tv-'+stamp)
    destination.mkdir(parents=True)
    lock=sqlite3.connect(root/'.data/thebekal.db',timeout=30)
    try:
        assert lock.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        lock.execute('BEGIN IMMEDIATE')
        source=sqlite3.connect(root/'.data/thebekal.db')
        target=sqlite3.connect(destination/'thebekal.db')
        try:
            source.backup(target)
            assert target.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        finally:
            target.close()
            source.close()
        tables={row[0] for row in lock.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        media=lock.execute('SELECT storage_name,sha256 FROM task_media').fetchall() if 'task_media' in tables else []
        (destination/'media').mkdir()
        for name,expected in media:
            assert Path(name).name==name
            source=root/'.data/media'/name
            shutil.copy2(source,destination/'media'/name)
            assert hashlib.sha256((destination/'media'/name).read_bytes()).hexdigest()==expected
        for name in ['app.py','security.py','upgrade.js','upgrade.css','server.ps1','DEPLOYMENT.md','youruni_tv_task_management_system update 17 Aug.html']:
            if (root/name).exists():shutil.copy2(root/name,destination/name)
        if (root/'.data/auth-config.json').exists():shutil.copy2(root/'.data/auth-config.json',destination/'auth-config.json')
        config=root/'.tools/cloudflared/config.yml'
        if config.exists():shutil.copy2(config,destination/'cloudflared-config.yml')
        credentials=root/'.tools/cloudflared/credentials'
        if credentials.exists():shutil.copytree(credentials,destination/'cloudflared-credentials')
    finally:
        lock.rollback();lock.close()
    # Backup itself is an isolated restore target: verify references and hashes again.
    restored=sqlite3.connect(destination/'thebekal.db')
    try:
        assert restored.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        if 'task_media' in tables:
            for name,expected in restored.execute('SELECT storage_name,sha256 FROM task_media'):
                assert hashlib.sha256((destination/'media'/name).read_bytes()).hexdigest()==expected
    finally:
        restored.close()
    manifest={'createdAt':stamp,'integrity':'ok','mediaCount':len(media),'files':{str(p.relative_to(destination)):hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.rglob('*') if p.is_file()}}
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return destination

if __name__=='__main__':print(backup())
