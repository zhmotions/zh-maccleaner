<?php
/**
 * Force-download the app with correct headers.
 * Hostinger serves .dmg as text/plain + nosniff, so the browser won't
 * download it directly. This streams it as a proper attachment.
 *  URL:  https://zhmotions.com/maccleaner/download
 */
$file = __DIR__ . '/ZH-MacCleaner.dmg';
$name = 'ZH-MacCleaner.dmg';

if (!is_file($file)) { http_response_code(404); echo 'Not found'; exit; }

header('Content-Type: application/octet-stream');
header('Content-Disposition: attachment; filename="' . $name . '"');
header('Content-Length: ' . filesize($file));
header('X-Content-Type-Options: nosniff');
header('Cache-Control: public, max-age=600');
readfile($file);
exit;
