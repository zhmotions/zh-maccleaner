<?php
/**
 * Force-download the app with correct headers.
 * Hostinger serves .dmg/.pkg as text/plain + nosniff, so the browser won't
 * download it directly. This streams it as a proper attachment.
 *   https://zhmotions.com/maccleaner/download         → .pkg (easy installer)
 *   https://zhmotions.com/maccleaner/download?type=dmg → .dmg (drag install)
 */
$type = (isset($_GET['type']) && $_GET['type'] === 'dmg') ? 'dmg' : 'pkg';

if ($type === 'dmg') { $file = __DIR__ . '/ZH-MacCleaner.dmg'; $name = 'ZH-MacCleaner.dmg'; }
else                 { $file = __DIR__ . '/ZH-MacCleaner.pkg'; $name = 'ZH-MacCleaner.pkg'; }

// Fall back to the .dmg if the .pkg hasn't been uploaded yet.
if (!is_file($file) && $type === 'pkg' && is_file(__DIR__ . '/ZH-MacCleaner.dmg')) {
    $file = __DIR__ . '/ZH-MacCleaner.dmg'; $name = 'ZH-MacCleaner.dmg';
}
if (!is_file($file)) { http_response_code(404); echo 'Not found'; exit; }

header('Content-Type: application/octet-stream');
header('Content-Disposition: attachment; filename="' . $name . '"');
header('Content-Length: ' . filesize($file));
header('X-Content-Type-Options: nosniff');
header('Cache-Control: public, max-age=600');
readfile($file);
exit;
