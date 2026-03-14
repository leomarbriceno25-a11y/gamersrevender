<?php
// ===== API REDEEM HYPE GAMES =====
// Funciones para validar, asociar y confirmar redención de PINs

function hypeRequest($url, $params) {
    $headers = [
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type: application/x-www-form-urlencoded; charset=UTF-8",
        "Accept: */*",
        "Referer: https://redeem.hype.games/widget",
        "Origin: https://redeem.hype.games"
    ];

    $ch = curl_init();
    curl_setopt($ch, CURLOPT_URL, $url);
    curl_setopt($ch, CURLOPT_POST, true);
    curl_setopt($ch, CURLOPT_POSTFIELDS, http_build_query($params));
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
    curl_setopt($ch, CURLOPT_TIMEOUT, 30);
    $response = curl_exec($ch);
    $error = curl_error($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    return [
        'http_code' => $httpCode,
        'response'  => $response,
        'error'     => $error,
        'json'      => json_decode($response, true)
    ];
}

// PASO 1: Validar PIN
function hypeValidarPin($pin) {
    return hypeRequest("https://redeem.hype.games/validate", [
        "Key"          => $pin,
        "CaptchaToken" => "03AFcWeA7iMhAV8eEIBUFdhuuetcYorsqTgmxJvdpVsnpQRoYGhopvxbYwyS",
        "origin"       => "widget"
    ]);
}

// PASO 2: Asociar PIN a cuenta
function hypeAsociarCuenta($pin, $gameAccountId) {
    $params = hypeParams($pin, $gameAccountId);
    return hypeRequest("https://redeem.hype.games/validate/account", $params);
}

// PASO 3: Confirmar redención
function hypeConfirmar($pin, $gameAccountId) {
    $params = hypeParams($pin, $gameAccountId);
    return hypeRequest("https://redeem.hype.games/confirm", $params);
}

// Parámetros comunes para pasos 2 y 3
function hypeParams($pin, $gameAccountId) {
    return [
        "RedeemCountryId"               => "5",
        "ProductId"                      => "2630",
        "CountryId"                      => "5",
        "CompanyName"                    => "HypeMexico",
        "Customer.CompanyName"           => "HypeMexico",
        "Key"                            => $pin,
        "CookieCardHypeInfo"             => "",
        "GameAccountId"                  => $gameAccountId,
        "privacy"                        => "on",
        "CaptchaToken"                   => "03AFcWeA7iMhAV8eEIBUFdhuuetcYorsqTgmxJvdpVsnpQRoYGhopvxbYwyS",
        "Customer.NationalityAlphaCode"  => "CL",
        "Customer.Name"                  => "Cliente GamersRev",
        "Customer.BornAt"                => "15/03/1995",
        "Customer.CountryId"             => "5",
        "RedeemSourceTypeId"             => "3",
        "QueryString"                    => "",
        "origin"                         => "widget"
    ];
}

// Función completa: ejecuta los 3 pasos y devuelve resultado
function hypeCanjearPin($pin, $gameAccountId) {
    $log = [];

    // Paso 1: Validar
    $r1 = hypeValidarPin($pin);
    $log['paso1'] = ['http' => $r1['http_code'], 'error' => $r1['error']];
    if ($r1['http_code'] != 200) {
        return ['ok' => false, 'error' => 'PIN inválido o expirado', 'log' => $log];
    }

    // Paso 2: Asociar cuenta
    $r2 = hypeAsociarCuenta($pin, $gameAccountId);
    $log['paso2'] = ['http' => $r2['http_code'], 'json' => $r2['json'], 'error' => $r2['error']];
    $username = null;
    if ($r2['json'] && isset($r2['json']['Success'])) {
        if ($r2['json']['Success'] === false) {
            return ['ok' => false, 'error' => $r2['json']['Message'] ?? 'Error al asociar cuenta', 'log' => $log];
        }
        $username = $r2['json']['Username'] ?? null;
    }

    // Paso 3: Confirmar
    $r3 = hypeConfirmar($pin, $gameAccountId);
    $log['paso3'] = ['http' => $r3['http_code'], 'error' => $r3['error']];

    // Verificar si el paso 3 fue exitoso (buscar "sucesso" o "success" en la respuesta)
    $exitoso = ($r3['http_code'] == 200 && 
               (strpos($r3['response'], 'sucesso') !== false || 
                strpos($r3['response'], 'success') !== false ||
                strpos($r3['response'], 'resgatados') !== false));

    if ($exitoso) {
        return ['ok' => true, 'username' => $username, 'log' => $log];
    } else {
        return ['ok' => false, 'error' => 'Error al confirmar redención', 'username' => $username, 'log' => $log];
    }
}
?>
