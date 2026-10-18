$ErrorActionPreference = 'Stop'
$v = Get-Content -Raw C:\Bots\negative-monitor\power-before.json | ConvertFrom-Json
powercfg /setacvalueindex $v.scheme SUB_SLEEP STANDBYIDLE $v.ac_sleep_seconds
powercfg /setacvalueindex $v.scheme SUB_SLEEP HIBERNATEIDLE $v.ac_hibernate_seconds
powercfg /setacvalueindex $v.scheme SUB_BUTTONS LIDACTION $v.ac_lid_action
powercfg /setactive $v.scheme
