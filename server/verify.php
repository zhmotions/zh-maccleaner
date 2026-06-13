<?php
/**
 * ZH Motions — license verify endpoint (self-hosted)
 * Deploy to:  https://www.zhmotions.com/api/license/verify.php
 * Keep keys.json in the SAME folder (chmod 600, not web-readable ideally).
 *
 * App POSTs: key, app, device, v
 * Returns JSON: {"valid":true|false, "plan":"pro", "message":"..."}
 */
header('Content-Type: application/json');
header('Access-Control-Allow-Origin: *');

// Read params from POST, GET, or a raw urlencoded/JSON body (robust across hosts)
$in = $_POST + $_GET;
if (empty($in['key'])) {
    $raw = file_get_contents('php://input');
    if ($raw) {
        $j = json_decode($raw, true);
        if (is_array($j)) { $in = array_merge($in, $j); }
        else { parse_str($raw, $p); if (is_array($p)) { $in = array_merge($in, $p); } }
    }
}
$key    = trim($in['key']    ?? '');
$app    = trim($in['app']    ?? '');
$device = trim($in['device'] ?? '');

if ($key === '' || $device === '') {
    echo json_encode(["valid"=>false, "message"=>"Missing key or device."]); exit;
}

$store = __DIR__ . '/keys.json';
$keys  = json_decode(@file_get_contents($store), true);
if (!is_array($keys)) { $keys = []; }

if (!isset($keys[$key])) {
    echo json_encode(["valid"=>false, "message"=>"License key not found."]); exit;
}

$k = $keys[$key];

// optional: restrict a key to one app ("maccleaner" / "downloader")
if (!empty($k['app']) && $app !== '' && $k['app'] !== $app) {
    echo json_encode(["valid"=>false, "message"=>"Key is for a different product."]); exit;
}
if (empty($k['active'])) {
    echo json_encode(["valid"=>false, "message"=>"This license has been deactivated."]); exit;
}
if (!empty($k['expires']) && time() > strtotime($k['expires'])) {
    echo json_encode(["valid"=>false, "message"=>"This license has expired."]); exit;
}

// device binding (per-seat limit)
$devices = isset($k['devices']) && is_array($k['devices']) ? $k['devices'] : [];
$max     = isset($k['max_devices']) ? (int)$k['max_devices'] : 3;

if (!in_array($device, $devices, true)) {
    if (count($devices) >= $max) {
        echo json_encode(["valid"=>false, "message"=>"Device limit reached for this license."]); exit;
    }
    $devices[] = $device;
    $keys[$key]['devices']   = $devices;
    $keys[$key]['last_seen'] = date('c');
    file_put_contents($store, json_encode($keys, JSON_PRETTY_PRINT), LOCK_EX);
}

echo json_encode([
    "valid"   => true,
    "plan"    => $k['plan'] ?? 'pro',
    "message" => "OK",
]);
