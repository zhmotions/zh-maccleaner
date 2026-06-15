<?php
/**
 * ZH Motions — license ADMIN endpoint (add / manage keys remotely).
 * Only ZH License Manager calls this (with the secret). Deploy next to keys.json:
 *   https://zhmotions.com/api/license/admin
 *
 * IMPORTANT: change $SECRET below AND the matching ADMIN_SECRET in the
 * License Manager app. Keep it private.
 */
header('Content-Type: application/json');

$SECRET = "ZHADMIN-CHANGE-ME-BEFORE-DEPLOY";     // <-- change this (and in the app)
$store  = __DIR__ . '/keys.json';

$in = $_POST + $_GET;
if (empty($in['secret']) || !hash_equals($SECRET, (string)$in['secret'])) {
    http_response_code(403);
    echo json_encode(["ok" => false, "error" => "forbidden"]); exit;
}

$keys = json_decode(@file_get_contents($store), true);
if (!is_array($keys)) { $keys = []; }

$action = $in['action'] ?? 'add';
$key    = trim($in['key'] ?? '');

switch ($action) {
    case 'add':
        if ($key === '') { echo json_encode(["ok"=>false,"error"=>"no key"]); exit; }
        $keys[$key] = [
            "app"         => $in['app'] ?? 'maccleaner',
            "plan"        => "pro",
            "active"      => true,
            "max_devices" => (int)($in['max_devices'] ?? 3),
            "devices"     => [],
            "expires"     => $in['expires'] ?? '',
            "owner"       => $in['owner'] ?? '',
            "created"     => date('Y-m-d'),
        ];
        break;
    case 'deactivate': if (isset($keys[$key])) $keys[$key]['active'] = false; break;
    case 'activate':   if (isset($keys[$key])) $keys[$key]['active'] = true;  break;
    case 'reset':      if (isset($keys[$key])) $keys[$key]['devices'] = [];    break;
    case 'delete':     unset($keys[$key]); break;
    case 'list':       echo json_encode(["ok"=>true, "keys"=>$keys]); exit;
    default:           echo json_encode(["ok"=>false,"error"=>"bad action"]); exit;
}

file_put_contents($store, json_encode($keys, JSON_PRETTY_PRINT), LOCK_EX);
echo json_encode(["ok" => true]);
