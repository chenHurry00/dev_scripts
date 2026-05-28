"""
服务端本机自动维护：备份、备份配额、日志清理和状态查询。
"""
import os
import shutil
import socket
import sqlite3
import tarfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path


_started = False


def _now():
    return datetime.now().isoformat(timespec='seconds')


def _dir_size(path):
    total = 0
    path = Path(path)
    if not path.exists():
        return 0

    for item in path.rglob('*'):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _backup_files(backup_dir):
    backup_dir = Path(backup_dir)
    if not backup_dir.exists():
        return []
    return sorted(
        [p for p in backup_dir.glob('*.tar.gz') if p.is_file()],
        key=lambda p: p.stat().st_mtime
    )


def _record_backup(db_path, status, backup_path=None, size_bytes=0, error_message=None):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO backup_records (
            server_name, backup_path, size_bytes, status, error_message
        ) VALUES (?, ?, ?, ?, ?)
    """, (socket.gethostname(), str(backup_path or ''), size_bytes, status, error_message))
    conn.commit()
    conn.close()


def _record_cleanup(db_path, started_at, status, deleted_db_rows=0, deleted_files=0,
                    freed_bytes=0, backup_path=None, error_message=None):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO cleanup_logs (
            started_at, finished_at, status, deleted_db_rows, deleted_files,
            freed_bytes, backup_path, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        started_at, _now(), status, deleted_db_rows, deleted_files,
        freed_bytes, str(backup_path or ''), error_message
    ))
    conn.commit()
    conn.close()


def storage_summary(app_config):
    """返回当前服务端存储状态。"""
    db_path = Path(app_config['AUDIT_DB'])
    logs_dir = Path(app_config['LOG_DIR'])
    backup_dir = Path(app_config['BACKUP_DIR'])
    usage = shutil.disk_usage(str(db_path.parent))

    return {
        'data_db_bytes': db_path.stat().st_size if db_path.exists() else 0,
        'logs_bytes': _dir_size(logs_dir),
        'backup_bytes': _dir_size(backup_dir),
        'disk_total_bytes': usage.total,
        'disk_used_bytes': usage.used,
        'disk_free_bytes': usage.free,
        'backup_max_bytes': app_config['BACKUP_MAX_BYTES'],
        'min_free_bytes': app_config['MIN_FREE_BYTES'],
    }


def backup_status(app_config):
    """返回最近备份和备份目录状态。"""
    files = _backup_files(app_config['BACKUP_DIR'])
    latest = files[-1] if files else None
    return {
        'backup_count': len(files),
        'backup_bytes': _dir_size(app_config['BACKUP_DIR']),
        'latest_backup': str(latest) if latest else '',
        'latest_backup_size': latest.stat().st_size if latest else 0,
        'latest_backup_time': (
            datetime.fromtimestamp(latest.stat().st_mtime).isoformat(timespec='seconds')
            if latest else ''
        ),
    }


def maintenance_status(app_config):
    """返回维护配置、最近备份和最近清理状态。"""
    db_path = app_config['AUDIT_DB']
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    last_backup = conn.execute("""
        SELECT * FROM backup_records ORDER BY created_at DESC LIMIT 1
    """).fetchone()
    last_cleanup = conn.execute("""
        SELECT * FROM cleanup_logs ORDER BY started_at DESC LIMIT 1
    """).fetchone()
    conn.close()

    return {
        'auto_maintenance_enabled': app_config['AUTO_MAINTENANCE_ENABLED'],
        'backup_interval_hours': app_config['BACKUP_INTERVAL_HOURS'],
        'cleanup_db_enabled': app_config['CLEANUP_DB_ENABLED'],
        'storage': storage_summary(app_config),
        'backups': backup_status(app_config),
        'last_backup': dict(last_backup) if last_backup else None,
        'last_cleanup': dict(last_cleanup) if last_cleanup else None,
    }


def _last_success_backup_time(db_path):
    conn = sqlite3.connect(db_path)
    row = conn.execute("""
        SELECT created_at FROM backup_records
        WHERE status IN ('success', 'success_with_quota_exceeded')
        ORDER BY created_at DESC LIMIT 1
    """).fetchone()
    conn.close()

    if not row:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except ValueError:
        return None


def _prune_old_backups(app_config, allow_delete_all=False, protected_path=None):
    backup_dir = Path(app_config['BACKUP_DIR'])
    backup_dir.mkdir(parents=True, exist_ok=True)
    protected_path = Path(protected_path).resolve() if protected_path else None

    deleted_files = 0
    freed_bytes = 0

    for tmp_file in backup_dir.glob('*.tmp'):
        try:
            freed_bytes += tmp_file.stat().st_size
            tmp_file.unlink()
            deleted_files += 1
        except OSError:
            continue

    while True:
        summary = storage_summary(app_config)
        over_quota = summary['backup_bytes'] > app_config['BACKUP_MAX_BYTES']
        low_space = summary['disk_free_bytes'] < app_config['MIN_FREE_BYTES']
        if not over_quota and not low_space:
            break

        files = _backup_files(backup_dir)
        candidates = []
        for file_path in files:
            if protected_path and file_path.resolve() == protected_path:
                continue
            candidates.append(file_path)

        if not allow_delete_all and app_config['KEEP_LAST_BACKUP'] and len(candidates) <= 1:
            break
        if not candidates:
            break

        victim = candidates[0]
        try:
            freed_bytes += victim.stat().st_size
            victim.unlink()
            deleted_files += 1
        except OSError:
            break

    return deleted_files, freed_bytes


def _create_backup(app_config):
    db_path = Path(app_config['AUDIT_DB'])
    logs_dir = Path(app_config['LOG_DIR'])
    backup_dir = Path(app_config['BACKUP_DIR'])
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    final_path = backup_dir / f"{socket.gethostname()}_{timestamp}.tar.gz"
    tmp_path = backup_dir / f"{final_path.name}.tmp"

    try:
        if tmp_path.exists():
            tmp_path.unlink()

        with tarfile.open(tmp_path, 'w:gz') as archive:
            if db_path.exists():
                archive.add(db_path, arcname='data/audit.db')
            if logs_dir.exists():
                archive.add(logs_dir, arcname='logs')

        tmp_path.rename(final_path)
        size_bytes = final_path.stat().st_size
        status = 'success'
        if size_bytes > app_config['BACKUP_MAX_BYTES']:
            status = 'success_with_quota_exceeded'
        _record_backup(app_config['AUDIT_DB'], status, final_path, size_bytes)
        return final_path, size_bytes, status, None
    except Exception as exc:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        _record_backup(app_config['AUDIT_DB'], 'failed', None, 0, str(exc))
        return None, 0, 'failed', str(exc)


def _cleanup_log_files(app_config):
    retention = {
        'audit': 180,
        'access': 30,
        'error': 90,
        'alert': 365,
    }
    deleted_files = 0
    freed_bytes = 0
    current_audit_name = f"audit_{datetime.now().strftime('%Y-%m')}.log"

    for name, days in retention.items():
        log_dir = Path(app_config['LOG_DIR']) / name
        if not log_dir.exists():
            continue
        cutoff = datetime.now() - timedelta(days=days)
        for file_path in log_dir.glob('*.log'):
            if name == 'audit' and file_path.name == current_audit_name:
                continue
            try:
                mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                if mtime >= cutoff:
                    continue
                freed_bytes += file_path.stat().st_size
                file_path.unlink()
                deleted_files += 1
            except OSError:
                continue

    return deleted_files, freed_bytes


def _recreate_audit_delete_trigger(conn):
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_audit_delete
        BEFORE DELETE ON audit_logs
        BEGIN
            SELECT RAISE(ABORT, 'Audit logs are immutable - deletion not allowed');
        END
    """)


def _cleanup_db_rows(app_config):
    if not app_config['CLEANUP_DB_ENABLED']:
        return 0

    policy = app_config.get('RETENTION_POLICY', {})
    deleted_rows = 0
    conn = sqlite3.connect(app_config['AUDIT_DB'])
    try:
        conn.execute('DROP TRIGGER IF EXISTS prevent_audit_delete')
        for risk_level, days in policy.items():
            if days < 0:
                continue
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            cursor = conn.execute("""
                DELETE FROM audit_logs
                WHERE risk_level = ? AND timestamp < ?
            """, (risk_level, cutoff))
            deleted_rows += cursor.rowcount if cursor.rowcount else 0
        _recreate_audit_delete_trigger(conn)
        conn.commit()
        db_path = Path(app_config['AUDIT_DB'])
        if deleted_rows > 0 and shutil.disk_usage(str(db_path.parent)).free > db_path.stat().st_size:
            conn.execute('VACUUM')
    finally:
        try:
            _recreate_audit_delete_trigger(conn)
            conn.commit()
        finally:
            conn.close()

    return deleted_rows


def run_maintenance_once(app_config):
    """执行一次服务端自动维护。"""
    started_at = _now()
    deleted_files = 0
    freed_bytes = 0
    deleted_db_rows = 0
    backup_path = None

    try:
        low_space_before_backup = storage_summary(app_config)['disk_free_bytes'] < app_config['MIN_FREE_BYTES']
        removed_count, removed_bytes = _prune_old_backups(
            app_config, allow_delete_all=low_space_before_backup
        )
        deleted_files += removed_count
        freed_bytes += removed_bytes

        last_backup_time = _last_success_backup_time(app_config['AUDIT_DB'])
        backup_due = (
            not last_backup_time or
            datetime.now() - last_backup_time >= timedelta(hours=app_config['BACKUP_INTERVAL_HOURS'])
        )
        low_space = storage_summary(app_config)['disk_free_bytes'] < app_config['MIN_FREE_BYTES']

        if backup_due or low_space:
            backup_path, _, status, error = _create_backup(app_config)
            if status == 'failed':
                raise RuntimeError(error or '备份失败')

        removed_count, removed_bytes = _prune_old_backups(
            app_config, allow_delete_all=False, protected_path=backup_path
        )
        deleted_files += removed_count
        freed_bytes += removed_bytes

        if storage_summary(app_config)['disk_free_bytes'] < app_config['MIN_FREE_BYTES']:
            removed_count, removed_bytes = _cleanup_log_files(app_config)
            deleted_files += removed_count
            freed_bytes += removed_bytes

        if storage_summary(app_config)['disk_free_bytes'] < app_config['MIN_FREE_BYTES']:
            deleted_db_rows = _cleanup_db_rows(app_config)

        status = 'success'
        if storage_summary(app_config)['disk_free_bytes'] < app_config['MIN_FREE_BYTES']:
            status = 'low_space_after_cleanup'

        _record_cleanup(
            app_config['AUDIT_DB'], started_at, status, deleted_db_rows,
            deleted_files, freed_bytes, backup_path
        )
        return {'status': status, 'deleted_files': deleted_files, 'deleted_db_rows': deleted_db_rows}
    except Exception as exc:
        _record_cleanup(
            app_config['AUDIT_DB'], started_at, 'failed', deleted_db_rows,
            deleted_files, freed_bytes, backup_path, str(exc)
        )
        return {'status': 'failed', 'error': str(exc)}


def start_auto_maintenance(app):
    """启动服务端本机自动维护线程。"""
    global _started
    if _started or not app.config.get('AUTO_MAINTENANCE_ENABLED'):
        return
    if app.config.get('DEBUG') and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        return

    _started = True

    def loop():
        while True:
            with app.app_context():
                run_maintenance_once(app.config)
            time.sleep(app.config['AUTO_MAINTENANCE_INTERVAL'])

    thread = threading.Thread(target=loop, name='audit-maintenance', daemon=True)
    thread.start()
