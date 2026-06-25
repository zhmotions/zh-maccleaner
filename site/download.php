<?php
/**
 * Force-download the app with correct headers.
 * Hostinger serves .dmg/.pkg as text/plain, so the browser won't download them
 * directly. This streams the file as a proper attachment.
 *   https://zhmotions.com/maccleaner/download         → .pkg (easy installer)
 *   https://zhmotions.com/maccleaner/download?type=dmg → .dmg (drag install)
 *
 * Accepts plain (ZH-MacCleaner.pkg) AND versioned (ZH-MacCleaner-1.0.3.pkg) names —
 * picks the newest. Streams in chunks so large files never truncate or exhaust memory.
 */
$type = (isset($_GET['type']) && $_GET['type'] === 'dmg') ? 'dmg' : 'pkg';

function pick(string $ext): string {
    $hits = glob(__DIR__ . '/ZH-MacCleaner*.' . $ext) ?: [];
    if (!$hits) return '';
    usort($hits, fn($a, $b) => filemtime($b) <=> filemtime($a));
    return $hits[0];
}

$file = pick($type);
if ($file === '' && $type === 'pkg') $file = pick('dmg');
if ($file === '' || !is_file($file)) { http_response_code(404); echo 'Not found'; exit; }

// Kill any output buffering so we stream straight to the socket (no truncation / memory blowup).
while (ob_get_level() > 0) { ob_end_clean(); }
@set_time_limit(0);

header('Content-Type: application/octet-stream');
header('Content-Disposition: attachment; filename="' . basename($file) . '"');
header('Content-Length: ' . filesize($file));
header('Accept-Ranges: bytes');
header('X-Content-Type-Options: nosniff');
header('Cache-Control: public, max-age=600');

$fp = fopen($file, 'rb');
if ($fp === false) { http_response_code(500); exit; }
while (!feof($fp)) {
    echo fread($fp, 1024 * 256);   // 256 KB chunks
    flush();
}
fclose($fp);
exit;
