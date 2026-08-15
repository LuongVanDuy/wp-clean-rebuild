from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.request import Request, urlopen
import hashlib
import io
import secrets
import time

from .site_config import SiteConnectionProfile
from .transport import FTPTransport


ProgressCallback = Callable[[dict], None]


@dataclass(slots=True)
class DatabaseBridgeResult:
    sql_path: Path
    sha256: str
    bytes_downloaded: int
    elapsed_seconds: float
    bridge_removed: bool


def _php_bridge(token: str) -> str:
    # The bridge is intentionally narrow: one authenticated read-only dump action.
    # It parses DB credentials from a conventional wp-config.php and never loads WordPress.
    return r'''<?php
@set_time_limit(0);
@ini_set('memory_limit', '256M');
@ini_set('display_errors', '0');

const WPCLEAN_TOKEN = '__TOKEN__';

function fail_bridge($message, $status = 500) {
    http_response_code($status);
    header('Content-Type: text/plain; charset=utf-8');
    echo "WPCLEAN_ERROR: " . $message;
    exit;
}

$provided = isset($_GET['token']) ? (string) $_GET['token'] : '';
if ($provided === '' || !hash_equals(WPCLEAN_TOKEN, $provided)) {
    fail_bridge('unauthorized', 403);
}

$configPath = __DIR__ . '/wp-config.php';
if (!is_file($configPath) || !is_readable($configPath)) {
    fail_bridge('wp-config.php not readable');
}

$config = file_get_contents($configPath);
if ($config === false) {
    fail_bridge('failed to read wp-config.php');
}

function read_define($source, $name) {
    $quoted = preg_quote($name, '/');
    $pattern = '/define\s*\(\s*[\'\"]' . $quoted . '[\'\"]\s*,\s*([\'\"])(.*?)\1\s*\)\s*;/s';
    if (!preg_match($pattern, $source, $matches)) {
        return null;
    }
    return stripcslashes($matches[2]);
}

$dbName = read_define($config, 'DB_NAME');
$dbUser = read_define($config, 'DB_USER');
$dbPass = read_define($config, 'DB_PASSWORD');
$dbHost = read_define($config, 'DB_HOST');

if ($dbName === null || $dbUser === null || $dbPass === null || $dbHost === null) {
    fail_bridge('unsupported wp-config.php database credential format');
}

$host = $dbHost;
$port = 3306;
$socket = null;

if (strpos($dbHost, ':') !== false) {
    if (preg_match('/^(.+):(\d+)$/', $dbHost, $m)) {
        $host = $m[1];
        $port = (int) $m[2];
    } elseif (preg_match('/^(.+):(.+\.sock)$/', $dbHost, $m)) {
        $host = $m[1];
        $socket = $m[2];
    }
}

mysqli_report(MYSQLI_REPORT_OFF);
$db = @new mysqli($host, $dbUser, $dbPass, $dbName, $port, $socket);
if ($db->connect_errno) {
    fail_bridge('database connection failed');
}
$db->set_charset('utf8mb4');

header('Content-Type: application/sql; charset=utf-8');
header('Content-Disposition: attachment; filename="database.sql"');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');
header('X-Content-Type-Options: nosniff');

while (ob_get_level() > 0) { @ob_end_flush(); }

echo "-- WP Clean Rebuild database backup\n";
echo "-- Generated: " . gmdate('c') . "\n";
echo "SET NAMES utf8mb4;\n";
echo "SET FOREIGN_KEY_CHECKS=0;\n\n";

$tablesResult = $db->query('SHOW FULL TABLES WHERE Table_type = \'BASE TABLE\'');
if (!$tablesResult) {
    fail_bridge('failed to enumerate tables');
}

while ($tableRow = $tablesResult->fetch_row()) {
    $table = $tableRow[0];
    $escapedTable = str_replace('`', '``', $table);

    $createResult = $db->query('SHOW CREATE TABLE `' . $escapedTable . '`');
    if (!$createResult) {
        fail_bridge('failed to read table structure');
    }
    $createRow = $createResult->fetch_row();

    echo "-- Table: `" . $escapedTable . "`\n";
    echo "DROP TABLE IF EXISTS `" . $escapedTable . "`;\n";
    echo $createRow[1] . ";\n\n";
    $createResult->free();

    $dataResult = $db->query('SELECT * FROM `' . $escapedTable . '`', MYSQLI_USE_RESULT);
    if (!$dataResult) {
        fail_bridge('failed to read table data');
    }

    $fieldCount = $dataResult->field_count;
    $fields = $dataResult->fetch_fields();
    $batch = [];
    $batchSize = 100;

    while ($row = $dataResult->fetch_row()) {
        $values = [];
        for ($i = 0; $i < $fieldCount; $i++) {
            if ($row[$i] === null) {
                $values[] = 'NULL';
            } elseif (isset($fields[$i]) && $fields[$i]->type === MYSQLI_TYPE_BIT) {
                $bitValue = (string) $row[$i];
                if (preg_match('/^[0-9]+$/D', $bitValue)) {
                    $values[] = $bitValue;
                } else {
                    $values[] = '0x' . bin2hex($bitValue);
                }
            } else {
                $values[] = "'" . $db->real_escape_string($row[$i]) . "'";
            }
        }
        $batch[] = '(' . implode(',', $values) . ')';

        if (count($batch) >= $batchSize) {
            echo 'INSERT INTO `' . $escapedTable . '` VALUES ' . implode(",\n", $batch) . ";\n";
            $batch = [];
            @flush();
        }
    }

    if ($batch) {
        echo 'INSERT INTO `' . $escapedTable . '` VALUES ' . implode(",\n", $batch) . ";\n";
    }
    $dataResult->free();
    echo "\n";
    @flush();
}

$tablesResult->free();
echo "SET FOREIGN_KEY_CHECKS=1;\n";
$db->close();
'''.replace('__TOKEN__', token)


def _upload_bridge(transport: FTPTransport, remote_path: str, content: str) -> None:
    client = transport._new_client()
    try:
        payload = io.BytesIO(content.encode('utf-8'))
        client.storbinary(f'STOR {remote_path}', payload, blocksize=256 * 1024)
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def _delete_bridge(transport: FTPTransport, remote_path: str) -> bool:
    client = transport._new_client()
    try:
        client.delete(remote_path)
        return True
    except Exception:
        return False
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def export_database_via_php_bridge(
    profile: SiteConnectionProfile,
    transport: FTPTransport,
    out_path: Path,
    *,
    progress: ProgressCallback | None = None,
    timeout: float = 600.0,
) -> DatabaseBridgeResult:
    token = secrets.token_hex(32)
    bridge_name = f'.wpclean-db-{secrets.token_hex(8)}.php'
    remote_bridge = str(PurePosixPath(profile.remote_path) / bridge_name)
    bridge_url = f"{profile.web_base_url}/{bridge_name}?token={token}"
    removed = False
    started = time.monotonic()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial = out_path.with_suffix(out_path.suffix + '.part')

    if progress:
        progress({'phase': 'upload_bridge', 'remote_path': remote_bridge})

    _upload_bridge(transport, remote_bridge, _php_bridge(token))

    try:
        if progress:
            progress({'phase': 'request_dump', 'url': profile.web_base_url})

        request = Request(
            bridge_url,
            headers={
                'User-Agent': 'WP-Clean-Rebuild/0.3',
                'Accept': 'application/sql,text/plain',
                'Cache-Control': 'no-cache',
            },
            method='GET',
        )

        digest = hashlib.sha256()
        downloaded = 0
        with urlopen(request, timeout=timeout) as response:
            content_type = (response.headers.get('Content-Type') or '').lower()
            total_header = response.headers.get('Content-Length')
            total = int(total_header) if total_header and total_header.isdigit() else None

            with partial.open('wb') as fh:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    if downloaded == 0 and chunk.startswith(b'WPCLEAN_ERROR:'):
                        raise RuntimeError(chunk.decode('utf-8', errors='replace'))
                    fh.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    if progress:
                        elapsed = max(time.monotonic() - started, 0.001)
                        progress({
                            'phase': 'download',
                            'bytes_downloaded': downloaded,
                            'bytes_total': total,
                            'bytes_per_second': downloaded / elapsed,
                            'elapsed_seconds': elapsed,
                            'content_type': content_type,
                        })

        if downloaded == 0:
            raise RuntimeError('Database bridge returned an empty response.')

        # Basic sanity check before accepting the dump.
        with partial.open('rb') as fh:
            head = fh.read(256)
        if b'WP Clean Rebuild database backup' not in head:
            raise RuntimeError('Database bridge did not return the expected SQL dump header.')

        partial.replace(out_path)
        return DatabaseBridgeResult(
            sql_path=out_path,
            sha256=digest.hexdigest(),
            bytes_downloaded=downloaded,
            elapsed_seconds=time.monotonic() - started,
            bridge_removed=False,
        )
    finally:
        removed = _delete_bridge(transport, remote_bridge)
        if progress:
            progress({'phase': 'remove_bridge', 'removed': removed, 'remote_path': remote_bridge})
